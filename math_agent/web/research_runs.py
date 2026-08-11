"""Read-only projections and share-token store for the research command deck.

Includes checkpoint-to-public-run projections plus ``ShareStore``, an append-only,
mtime-invalidated token log for anonymous read-only shares.

.. note::
    ``ShareStore`` is designed for single-process or low-concurrency local
    deployments. When multiple Uvicorn/gunicorn workers run in parallel, each
    worker holds its own in-memory singleton and may append duplicate live tokens
    for the same ``(user_id, session_id)``. In v1 this is functionally harmless
    (all duplicate tokens work) and is accepted; cross-worker uniqueness is not
    guaranteed.
"""
from __future__ import annotations

import copy
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from math_agent.agent.research_artifacts import _safe_component
from math_agent.web.project_store import _default_root

RUN_STATUSES = ("running", "waiting_human", "completed", "best_effort")
_EXCERPT_CHARS = 160
_PRIVATE_DETAIL_KEYS = frozenset({
    "project_context",
    "context_preamble",
    "turns",
    "human_decisions",
    "submitted_human_decision",
})


def _excerpt(text: Any, *, limit: int = _EXCERPT_CHARS) -> str:
    body = " ".join(str(text or "").split())
    return body if len(body) <= limit else body[: limit - 1].rstrip() + "…"


def is_research_checkpoint(checkpoint: Any) -> bool:
    return isinstance(checkpoint, dict) and checkpoint.get("strategy") == "research"


def _proof_goals(checkpoint: dict[str, Any]) -> list[dict[str, Any]]:
    graph = checkpoint.get("proof_graph")
    if not isinstance(graph, dict):
        return []
    return [goal for goal in (graph.get("goals") or []) if isinstance(goal, dict)]


def run_status(checkpoint: dict[str, Any], *, active: bool) -> str:
    if isinstance(checkpoint.get("pending_interaction"), dict):
        return "waiting_human"
    if active:
        return "running"
    graph = checkpoint.get("proof_graph")
    root_id = graph.get("root_id") if isinstance(graph, dict) else None
    for goal in _proof_goals(checkpoint):
        if goal.get("id") == root_id:
            return "completed" if goal.get("status") == "proved" else "best_effort"
    return "best_effort"


def summarize_run(checkpoint: dict[str, Any], *, active: bool) -> dict[str, Any]:
    graph = checkpoint.get("proof_graph")
    root_id = graph.get("root_id") if isinstance(graph, dict) else None
    lemmas = [goal for goal in _proof_goals(checkpoint) if goal.get("id") != root_id]
    budget = checkpoint.get("budget_consumption")
    if not isinstance(budget, dict):
        budget = {}
    return {
        "session_id": str(checkpoint.get("session_id") or ""),
        "problem_excerpt": _excerpt(checkpoint.get("problem")),
        "status": run_status(checkpoint, active=active),
        "goals_total": len(lemmas),
        "goals_proved": sum(1 for goal in lemmas if goal.get("status") == "proved"),
        "has_pending_interaction": isinstance(checkpoint.get("pending_interaction"), dict),
        "budget_extensions": int(budget.get("research_budget_extensions") or 0),
        "updated_at": str(checkpoint.get("at") or ""),
    }


def _public_pending(pending: Any) -> dict[str, Any] | None:
    if not isinstance(pending, dict):
        return None
    return {
        "request_id": str(pending.get("request_id") or ""),
        "kind": str(pending.get("kind") or ""),
        "stage": str(pending.get("stage") or ""),
        "question": str(pending.get("question") or ""),
        "details": dict(pending.get("details")) if isinstance(pending.get("details"), dict) else {},
        "allowed_decisions": [str(item) for item in (pending.get("allowed_decisions") or [])],
    }


def _public_artifact(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(item.get("id") or ""),
        "goal_id": str(item.get("goal_id") or ""),
        "goal_statement": str(item.get("goal_statement") or ""),
        "attempt_index": int(item.get("attempt_index") or 0),
        "strategy": str(item.get("strategy") or ""),
        "status": str(item.get("status") or ""),
        "summary": str(item.get("summary") or ""),
        "verification_status": str(item.get("verification_status") or ""),
        "verification_issues": [str(v) for v in (item.get("verification_issues") or [])],
        "created_at": str(item.get("created_at") or ""),
    }


def run_detail(checkpoint: dict[str, Any], *, active: bool) -> dict[str, Any]:
    summary = summarize_run(checkpoint, active=active)
    artifacts = [
        _public_artifact(item)
        for item in (checkpoint.get("research_artifacts") or [])
        if isinstance(item, dict)
    ]
    failures = [
        {
            "goal_id": str(item.get("goal_id") or ""),
            "reason": str(item.get("reason") or ""),
            "summary": str(item.get("summary") or "")[:600],
        }
        for item in (checkpoint.get("research_failures") or [])[-20:]
        if isinstance(item, dict)
    ]
    detail = {
        "session_id": summary["session_id"],
        "problem": str(checkpoint.get("problem") or ""),
        "status": summary["status"],
        "active": active,
        "goals_total": summary["goals_total"],
        "goals_proved": summary["goals_proved"],
        "budget_extensions": summary["budget_extensions"],
        "proof_graph": copy.deepcopy(checkpoint.get("proof_graph"))
        if isinstance(checkpoint.get("proof_graph"), dict)
        else {},
        "pending_interaction": _public_pending(checkpoint.get("pending_interaction")),
        "research_artifacts": artifacts,
        "research_failures": failures,
        "updated_at": summary["updated_at"],
    }
    for key in _PRIVATE_DETAIL_KEYS:
        detail.pop(key, None)
    return detail


