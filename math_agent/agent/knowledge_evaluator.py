from __future__ import annotations

import json
import logging
from typing import Any

from math_agent.agent.prompts import KNOWLEDGE_EVAL_SYSTEM
from math_agent.knowledge.trust import KnowledgeStatus, KnowledgeTrustPolicy
from math_agent.llm.base import LLMBackend, Message
from math_agent.types import EventCallback

log = logging.getLogger("math_agent.agent.knowledge_evaluator")

_CATALOG_ITEM_LIMIT = 80   # max items sent to LLM per kind
_CATALOG_BODY_LIMIT = 200  # chars per item body/statement
_REVISION_FIELDS = {
    "fact": frozenset({"statement", "why", "domain", "tags"}),
    "intuition": frozenset({"title", "body", "kind", "domain", "tags"}),
    "trick": frozenset(
        {"title", "body", "category", "applicability", "failure_mode", "domain", "tags"}
    ),
}
_PROTECTED_STATUSES = frozenset({
    KnowledgeStatus.VERIFIED.value,
    KnowledgeStatus.APPROVED.value,
})
_SCOREABLE_STATUSES = frozenset({KnowledgeStatus.CANDIDATE.value}) | KnowledgeTrustPolicy.SOLVE_RETRIEVAL
# Display labels for the _apply_ops counters. The counter keys stay English —
# they are an internal data structure — so only the user-facing text is mapped.
_COUNT_LABELS = {
    "revised": "修订",
    "discarded": "弃用",
    "proposed": "新增",
    "scored": "评分",
}


def _status_of(item: dict[str, Any]) -> str:
    return str(item.get("status") or "").strip().lower()


