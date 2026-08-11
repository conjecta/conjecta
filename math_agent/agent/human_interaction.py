from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from math_agent.agent.react_state import HumanInputRequired, ReActTrace


VALID_HUMAN_DECISIONS = frozenset({"approve", "reject", "edit", "respond"})


def hitl_should_pause(
    config: Any,
    kind: str,
    *,
    force_plan_review: bool = False,
    force_budget_extend: bool = False,
) -> bool:
    """Return whether the configured HITL policy should interrupt this run.

    ``manual`` keeps every configured checkpoint, ``adaptive`` lets routine
    planning continue autonomously but still escalates substantive blocks, and
    ``auto`` never pauses.  Unknown modes intentionally fall back to adaptive
    behavior so a typo cannot turn every research request into a hard stop.

    Research mode may pass ``force_plan_review=True`` so the first proof-graph
    review still pauses under adaptive when ``review_research_plan`` is enabled.
    Soft research-budget exhaustion may pass ``force_budget_extend=True`` so
    adaptive mode still asks whether to fund another proof segment.
    """

    if not bool(getattr(config, "enabled", False)):
        return False
    mode = str(getattr(config, "mode", "adaptive") or "adaptive").strip().lower()
    if mode == "auto":
        return False
    if kind == "budget_extend" and force_budget_extend:
        return True
    if kind == "plan_review":
        if force_plan_review and bool(getattr(config, "review_research_plan", True)):
            return True
        if mode != "manual":
            return False
    return True


def create_interaction(
    *,
    kind: str,
    question: str,
    stage: str,
    details: dict[str, Any] | None = None,
    allowed_decisions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "request_id": f"hitl-{uuid.uuid4().hex}",
        "kind": kind,
        "stage": stage,
        "question": question,
        "details": dict(details or {}),
        "allowed_decisions": allowed_decisions
        or ["approve", "reject", "edit", "respond"],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def normalize_decision(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    request_id = str(raw.get("request_id") or "").strip()
    decision = str(raw.get("decision") or "").strip().lower()
    if not request_id or decision not in VALID_HUMAN_DECISIONS:
        return None
    result: dict[str, Any] = {
        "request_id": request_id,
        "decision": decision,
        "feedback": str(raw.get("feedback") or "").strip()[:8000],
        "decided_at": datetime.now(timezone.utc).isoformat(),
    }
    edited_action = raw.get("edited_action")
    if isinstance(edited_action, dict):
        result["edited_action"] = dict(edited_action)
    return result


def matching_decision(
    trace: ReActTrace, raw: Any
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    pending = trace.pending_interaction
    decision = normalize_decision(raw)
    if not pending or decision is None:
        return None
    if decision["request_id"] != str(pending.get("request_id") or ""):
        return None
    allowed = pending.get("allowed_decisions") or []
    if decision["decision"] not in allowed:
        return None
    return pending, decision


def record_decision(trace: ReActTrace, decision: dict[str, Any]) -> None:
    trace.human_decisions.append(dict(decision))
    trace.pending_interaction = None


def pause_for_human(trace: ReActTrace, interaction: dict[str, Any]) -> None:
    trace.pending_interaction = dict(interaction)
    raise HumanInputRequired(interaction)


def interaction_event(
    interaction: dict[str, Any], *, checkpoint_id: str
) -> dict[str, Any]:
    return {
        "type": "human_input_required",
        "checkpoint_id": checkpoint_id,
        "resumable": True,
        **dict(interaction),
    }