def inbox_entry(checkpoint: dict[str, Any]) -> dict[str, Any] | None:
    pending_raw = checkpoint.get("pending_interaction")
    if not isinstance(pending_raw, dict) or pending_raw.get("decision_claim_id"):
        return None
    pending = _public_pending(pending_raw)
    assert pending is not None
    waiting = 0.0
    try:
        created_at = pending_raw.get("created_at")
        marked = datetime.fromisoformat(str(created_at or checkpoint.get("at") or ""))
        waiting = max(0.0, (datetime.now(timezone.utc) - marked).total_seconds())
    except (ValueError, TypeError):
        pass
    return {
        "session_id": str(checkpoint.get("session_id") or ""),
        "problem_excerpt": _excerpt(checkpoint.get("problem")),
        "kind": pending["kind"],
        "question": pending["question"],
        "request_id": pending["request_id"],
        "allowed_decisions": pending["allowed_decisions"],
        "waiting_seconds": waiting,
    }


def artifact_path_for(
    checkpoint: dict[str, Any], artifact_id: str, *, artifact_root: str | Path
) -> Path | None:
    """Resolve an artifact's on-disk path, confined to this session's directory."""
    wanted = str(artifact_id or "").strip()
    if not wanted:
        return None
    session_dir = Path(artifact_root).resolve() / _safe_component(
        str(checkpoint.get("session_id") or "")
    )
    for item in checkpoint.get("research_artifacts") or []:
        if not isinstance(item, dict) or str(item.get("id") or "") != wanted:
            continue
        try:
            resolved = Path(str(item.get("path") or "")).resolve()
        except OSError:
            return None
        if session_dir in resolved.parents and resolved.is_file():
            return resolved
        return None
    return None


SHARE_LOG_NAME = "_shares.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ShareStore:
    """Append-only token → (user_id, session_id) index for anonymous read-only shares.

    Lives at ``{CONJECTA_PROJECT_STORE_DIR}/_shares.jsonl`` (one level above the
    per-tenant roots) so anonymous lookups can resolve the owning tenant.
    Last write wins per token; revocation is a new record with ``revoked=True``.

    This store is intended for single-process or low-concurrency local deployments.
    Across multiple workers, duplicate live tokens for the same run are possible
    and are acceptable in v1 (all tokens resolve to the same run).
    """

    def __init__(self, root: str | Path | None = None) -> None:
        base = Path(root) if root is not None else _default_root()
        self.path = base.resolve() / SHARE_LOG_NAME
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._records: dict[str, dict[str, Any]] | None = None
        self._mtime = 0.0

    def _current_mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except (OSError, FileNotFoundError):
            return 0.0

    def _load_index(self) -> None:
        # Caller must hold ``self._lock``.
        records: dict[str, dict[str, Any]] = {}
        mtime = 0.0
        if self.path.exists():
            mtime = self._current_mtime()
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                token = str(row.get("token") or "")
                if token:
                    records[token] = row
        self._records = records
        self._mtime = mtime

    def _ensure_index(self) -> None:
        # Caller must hold ``self._lock``.
        current = self._current_mtime()
        if self._records is None or current > self._mtime:
            self._load_index()

    def _append(self, record: dict[str, Any]) -> None:
        # Caller must hold ``self._lock``.
        text = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(text)
        self._mtime = self._current_mtime()
        assert self._records is not None
        self._records[record["token"]] = dict(record)

    def create(self, *, user_id: str, session_id: str) -> dict[str, Any]:
        with self._lock:
            self._ensure_index()
            assert self._records is not None
            for record in self._records.values():
                if (
                    record.get("session_id") == session_id
                    and record.get("user_id") == user_id
                    and not record.get("revoked")
                ):
                    return dict(record)
            record = {
                "token": secrets.token_urlsafe(24),
                "user_id": user_id,
                "session_id": session_id,
                "created_at": _now_iso(),
                "revoked": False,
            }
            self._append(record)
            return dict(record)

    def get(self, token: str) -> dict[str, Any] | None:
        with self._lock:
            self._ensure_index()
            assert self._records is not None
            record = self._records.get(str(token or ""))
            if record is None or record.get("revoked"):
                return None
            return dict(record)

    def revoke(self, token: str, *, user_id: str) -> bool:
        with self._lock:
            self._ensure_index()
            assert self._records is not None
            record = self._records.get(str(token or ""))
            if record is None or record.get("revoked") or record.get("user_id") != user_id:
                return False
            self._append({**record, "revoked": True, "revoked_at": _now_iso()})
            return True