class KnowledgeEvaluator:
    """Autonomous agent that silently improves the knowledge store.

    After each solve, the supervisor may invoke `evaluate()`. It loads the
    full catalog, asks the LLM to revise/discard/score/propose, and writes
    back the changes — all without user interaction.
    """

    def __init__(
        self,
        llm: LLMBackend,
        knowledge_store: Any | None = None,
        project_store: Any | None = None,
    ) -> None:
        self.llm = llm
        self.knowledge_store = knowledge_store  # KnowledgeStore (Supabase)
        self.project_store = project_store      # ProjectStore (JSONL)

    async def evaluate(
        self,
        project_id: str | None,
        context_hint: str = "",
        on_event: EventCallback | None = None,
    ) -> dict[str, int]:
        """Run the evaluation loop. Returns a summary dict of changes made."""
        if not project_id:
            return {}

        async def emit(event: dict[str, Any]) -> None:
            if on_event:
                await on_event(event)

        # Load catalog
        facts, intuitions, tricks = self._load_catalog(project_id)
        total = len(facts) + len(intuitions) + len(tricks)
        if total == 0:
            return {}

        await emit({
            "type": "stage_status",
            "stage": "refining",
            "message": f"正在评估 {total} 条知识…",
        })

        # Build prompt and call LLM
        prompt = self._build_prompt(facts, intuitions, tricks, context_hint)
        try:
            response = await self.llm.complete(
                [Message(role="user", content=prompt)],
                system=KNOWLEDGE_EVAL_SYSTEM,
                temperature=0.1,
            )
            raw = response.text
        except Exception as exc:
            log.warning("Knowledge evaluation LLM call failed: %s", exc)
            await emit({
                "type": "stage_status",
                "stage": "refining",
                "message": "知识评估未完成，已跳过。",
                "success": False,
            })
            return {}

        ops = _parse_ops(raw)
        counts = self._apply_ops(project_id, ops, facts, intuitions, tricks)

        summary = "，".join(
            f"{_COUNT_LABELS.get(k, k)} {v} 条" for k, v in counts.items() if v
        )
        await emit({
            "type": "stage_status",
            "stage": "refining",
            "message": f"知识已提炼：{summary or '无变化'}。",
            "success": True,
        })
        return counts

    # ------------------------------------------------------------------

    def _load_catalog(
        self, project_id: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        facts: list[dict[str, Any]] = []
        intuitions: list[dict[str, Any]] = []
        tricks: list[dict[str, Any]] = []

        store = self.knowledge_store or self.project_store
        if store is None:
            return facts, intuitions, tricks
        try:
            facts = store.list_facts(project_id, limit=_CATALOG_ITEM_LIMIT)
        except Exception as exc:
            log.debug("Could not load facts: %s", exc)
        try:
            intuitions = store.list_intuitions(project_id, limit=_CATALOG_ITEM_LIMIT)
        except Exception as exc:
            log.debug("Could not load intuitions: %s", exc)
        try:
            tricks = store.list_tricks(project_id, limit=_CATALOG_ITEM_LIMIT)
        except Exception as exc:
            log.debug("Could not load tricks: %s", exc)
        return facts, intuitions, tricks

    def _build_prompt(
        self,
        facts: list[dict[str, Any]],
        intuitions: list[dict[str, Any]],
        tricks: list[dict[str, Any]],
        context_hint: str,
    ) -> str:
        catalog: dict[str, Any] = {}
        if facts:
            catalog["facts"] = [
                {
                    "id": str(f.get("id", "")),
                    "statement": str(f.get("statement", ""))[:_CATALOG_BODY_LIMIT],
                    "why": str(f.get("why", ""))[:_CATALOG_BODY_LIMIT],
                    "score": f.get("score"),
                }
                for f in facts
            ]
        if intuitions:
            catalog["intuitions"] = [
                {
                    "id": str(i.get("id", "")),
                    "title": str(i.get("title", ""))[:_CATALOG_BODY_LIMIT],
                    "body": str(i.get("body", ""))[:_CATALOG_BODY_LIMIT],
                    "score": i.get("score"),
                }
                for i in intuitions
            ]
        if tricks:
            catalog["tricks"] = [
                {
                    "id": str(t.get("id", "")),
                    "title": str(t.get("title", ""))[:_CATALOG_BODY_LIMIT],
                    "body": str(t.get("body", ""))[:_CATALOG_BODY_LIMIT],
                    "category": str(t.get("category", ""))[:80],
                    "score": t.get("score"),
                }
                for t in tricks
            ]
        payload: dict[str, Any] = {"catalog": catalog}
        if context_hint:
            payload["context_hint"] = context_hint[:500]
        return json.dumps(payload, ensure_ascii=False)

    def _apply_ops(
        self,
        project_id: str,
        ops: dict[str, Any],
        facts: list[dict[str, Any]],
        intuitions: list[dict[str, Any]],
        tricks: list[dict[str, Any]],
    ) -> dict[str, int]:
        all_items: dict[str, dict[str, Any]] = {}
        for item in facts:
            all_items[str(item.get("id", ""))] = {**item, "_kind": "fact"}
        for item in intuitions:
            all_items[str(item.get("id", ""))] = {**item, "_kind": "intuition"}
        for item in tricks:
            all_items[str(item.get("id", ""))] = {**item, "_kind": "trick"}

        counts: dict[str, int] = {
            "revised": 0, "discarded": 0, "proposed": 0, "scored": 0
        }
        total_items = len(all_items)

        # Apply scores first (used by revisions/discards for cross-reference)
        for entry in ops.get("scores", [])[:50]:
            item_id = str(entry.get("id") or "").strip()
            score = entry.get("score")
            if not item_id or score is None:
                continue
            item = all_items.get(item_id)
            if item is None:
                continue
            status = _status_of(item) or "candidate"
            if status not in _SCOREABLE_STATUSES:
                continue
            kind = item["_kind"]
            try:
                score = float(score)
            except (TypeError, ValueError):
                continue
            score = max(0.0, min(1.0, score))
            self._set_score(project_id, item_id, kind, score)
            counts["scored"] += 1

        # Revisions
        for rev in ops.get("revisions", [])[:10]:
            item_id = str(rev.get("id") or "").strip()
            fields = rev.get("fields")
            if not item_id or not isinstance(fields, dict) or not fields:
                continue
            item = all_items.get(item_id)
            if item is None:
                continue
            if _status_of(item) in _PROTECTED_STATUSES:
                continue
            kind = str(item["_kind"])
            allowed = _REVISION_FIELDS.get(kind, frozenset())
            fields = {key: value for key, value in fields.items() if key in allowed}
            if not fields:
                continue
            self._update_item(project_id, item_id, kind, fields)
            counts["revised"] += 1

        # Discards (guard: keep at least one item; protect verified/approved)
        for discard in ops.get("discards", [])[:5]:
            item_id = str(discard.get("id") or "").strip()
            if not item_id or item_id not in all_items:
                continue
            item = all_items[item_id]
            if _status_of(item) in _PROTECTED_STATUSES:
                continue
            if total_items - counts["discarded"] <= 1:
                break
            kind = str(item["_kind"])
            self._delete_item(project_id, item_id, kind)
            counts["discarded"] += 1

        # Proposals
        for prop in ops.get("proposals", [])[:5]:
            kind = str(prop.get("kind") or "").strip()
            if kind not in ("fact", "intuition", "trick"):
                continue
            self._add_item(project_id, kind, prop)
            counts["proposed"] += 1

        return counts

    # ------------------------------------------------------------------
    # Store-agnostic write helpers
    # ------------------------------------------------------------------

    def _update_item(
        self, project_id: str, item_id: str, kind: str, fields: dict[str, Any]
    ) -> None:
        store = self.knowledge_store or self.project_store
        if store is None:
            return
        try:
            if callable(getattr(store, "update_item", None)):
                store.update_item(project_id, item_id, kind, fields)
            else:
                store.update_knowledge_item(project_id, item_id, kind, fields)
        except Exception as exc:
            log.debug("Knowledge update failed: %s", exc)

    def _delete_item(self, project_id: str, item_id: str, kind: str) -> None:
        store = self.knowledge_store or self.project_store
        if store is None:
            return
        try:
            if callable(getattr(store, "delete_item", None)):
                store.delete_item(project_id, item_id, kind)
            else:
                store.delete_knowledge_item(project_id, item_id, kind)
        except Exception as exc:
            log.debug("Knowledge delete failed: %s", exc)

    def _set_score(
        self, project_id: str, item_id: str, kind: str, score: float
    ) -> None:
        store = self.knowledge_store or self.project_store
        if store is None:
            return
        try:
            if callable(getattr(store, "set_score", None)):
                store.set_score(project_id, item_id, kind, score)
            else:
                store.update_knowledge_item(project_id, item_id, kind, {"score": score})
        except Exception as exc:
            log.debug("Knowledge score update failed: %s", exc)

    def _add_item(
        self, project_id: str, kind: str, prop: dict[str, Any]
    ) -> None:
        store = self.knowledge_store or self.project_store
        if store is None:
            return
        try:
            base = {
                "source": "knowledge_evaluator",
                "source_type": "agent_trace",
                "status": "candidate",
                "created_by": "knowledge_evaluator",
            }
            if kind == "fact":
                store.add_many(
                    project_id,
                    [{
                        **base,
                        "statement": str(
                            prop.get("statement") or prop.get("title") or ""
                        ).strip(),
                        "why": str(prop.get("why") or "").strip(),
                    }],
                    [],
                    [],
                )
            elif kind == "intuition":
                store.add_many(
                    project_id,
                    [],
                    [{
                        **base,
                        "title": str(prop.get("title") or "").strip(),
                        "body": str(prop.get("body") or "").strip(),
                        "kind": "evaluator",
                    }],
                    [],
                )
            elif kind == "trick":
                store.add_many(
                    project_id,
                    [],
                    [],
                    [{
                        **base,
                        "title": str(prop.get("title") or "").strip(),
                        "body": str(prop.get("body") or "").strip(),
                        "category": str(prop.get("category") or "").strip(),
                    }],
                )
        except Exception as exc:
            log.debug("Failed to add proposed %s: %s", kind, exc)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _parse_ops(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        raw = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:]).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(raw[start:end + 1])
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    log.warning("Knowledge evaluator could not parse LLM response")
    return {}
