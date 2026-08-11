from __future__ import annotations

import json
import os
import re
import uuid
from hashlib import sha256
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from math_agent.knowledge.trust import KnowledgeTrustPolicy
from math_agent.search.text_retrieval import rank_rows


PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
EVENT_LOG_NAME = "events.jsonl"
CHECKPOINT_LOG_NAME = "checkpoints.jsonl"
# Compact the checkpoint log once it passes this size and superseded records
# make up enough of it that a rewrite is worth the I/O.
CHECKPOINT_COMPACT_MIN_BYTES = 2 * 1024 * 1024
CHECKPOINT_COMPACT_RATIO = 0.5
VALID_REVIEW_STATUSES = set(KnowledgeTrustPolicy.REVIEW_QUEUE)
TRUSTED_KNOWLEDGE_STATUSES = KnowledgeTrustPolicy.SOLVE_RETRIEVAL
VALID_GRAPH_EDGE_KINDS = {
    "analogy_with",
    "answers_question",
    "arises_from",
    "connects_to",
    "depends_on",
    "derived_from",
    "equivalent_to",
    "formalizes_as",
    "generalizes",
    "has_intuition",
    "introduces",
    "refines",
    "supports",
    "special_case_of",
    "uses",
    "uses_technique",
    "related_to",
    "contradicts",
    "references",
}
VALID_GRAPH_NODE_KINDS = {
    "definition",
    "theorem",
    "lemma",
    "proposition",
    "corollary",
    "exercise",
    "technique",
    "intuition",
    "paper",
    "question",
    "viewpoint",
    "topic",
    "source",
}
COMMON_MEMORY_FIELDS = [
    "source",
    "source_type",
    "source_ref",
    "source_title",
    "evidence",
    "confidence",
    "score",
    "status",
    "domain",
    "tags",
    "created_by",
    "review_note",
    "metadata",
]
FACT_MEMORY_FIELDS = [
    "statement", "why", "statement_zh", "why_zh", "formal_status", "lean_name",
    *COMMON_MEMORY_FIELDS,
]
INTUITION_MEMORY_FIELDS = ["title", "body", "title_zh", "body_zh", "kind", *COMMON_MEMORY_FIELDS]
TRICK_MEMORY_FIELDS = [
    "title",
    "body",
    "title_zh",
    "body_zh",
    "category",
    "applicability",
    "failure_mode",
    *COMMON_MEMORY_FIELDS,
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_tool_evidence(items: Any) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in list(items or [])[:50]:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {}
        for key, value in item.items():
            cap = 2000 if str(key) in ("args_preview", "output_preview") else 500
            entry[str(key)[:64]] = str(value)[:cap] if isinstance(value, str) else value
        cleaned.append(entry)
    return cleaned


def _default_root() -> Path:
    return Path(os.getenv("CONJECTA_PROJECT_STORE_DIR") or "logs/projects").resolve()


def project_store_root_for_user(user_id: str) -> Path:
    """Per-tenant local root: ``{CONJECTA_PROJECT_STORE_DIR}/{user_id}``."""
    return _default_root() / sanitize_user_id(user_id)


def sanitize_user_id(user_id: str | None) -> str:
    """Return a single safe path component for a tenant-owned runtime root."""
    raw = (user_id or "").strip()
    if not raw:
        return "anonymous"
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", raw).strip("_-") or "tenant"
    if safe != raw or len(safe) > 128:
        digest = sha256(raw.encode("utf-8")).hexdigest()[:12]
        safe = f"{safe[:115]}-{digest}"
    return safe


_STORE_CACHE: dict[str, "ProjectStore"] = {}
_STORE_CACHE_LOCK = RLock()


def project_store_for_root(root: Path | None = None) -> "ProjectStore":
    """Return the process-wide ``ProjectStore`` for one root, creating it once.

    Building a store is cheap, but its first read replays the whole event and
    checkpoint log to rebuild the in-memory index (tens of milliseconds on a
    multi-megabyte log). Callers must share one instance per root so that cost
    is paid once per process rather than once per request.
    """
    resolved = (root or _default_root()).resolve()
    key = str(resolved)
    with _STORE_CACHE_LOCK:
        store = _STORE_CACHE.get(key)
        if store is None:
            store = ProjectStore(root=resolved)
            _STORE_CACHE[key] = store
        return store


def project_store_for_user(user_id: str) -> "ProjectStore":
    return project_store_for_root(project_store_root_for_user(user_id))


def _http_exception(status_code: int, detail: str):
    from fastapi import HTTPException

    return HTTPException(status_code=status_code, detail=detail)


def validate_project_id(project_id: str) -> str:
    project_id = (project_id or "").strip()
    if not PROJECT_ID_RE.match(project_id):
        raise _http_exception(status_code=400, detail="Invalid project id.")
    return project_id


def _normalize_limit(limit: int) -> int:
    try:
        value = int(limit)
    except Exception:
        value = 200
    return max(0, min(value, 1000))


def _normalize_offset(offset: int) -> int:
    try:
        value = int(offset)
    except Exception:
        value = 0
    return max(0, value)


@dataclass
class ProjectStore:
    """Append-only JSONL store for projects, review items, and knowledge."""

    root: Path | None = None

    def __post_init__(self) -> None:
        self.root = (self.root or _default_root()).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        # Per-instance: all guarded state below belongs to this root, so one
        # tenant's index rebuild must not serialize every other tenant's reads.
        self._lock = RLock()
        self._event_index_loaded = False
        self._event_log_signature: tuple[int, int, int, int] | None = None
        self._legacy_signatures: tuple[tuple[str, tuple[int, int, int, int]], ...] = ()
        self._legacy_errors: set[str] = set()
        self._events_cache: list[dict[str, Any]] = []
        self._state_index: dict[str, dict[str, Any]] = {}
        self._checkpoint_index_loaded = False
        self._checkpoint_log_signature: tuple[int, int, int, int] | None = None
        self._checkpoints_by_session: dict[str, dict[str, Any]] = {}

    @property
    def event_log_path(self) -> Path:
        assert self.root is not None
        return self.root / EVENT_LOG_NAME

    @property
    def checkpoint_log_path(self) -> Path:
        assert self.root is not None
        return self.root / CHECKPOINT_LOG_NAME

    def write_checkpoint(self, checkpoint: dict[str, Any]) -> None:
        """Persist a solve checkpoint keyed by session_id. Overwrites prior entry for same session."""
        record = deepcopy(checkpoint)
        session_id = str(record.get("session_id") or "")
        if not session_id:
            return
        record["at"] = _now()
        text = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self._ensure_checkpoint_index()
            indexed_size = (
                self._checkpoint_log_signature[2]
                if self._checkpoint_log_signature is not None
                else 0
            )
            expected_size = indexed_size + len(text.encode("utf-8"))
            with self.checkpoint_log_path.open("a", encoding="utf-8") as fh:
                fh.write(text)
            self._checkpoints_by_session[session_id] = deepcopy(record)
            signature = self._file_signature(self.checkpoint_log_path)
            self._checkpoint_log_signature = (
                (signature[0], signature[1], expected_size, signature[3])
                if signature is not None
                else None
            )
            if expected_size >= CHECKPOINT_COMPACT_MIN_BYTES:
                self._maybe_compact_checkpoints(expected_size)

    def _maybe_compact_checkpoints(self, current_size: int) -> None:
        """Rewrite the checkpoint log keeping only the latest record per session.

        Only the newest record per session is ever read back, but every ReAct
        step appends a full ~21KB trace snapshot, so the log grows without
        bound (4.4MB / 214 records in production, of which 77 were live).
        Compaction runs only when superseded records dominate, so a log of
        mostly-distinct sessions is left alone. Callers hold ``self._lock``.
        """
        live = self._checkpoints_by_session
        if not live:
            return
        projected = sum(
            len(json.dumps(rec, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) + 1
            for rec in live.values()
        )
        if projected > current_size * CHECKPOINT_COMPACT_RATIO:
            return
        tmp_path = self.checkpoint_log_path.with_suffix(".jsonl.compact")
        try:
            with tmp_path.open("w", encoding="utf-8") as fh:
                for record in live.values():
                    fh.write(
                        json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
                    )
            os.replace(tmp_path, self.checkpoint_log_path)
        except OSError:
            # Compaction is an optimization: a failure must never lose the
            # durable log or fail the solve that triggered it.
            tmp_path.unlink(missing_ok=True)
            return
        self._checkpoint_log_signature = self._file_signature(self.checkpoint_log_path)

    def get_checkpoint(self, session_id: str) -> dict[str, Any] | None:
        """Return the most recent checkpoint for session_id, or None."""
        with self._lock:
            self._ensure_checkpoint_index()
            result = self._checkpoints_by_session.get(session_id)
            return deepcopy(result) if result is not None else None

    def list_checkpoints(self) -> list[dict[str, Any]]:
        """Return all checkpoints, most recently written first."""
        with self._lock:
            self._ensure_checkpoint_index()
            items = [deepcopy(item) for item in self._checkpoints_by_session.values()]
        items.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
        return items

    def claim_human_decision(
        self, session_id: str, decision: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Persist a once-only decision claim before any approved action executes."""
        with self._lock:
            checkpoint = self.get_checkpoint(session_id)
            if checkpoint is None:
                return None
            pending = checkpoint.get("pending_interaction")
            if not isinstance(pending, dict):
                return None
            if pending.get("decision_claim_id"):
                return None
            if str(decision.get("request_id") or "") != str(
                pending.get("request_id") or ""
            ):
                return None
            pending = dict(pending)
            pending["decision_claim_id"] = uuid.uuid4().hex
            pending["decision_claimed_at"] = _now()
            checkpoint["pending_interaction"] = pending
            checkpoint["submitted_human_decision"] = deepcopy(decision)
            self.write_checkpoint(checkpoint)
            return checkpoint

    def claim_goal_action(
        self, session_id: str, goal_action: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Persist a once-only goal-action claim before the resumed run starts."""
        with self._lock:
            checkpoint = self.get_checkpoint(session_id)
            if checkpoint is None:
                return None
            if checkpoint.get("goal_action_claim_id"):
                return None
            checkpoint["goal_action_claim_id"] = uuid.uuid4().hex
            checkpoint["goal_action_claimed_at"] = _now()
            checkpoint["submitted_goal_action"] = deepcopy(goal_action)
            self.write_checkpoint(checkpoint)
            return checkpoint

    def list_projects(self) -> list[dict[str, Any]]:
        projects: list[dict[str, Any]] = []
        for project_id, state in self._all_states().items():
            project = state.get("project")
            if not isinstance(project, dict):
                continue
            projects.append(
                {
                    "id": project.get("id") or project_id,
                    "name": project.get("name") or project_id,
                    "updatedAt": state.get("updatedAt") or project.get("updatedAt") or "",
                    "starred": bool(state.get("starred", False)),
                }
            )
        return sorted(projects, key=lambda item: str(item.get("id") or ""))

    def get_project(self, project_id: str) -> dict[str, Any]:
        state = self._state_for(project_id, strict_legacy=True)
        if not isinstance(state.get("project"), dict):
            raise _http_exception(status_code=404, detail="Project not found.")
        return self._project_response(state)

    def save_project(self, project_id: str, project: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(project, dict):
            raise _http_exception(status_code=400, detail="Project payload must be an object.")
        project_id = validate_project_id(project_id)
        project = dict(project)
        project["id"] = project_id
        project.setdefault("updatedAt", _now())
        self._append_event(
            {
                "type": "project_saved",
                "project_id": project_id,
                "project": project,
            }
        )
        return self.get_project(project_id)

    def set_starred(self, project_id: str, starred: bool) -> dict[str, Any]:
        project_id = validate_project_id(project_id)
        self._append_event(
            {"type": "starred", "project_id": project_id, "starred": bool(starred)}
        )
        return self.get_project(project_id)

    def list_review_items(self, project_id: str, *, status: str | None = None) -> list[dict[str, Any]]:
        try:
            state = self._state_for(project_id, strict_legacy=True)
        except Exception as exc:
            if getattr(exc, "status_code", None) == 404:
                return []
            raise
        queue = self._review_queue(state)
        if status:
            queue = [item for item in queue if item.get("status") == status]
        return queue

    def add_review_item(self, project_id: str, item: dict[str, Any]) -> dict[str, Any]:
        project_id = validate_project_id(project_id)
        if not isinstance(item, dict):
            raise _http_exception(status_code=400, detail="Review item must be an object.")
        self.get_project(project_id)
        now = _now()
        item = dict(item)
        item.setdefault("id", f"review-{uuid.uuid4().hex}")
        item.setdefault("status", "open")
        item.setdefault("createdAt", now)
        item["updatedAt"] = now
        self._append_event(
            {
                "type": "review_item_added",
                "project_id": project_id,
                "item": item,
            }
        )
        return item

    def resolve_review_item(
        self,
        project_id: str,
        item_id: str,
        *,
        status: str,
        reason: str = "",
    ) -> dict[str, Any]:
        project_id = validate_project_id(project_id)
        if status not in VALID_REVIEW_STATUSES:
            raise _http_exception(status_code=400, detail="Invalid review status.")
        queue = self.list_review_items(project_id)
        if not any(item.get("id") == item_id for item in queue):
            raise _http_exception(status_code=404, detail="Review item not found.")
        self._append_event(
            {
                "type": "review_item_resolved",
                "project_id": project_id,
                "item_id": item_id,
                "status": status,
                "reason": reason[:1000],
            }
        )
        for item in self.list_review_items(project_id):
            if item.get("id") == item_id:
                return item
        raise _http_exception(status_code=404, detail="Review item not found.")

    def add_turn(self, project_id: str, turn: dict[str, Any]) -> dict[str, Any]:
        project_id = validate_project_id(project_id)
        if not isinstance(turn, dict):
            raise _http_exception(status_code=400, detail="Turn payload must be an object.")
        record = {
            "id": uuid.uuid4().hex,
            "conversation_id": str(turn.get("conversation_id") or "")[:128],
            "problem": (turn.get("problem") or "")[:8000],
            "answer": (turn.get("answer") or "")[:20000],
            "attachments": turn.get("attachments") or [],
            "created_at": _now(),
        }
        if turn.get("verification_status"):
            record["verification_status"] = str(turn["verification_status"])[:32]
        if turn.get("strategy"):
            record["strategy"] = str(turn["strategy"])[:32]
        if turn.get("session_id"):
            record["session_id"] = str(turn["session_id"])[:128]
        if turn.get("lean_proofs"):
            record["lean_proofs"] = [
                str(proof)[:20000] for proof in list(turn["lean_proofs"])[:20]
            ]
        if turn.get("verification_issues"):
            record["verification_issues"] = [
                str(issue)[:2000] for issue in list(turn["verification_issues"])[:20]
            ]
        if turn.get("tool_evidence"):
            record["tool_evidence"] = _clean_tool_evidence(turn["tool_evidence"])
        self._append_event({"type": "turn", "project_id": project_id, "turn": record})
        return record

    def update_turn(
        self,
        project_id: str,
        turn_id: str,
        *,
        answer: str | None = None,
        problem: str | None = None,
        verification_status: str | None = None,
        strategy: str | None = None,
        session_id: str | None = None,
        lean_proofs: list[Any] | None = None,
        verification_issues: list[Any] | None = None,
        tool_evidence: list[Any] | None = None,
    ) -> dict[str, Any]:
        """Update fields on an existing turn (e.g. fill in the answer when a solve finishes)."""
        project_id = validate_project_id(project_id)
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            raise _http_exception(status_code=400, detail="Turn id is required.")
        patch: dict[str, Any] = {"id": turn_id}
        if answer is not None:
            patch["answer"] = str(answer)[:20000]
        if problem is not None:
            patch["problem"] = str(problem)[:8000]
        if verification_status is not None:
            patch["verification_status"] = str(verification_status)[:32]
        if strategy is not None:
            patch["strategy"] = str(strategy)[:32]
        if session_id is not None:
            patch["session_id"] = str(session_id)[:128]
        if lean_proofs is not None:
            patch["lean_proofs"] = [str(proof)[:20000] for proof in list(lean_proofs)[:20]]
        if verification_issues is not None:
            patch["verification_issues"] = [
                str(issue)[:2000] for issue in list(verification_issues)[:20]
            ]
        if tool_evidence is not None:
            patch["tool_evidence"] = _clean_tool_evidence(tool_evidence)
        if len(patch) == 1:
            raise _http_exception(status_code=400, detail="No turn fields to update.")
        self._append_event(
            {"type": "turn_updated", "project_id": project_id, "turn": patch}
        )
        for turn in self.list_turns(project_id):
            if isinstance(turn, dict) and str(turn.get("id") or "") == turn_id:
                return turn
        raise _http_exception(status_code=404, detail="Turn not found.")

    def list_turns(self, project_id: str) -> list[dict[str, Any]]:
        project_id = validate_project_id(project_id)
        return list(self._state_for(project_id, strict_legacy=False).get("turns", []))

    def delete_conversation(self, project_id: str, conversation_id: str) -> int:
        """Remove all turns belonging to a conversation. Returns deleted count.

        Legacy turns without ``conversation_id`` are grouped by turn ``id`` in
        the UI; matching either field removes those historical records.
        """
        project_id = validate_project_id(project_id)
        conversation_id = str(conversation_id or "").strip()[:128]
        if not conversation_id:
            raise _http_exception(status_code=400, detail="Conversation id is required.")
        turns = self.list_turns(project_id)
        deleted = sum(
            1
            for turn in turns
            if isinstance(turn, dict)
            and (
                str(turn.get("conversation_id") or "") == conversation_id
                or (
                    not str(turn.get("conversation_id") or "")
                    and str(turn.get("id") or "") == conversation_id
                )
            )
        )
        if deleted == 0:
            raise _http_exception(status_code=404, detail="Conversation not found.")
        self._append_event(
            {
                "type": "conversation_deleted",
                "project_id": project_id,
                "conversation_id": conversation_id,
            }
        )
        return deleted

    def list_facts(
        self, project_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._list_knowledge(project_id, "facts", "statement", limit=limit, offset=offset)

    def list_intuitions(
        self, project_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._list_knowledge(project_id, "intuitions", "title", limit=limit, offset=offset)

    def list_tricks(
        self, project_id: str, *, limit: int = 200, offset: int = 0
    ) -> list[dict[str, Any]]:
        return self._list_knowledge(project_id, "tricks", "title", limit=limit, offset=offset)

    def add_fact(
        self,
        project_id: str,
        statement: str,
        why: str = "",
        source: str = "",
        *,
        status: str = "approved",
    ) -> dict[str, Any]:
        inserted = self.add_many(
            project_id,
            [{
                "statement": statement,
                "why": why,
                "source": source,
                "source_type": "manual",
                "status": status,
            }],
            [],
            [],
        )["facts"]
        if not inserted:
            raise _http_exception(status_code=400, detail="Fact statement is required.")
        return inserted[0]

    def add_intuition(
        self,
        project_id: str,
        title: str,
        body: str,
        kind: str = "",
        source: str = "",
    ) -> dict[str, Any]:
        inserted = self.add_many(
            project_id,
            [],
            [{
                "title": title,
                "body": body,
                "kind": kind,
                "source": source,
                "source_type": "manual",
                "status": "approved",
            }],
            [],
        )["intuitions"]
        if not inserted:
            raise _http_exception(status_code=400, detail="Intuition title is required.")
        return inserted[0]

    def add_trick(
        self,
        project_id: str,
        title: str,
        body: str,
        category: str = "",
        source: str = "",
        *,
        status: str = "approved",
    ) -> dict[str, Any]:
        inserted = self.add_many(
            project_id,
            [],
            [],
            [{
                "title": title,
                "body": body,
                "category": category,
                "source": source,
                "source_type": "manual",
                "status": status,
            }],
        )["tricks"]
        if not inserted:
            raise _http_exception(status_code=400, detail="Technique title is required.")
        return inserted[0]

    def add_many(
        self,
        project_id: str,
        facts: list[dict[str, str]],
        intuitions: list[dict[str, str]],
        tricks: list[dict[str, str]],
    ) -> dict[str, list[dict[str, Any]]]:
        project_id = validate_project_id(project_id)
        inserted = {
            "facts": self._knowledge_rows(project_id, facts, FACT_MEMORY_FIELDS),
            "intuitions": self._knowledge_rows(project_id, intuitions, INTUITION_MEMORY_FIELDS),
            "tricks": self._knowledge_rows(project_id, tricks, TRICK_MEMORY_FIELDS),
        }
        if inserted["facts"] or inserted["intuitions"] or inserted["tricks"]:
            self._append_event(
                {
                    "type": "knowledge_added",
                    "project_id": project_id,
                    "facts": inserted["facts"],
                    "intuitions": inserted["intuitions"],
                    "tricks": inserted["tricks"],
                }
            )
        return inserted

    def update_knowledge_item(
        self,
        project_id: str,
        item_id: str,
        kind: str,
        fields: dict[str, Any],
    ) -> None:
        """Patch a single knowledge item by id. kind must be 'fact', 'intuition', or 'trick'."""
        project_id = validate_project_id(project_id)
        patch = {k: v for k, v in fields.items() if k not in ("id", "project_id", "created_at")}
        if not patch:
            return
        self._append_event({
            "type": "knowledge_updated",
            "project_id": project_id,
            "item_id": item_id,
            "kind": kind,
            "fields": patch,
        })

    def delete_knowledge_item(self, project_id: str, item_id: str, kind: str) -> None:
        """Remove a knowledge item from the store."""
        project_id = validate_project_id(project_id)
        self._append_event({
            "type": "knowledge_deleted",
            "project_id": project_id,
            "item_id": item_id,
            "kind": kind,
        })

    def get_knowledge_item(
        self, project_id: str, item_id: str, kind: str
    ) -> dict[str, Any] | None:
        """Return a single knowledge item by id, or None.

        Uses the public ``list_*`` methods to look up the item so callers get a
        consistent, de-duplicated view of the project's knowledge.

        ``kind`` must be one of ``fact``, ``intuition``, ``trick``, or ``graph_node``.
        For graph nodes, ``item_id`` is matched against either ``id`` or ``ref``.
        """
        project_id = validate_project_id(project_id)
        if kind == "graph_node":
            for row in self.list_knowledge_graph_nodes(project_id):
                if isinstance(row, dict) and item_id in (
                    str(row.get("id") or ""),
                    str(row.get("ref") or ""),
                ):
                    return deepcopy(row)
            return None
        list_method = {
            "fact": self.list_facts,
            "intuition": self.list_intuitions,
            "trick": self.list_tricks,
        }.get(kind)
        if list_method is None:
            return None
        # Paginate: list_* clamps the limit (see _normalize_limit), so a
        # single oversized request silently truncates and the lookup would
        # miss items beyond the cap.
        offset = 0
        while True:
            rows = list_method(project_id, limit=1000, offset=offset)
            if not rows:
                return None
            for row in rows:
                if isinstance(row, dict) and str(row.get("id") or "") == item_id:
                    return deepcopy(row)
            if len(rows) < 1000:
                return None
            offset += len(rows)

    def add_knowledge_graph_edges(
        self,
        project_id: str,
        edges: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        project_id = validate_project_id(project_id)
        normalized: list[dict[str, Any]] = []
        now = _now()
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            source = str(edge.get("source") or "").strip()
            target = str(edge.get("target") or "").strip()
            kind = str(edge.get("kind") or "related_to").strip().lower()
            if not source or not target or source == target:
                continue
            if kind not in VALID_GRAPH_EDGE_KINDS:
                kind = "related_to"
            normalized.append(
                {
                    "id": str(edge.get("id") or f"{source}:{kind}:{target}"),
                    "source": source,
                    "target": target,
                    "kind": kind,
                    "label": str(edge.get("label") or "").strip(),
                    "evidence": str(edge.get("evidence") or "").strip(),
                    "weight": float(edge.get("weight") if edge.get("weight") is not None else 1.0),
                    "status": str(edge.get("status") or "candidate").strip(),
                    "score": str(edge.get("score") or "").strip(),
                    "review_note": str(edge.get("review_note") or "").strip(),
                    "created_at": str(edge.get("created_at") or now),
                    "updated_at": str(edge.get("updated_at") or now),
                    "metadata": edge.get("metadata") if isinstance(edge.get("metadata"), dict) else {},
                }
            )
        if normalized:
            self._append_event(
                {
                    "type": "knowledge_graph_edges_added",
                    "project_id": project_id,
                    "edges": normalized,
                }
            )
        return normalized

    def add_knowledge_graph_nodes(
        self,
        project_id: str,
        nodes: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        project_id = validate_project_id(project_id)
        normalized: list[dict[str, Any]] = []
        now = _now()
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("id") or node.get("ref") or uuid.uuid4()).strip()
            kind = str(node.get("kind") or "topic").strip().lower()
            title = str(node.get("title") or node.get("label") or node.get("statement") or "").strip()
            if not node_id or not title:
                continue
            if kind not in VALID_GRAPH_NODE_KINDS:
                kind = "topic"
            normalized.append(
                {
                    "id": node_id,
                    "ref": str(node.get("ref") or node_id).strip(),
                    "kind": kind,
                    "title": title,
                    "statement": str(node.get("statement") or "").strip(),
                    "body": str(node.get("body") or "").strip(),
                    "evidence": str(node.get("evidence") or "").strip(),
                    "source_type": str(node.get("source_type") or "").strip(),
                    "source_ref": str(node.get("source_ref") or "").strip(),
                    "source_title": str(node.get("source_title") or "").strip(),
                    "status": str(node.get("status") or "candidate").strip(),
                    "score": str(node.get("score") or "").strip(),
                    "formal_status": str(node.get("formal_status") or "").strip(),
                    "lean_name": str(node.get("lean_name") or "").strip(),
                    "domain": str(node.get("domain") or "").strip(),
                    "tags": str(node.get("tags") or "").strip(),
                    "created_by": str(node.get("created_by") or "memory_consolidation").strip(),
                    "review_note": str(node.get("review_note") or "").strip(),
                    "created_at": str(node.get("created_at") or now),
                    "updated_at": str(node.get("updated_at") or now),
                    "metadata": node.get("metadata") if isinstance(node.get("metadata"), dict) else {},
                }
            )
        if normalized:
            self._append_event(
                {
                    "type": "knowledge_graph_nodes_added",
                    "project_id": project_id,
                    "nodes": normalized,
                }
            )
        return normalized

    def list_knowledge_graph_nodes(self, project_id: str) -> list[dict[str, Any]]:
        project_id = validate_project_id(project_id)
        by_id: dict[str, dict[str, Any]] = {}
        for event in self._iter_events():
            if event.get("project_id") != project_id:
                continue
            if event.get("type") != "knowledge_graph_nodes_added":
                continue
            nodes = event.get("nodes")
            if not isinstance(nodes, list):
                continue
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                node_id = str(node.get("id") or "").strip()
                if node_id:
                    by_id[node_id] = dict(node)
        return list(by_id.values())

    def list_knowledge_graph_edges(self, project_id: str) -> list[dict[str, Any]]:
        project_id = validate_project_id(project_id)
        by_id: dict[str, dict[str, Any]] = {}
        for event in self._iter_events():
            if event.get("project_id") != project_id:
                continue
            if event.get("type") != "knowledge_graph_edges_added":
                continue
            edges = event.get("edges")
            if not isinstance(edges, list):
                continue
            for edge in edges:
                if not isinstance(edge, dict):
                    continue
                edge_id = str(edge.get("id") or "").strip()
                if edge_id:
                    by_id[edge_id] = dict(edge)
        return list(by_id.values())

    def search_facts(
        self, project_id: str, query: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self._search(self.list_facts(project_id, limit=1000), query, ["statement", "why"], limit)

    def search_intuitions(
        self, project_id: str, query: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self._search(
            self.list_intuitions(project_id, limit=1000), query, ["title", "body"], limit
        )

    def search_tricks(
        self, project_id: str, query: str, *, limit: int = 20
    ) -> list[dict[str, Any]]:
        return self._search(self.list_tricks(project_id, limit=1000), query, ["title", "body"], limit)

    def _append_event(self, event: dict[str, Any]) -> dict[str, Any]:
        event = dict(event)
        event.setdefault("version", 1)
        event.setdefault("at", _now())
        text = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            self._ensure_event_index()
            indexed_size = (
                self._event_log_signature[2]
                if self._event_log_signature is not None
                else 0
            )
            expected_size = indexed_size + len(text.encode("utf-8"))
            with self.event_log_path.open("a", encoding="utf-8") as handle:
                handle.write(text)
            cached_event = deepcopy(event)
            self._events_cache.append(cached_event)
            project_id = cached_event.get("project_id")
            if isinstance(project_id, str) and PROJECT_ID_RE.match(project_id):
                state = self._state_index.setdefault(project_id, self._empty_state(project_id))
                self._apply_event(state, cached_event)
            signature = self._file_signature(self.event_log_path)
            self._event_log_signature = (
                (signature[0], signature[1], expected_size, signature[3])
                if signature is not None
                else None
            )
        return event

    def _iter_events(self) -> list[dict[str, Any]]:
        with self._lock:
            self._ensure_event_index()
            return deepcopy(self._events_cache)

    @staticmethod
    def _file_signature(path: Path) -> tuple[int, int, int, int] | None:
        try:
            stat = path.stat()
        except FileNotFoundError:
            return None
        return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

    def _current_legacy_signatures(
        self,
    ) -> tuple[tuple[str, tuple[int, int, int, int]], ...]:
        assert self.root is not None
        signatures: list[tuple[str, tuple[int, int, int, int]]] = []
        for path in sorted(self.root.glob("*.json")):
            if not path.is_file():
                continue
            signature = self._file_signature(path)
            if signature is not None:
                signatures.append((path.name, signature))
        return tuple(signatures)

    def _ensure_event_index(self) -> None:
        with self._lock:
            event_signature = self._file_signature(self.event_log_path)
            legacy_signatures = self._current_legacy_signatures()
            if (
                self._event_index_loaded
                and event_signature == self._event_log_signature
                and legacy_signatures == self._legacy_signatures
            ):
                return

            states: dict[str, dict[str, Any]] = {}
            legacy_errors: set[str] = set()
            assert self.root is not None
            for filename, _signature in legacy_signatures:
                path = self.root / filename
                project_id = path.stem
                if not PROJECT_ID_RE.match(project_id):
                    continue
                try:
                    legacy = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    legacy_errors.add(project_id)
                    continue
                if isinstance(legacy, dict):
                    states[project_id] = self._state_from_legacy(project_id, legacy)

            events: list[dict[str, Any]] = []
            if event_signature is not None:
                with self.event_log_path.open("r", encoding="utf-8") as handle:
                    for raw in handle:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            event = json.loads(raw)
                        except Exception:
                            continue
                        if not isinstance(event, dict):
                            continue
                        events.append(event)
                        project_id = event.get("project_id")
                        if not isinstance(project_id, str) or not PROJECT_ID_RE.match(project_id):
                            continue
                        state = states.setdefault(project_id, self._empty_state(project_id))
                        self._apply_event(state, event)

            self._events_cache = events
            self._state_index = states
            self._legacy_errors = legacy_errors
            self._event_log_signature = event_signature
            self._legacy_signatures = legacy_signatures
            self._event_index_loaded = True

    def _ensure_checkpoint_index(self) -> None:
        with self._lock:
            signature = self._file_signature(self.checkpoint_log_path)
            if self._checkpoint_index_loaded and signature == self._checkpoint_log_signature:
                return
            checkpoints: dict[str, dict[str, Any]] = {}
            if signature is not None:
                with self.checkpoint_log_path.open("r", encoding="utf-8") as handle:
                    for raw in handle:
                        raw = raw.strip()
                        if not raw:
                            continue
                        try:
                            record = json.loads(raw)
                        except Exception:
                            continue
                        if not isinstance(record, dict):
                            continue
                        session_id = str(record.get("session_id") or "")
                        if session_id:
                            checkpoints[session_id] = record
            self._checkpoints_by_session = checkpoints
            self._checkpoint_log_signature = signature
            self._checkpoint_index_loaded = True

    def _legacy_project_path(self, project_id: str) -> Path:
        project_id = validate_project_id(project_id)
        assert self.root is not None
        path = (self.root / f"{project_id}.json").resolve()
        if path.parent != self.root:
            raise _http_exception(status_code=400, detail="Invalid project id.")
        return path

    def _load_legacy_project(self, project_id: str, *, strict: bool) -> dict[str, Any] | None:
        path = self._legacy_project_path(project_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            if strict:
                err = _http_exception(status_code=500, detail="Project file is unreadable.")
                raise err from exc
            return None
        return data if isinstance(data, dict) else None

    def _all_states(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            self._ensure_event_index()
            return deepcopy(self._state_index)

    def _state_for(self, project_id: str, *, strict_legacy: bool) -> dict[str, Any]:
        project_id = validate_project_id(project_id)
        with self._lock:
            self._ensure_event_index()
            if strict_legacy and project_id in self._legacy_errors:
                raise _http_exception(status_code=500, detail="Project file is unreadable.")
            state = self._state_index.get(project_id)
            return deepcopy(state) if state is not None else self._empty_state(project_id)

    def _empty_state(self, project_id: str) -> dict[str, Any]:
        return {
            "id": project_id,
            "project": None,
            "review": {},
            "reviewOrder": [],
            "facts": [],
            "intuitions": [],
            "tricks": [],
            "knowledge_nodes": [],
            "knowledge_edges": [],
            "scores": {},
            "turns": [],
            "updatedAt": "",
            "starred": False,
        }

    def _state_from_legacy(self, project_id: str, data: dict[str, Any]) -> dict[str, Any]:
        state = self._empty_state(project_id)
        project = data.get("project") if isinstance(data.get("project"), dict) else None
        if project is not None:
            state["project"] = project
            state["updatedAt"] = data.get("updatedAt") or project.get("updatedAt") or ""
        queue = data.get("reviewQueue")
        if isinstance(queue, list):
            for item in reversed(queue):
                if isinstance(item, dict):
                    self._put_review_item(state, item)
        return state

    def _apply_event(self, state: dict[str, Any], event: dict[str, Any]) -> None:
        event_type = event.get("type")
        at = event.get("at") or _now()
        if event_type == "project_saved":
            project = event.get("project")
            if isinstance(project, dict):
                state["project"] = dict(project)
                state["updatedAt"] = at
            return
        if event_type == "review_item_added":
            item = event.get("item")
            if isinstance(item, dict):
                self._put_review_item(state, item)
                state["updatedAt"] = at
            return
        if event_type == "review_item_resolved":
            item_id = str(event.get("item_id") or "")
            item = state["review"].get(item_id)
            if isinstance(item, dict):
                item["status"] = event.get("status") or item.get("status") or "open"
                item["reason"] = str(event.get("reason") or "")[:1000]
                item["updatedAt"] = at
                state["updatedAt"] = at
            return
        if event_type == "knowledge_added":
            for key in ("facts", "intuitions", "tricks"):
                rows = event.get(key)
                if isinstance(rows, list):
                    state[key].extend(row for row in rows if isinstance(row, dict))
            state["updatedAt"] = at
            return
        if event_type == "knowledge_graph_nodes_added":
            rows = event.get("nodes")
            if isinstance(rows, list):
                state.setdefault("knowledge_nodes", []).extend(
                    row for row in rows if isinstance(row, dict)
                )
            state["updatedAt"] = at
            return
        if event_type == "knowledge_graph_edges_added":
            rows = event.get("edges")
            if isinstance(rows, list):
                state.setdefault("knowledge_edges", []).extend(
                    row for row in rows if isinstance(row, dict)
                )
            state["updatedAt"] = at
            return
        if event_type == "knowledge_updated":
            item_id = str(event.get("item_id") or "")
            kind = str(event.get("kind") or "")
            key = f"{kind}s"
            fields = event.get("fields")
            if item_id and key in state and isinstance(fields, dict):
                for row in state[key]:
                    if isinstance(row, dict) and str(row.get("id") or "") == item_id:
                        row.update({k: v for k, v in fields.items()
                                    if k not in ("id", "project_id", "created_at")})
                        row["updated_at"] = at
                        break
            state["updatedAt"] = at
            return
        if event_type == "knowledge_deleted":
            item_id = str(event.get("item_id") or "")
            kind = str(event.get("kind") or "")
            key = f"{kind}s"
            if item_id and key in state:
                state[key] = [r for r in state[key]
                               if not (isinstance(r, dict) and str(r.get("id") or "") == item_id)]
            state["updatedAt"] = at
            return
        if event_type == "turn":
            turn = event.get("turn")
            if isinstance(turn, dict):
                if not isinstance(state.get("project"), dict):
                    state["project"] = {"id": state.get("id")}
                state.setdefault("turns", []).append(turn)
                state["updatedAt"] = at
            return
        if event_type == "turn_updated":
            patch = event.get("turn")
            if isinstance(patch, dict):
                turn_id = str(patch.get("id") or "")
                turns = state.get("turns") or []
                for index, turn in enumerate(turns):
                    if not isinstance(turn, dict):
                        continue
                    if str(turn.get("id") or "") != turn_id:
                        continue
                    updated = dict(turn)
                    if "answer" in patch:
                        updated["answer"] = patch.get("answer") or ""
                    if "problem" in patch:
                        updated["problem"] = patch.get("problem") or ""
                    for key in (
                        "verification_status",
                        "strategy",
                        "session_id",
                        "lean_proofs",
                        "verification_issues",
                        "tool_evidence",
                    ):
                        if key in patch:
                            updated[key] = patch[key]
                    updated["updated_at"] = at
                    turns[index] = updated
                    state["updatedAt"] = at
                    break
            return
        if event_type == "conversation_deleted":
            conversation_id = str(event.get("conversation_id") or "")
            if conversation_id:
                turns = state.get("turns") or []
                state["turns"] = [
                    turn
                    for turn in turns
                    if not (
                        isinstance(turn, dict)
                        and (
                            str(turn.get("conversation_id") or "") == conversation_id
                            or (
                                not str(turn.get("conversation_id") or "")
                                and str(turn.get("id") or "") == conversation_id
                            )
                        )
                    )
                ]
                state["updatedAt"] = at
            return
        if event_type == "starred":
            if not isinstance(state.get("project"), dict):
                state["project"] = {"id": state.get("id")}
            state["starred"] = bool(event.get("starred"))
            state["updatedAt"] = at

    def _put_review_item(self, state: dict[str, Any], item: dict[str, Any]) -> None:
        item = dict(item)
        item_id = str(item.get("id") or "")
        if not item_id:
            return
        order = state["reviewOrder"]
        if item_id in order:
            order.remove(item_id)
        order.insert(0, item_id)
        state["review"][item_id] = item

    def _project_response(self, state: dict[str, Any]) -> dict[str, Any]:
        project = state.get("project") or {}
        project_id = state.get("id") or project.get("id") or ""
        return {
            "id": project_id,
            "project": project,
            "reviewQueue": self._review_queue(state),
            "turns": list(state.get("turns") or []),
            "updatedAt": state.get("updatedAt") or project.get("updatedAt") or "",
            "starred": bool(state.get("starred", False)),
        }

    def _review_queue(self, state: dict[str, Any]) -> list[dict[str, Any]]:
        review = state.get("review") if isinstance(state.get("review"), dict) else {}
        order = state.get("reviewOrder") if isinstance(state.get("reviewOrder"), list) else []
        return [review[item_id] for item_id in order if isinstance(review.get(item_id), dict)]

    def _list_knowledge(
        self,
        project_id: str,
        key: str,
        primary_key: str,
        *,
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        project_id = validate_project_id(project_id)
        state = self._state_for(project_id, strict_legacy=False)
        rows = self._embedded_knowledge(project_id, state, key)
        rows.extend(dict(row) for row in state.get(key, []) if isinstance(row, dict))
        deduped = self._dedupe_rows(rows, primary_key)
        start = _normalize_offset(offset)
        end = start + _normalize_limit(limit)
        return deduped[start:end]

    def _embedded_knowledge(
        self, project_id: str, state: dict[str, Any], key: str
    ) -> list[dict[str, Any]]:
        project = state.get("project")
        if not isinstance(project, dict):
            return []
        raw = project.get(key)
        if not isinstance(raw, list):
            return []
        rows: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            row = dict(item)
            row.setdefault("project_id", project_id)
            row.setdefault("source", "project")
            if "created_at" not in row and row.get("createdAt"):
                row["created_at"] = row.get("createdAt")
            if "updated_at" not in row and row.get("updatedAt"):
                row["updated_at"] = row.get("updatedAt")
            rows.append(row)
        return rows

    def _dedupe_rows(self, rows: list[dict[str, Any]], primary_key: str) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for row in rows:
            row_id = str(row.get("id") or "").strip()
            primary = str(row.get(primary_key) or "").strip().lower()
            if not row_id and not primary:
                continue
            dedupe_key = row_id or f"{primary_key}:{primary}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            out.append(row)
        return out

    def _knowledge_rows(
        self,
        project_id: str,
        items: list[dict[str, str]],
        fields: list[str],
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        now = _now()
        primary = fields[0]
        for item in items:
            if not isinstance(item, dict):
                continue
            row: dict[str, Any] = {
                "id": str(item.get("id") or uuid.uuid4()),
                "project_id": project_id,
                "created_at": str(item.get("created_at") or now),
                "updated_at": str(item.get("updated_at") or now),
            }
            for field in fields:
                value = item.get(field)
                if field == "metadata":
                    if isinstance(value, dict):
                        row[field] = dict(value)
                    elif isinstance(value, list):
                        row[field] = list(value)
                    elif isinstance(value, str):
                        row[field] = value
                    else:
                        row[field] = ""
                else:
                    row[field] = str(value or "").strip()
            if "status" in fields and not row.get("status"):
                row["status"] = "candidate"
            if row.get(primary):
                rows.append(row)
        return rows

    def _search(
        self,
        rows: list[dict[str, Any]],
        query: str,
        columns: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        if not (query or "").strip():
            return []
        trusted_rows: list[dict[str, Any]] = []
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            if status not in TRUSTED_KNOWLEDGE_STATUSES:
                continue
            trusted_rows.append(row)
        return rank_rows(
            trusted_rows,
            query,
            columns,
            limit=_normalize_limit(limit),
        )
