from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from math_agent.agent.proof_graph import ProofGraph
from math_agent.agent.research_artifacts import ACCEPTED_ARTIFACT_STATUSES
from math_agent.agent.verification import VerificationOutcome


CONTEXT_PREAMBLE_MAX_CHARS = 12_000

PLAN_ITEMS_MAX = 20
PLAN_ITEM_STATUSES = ("pending", "in_progress", "done")


def normalize_plan_items(raw: Any, *, max_items: int = PLAN_ITEMS_MAX) -> list[dict[str, str]]:
    """Coerce a raw update_plan payload into ``{content, status}`` dicts.

    Entries without a non-empty content string are dropped, unknown statuses
    fall back to "pending", and the list is truncated to ``max_items``.
    """
    if not isinstance(raw, list):
        return []
    items: list[dict[str, str]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        content = str(entry.get("content") or "").strip()
        if not content:
            continue
        status = str(entry.get("status") or "pending").strip()
        if status not in PLAN_ITEM_STATUSES:
            status = "pending"
        items.append({"content": content, "status": status})
        if len(items) >= max_items:
            break
    return items


def _clip_head_tail(text: str, limit: int) -> str:
    value = text or ""
    if limit <= 0:
        return ""
    if len(value) <= limit:
        return value
    marker = "\n[... clipped ...]\n"
    if limit <= len(marker) + 2:
        return value[:limit]
    head = (limit - len(marker)) * 3 // 4
    tail = limit - len(marker) - head
    return value[:head] + marker + value[-tail:]


def _clip_context_preamble(text: str) -> str:
    value = text or ""
    if len(value) <= CONTEXT_PREAMBLE_MAX_CHARS:
        return value
    marker = "\n\n[... context truncated ...]"
    return value[: CONTEXT_PREAMBLE_MAX_CHARS - len(marker)] + marker


@dataclass(frozen=True)
class Action:
    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolObservation:
    success: bool
    output: str
    lean_code: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewResult:
    reviewer: str
    verdict: str  # "PASS", "FAIL", or "UNAVAILABLE" (reviewer abstained)
    issues: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def abstained(self) -> bool:
        """True when the reviewer could not run and casts no vote."""
        return self.verdict.upper() == "UNAVAILABLE"


@dataclass
class ProjectContext:
    project_id: str | None = None
    user_id: str | None = None
    facts: list[dict[str, Any]] = field(default_factory=list)
    intuitions: list[dict[str, Any]] = field(default_factory=list)
    tricks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ReActTurn:
    thought: str
    action: Action
    observation: ToolObservation
    reviews: list[ReviewResult] = field(default_factory=list)
    step_num: int = 0


@dataclass
class ReActTrace:
    problem: str
    turns: list[ReActTurn] = field(default_factory=list)
    current_goal: str = ""
    project_context: ProjectContext = field(default_factory=ProjectContext)
    plan_text: str = ""
    plan_items: list[dict[str, Any]] = field(default_factory=list)
    formalization_plan: dict[str, Any] = field(default_factory=dict)
    context_preamble: str = ""
    claim_check: dict[str, Any] = field(default_factory=dict)
    mid_verifications: list[dict[str, Any]] = field(default_factory=list)
    next_step_num: int = 1
    budget_consumption: dict[str, int] = field(default_factory=dict)
    proof_graph: ProofGraph = field(default_factory=ProofGraph)
    research_mode: bool = False
    research_artifacts: list[dict[str, Any]] = field(default_factory=list)
    research_failures: list[dict[str, Any]] = field(default_factory=list)
    research_goal_rounds: dict[str, int] = field(default_factory=dict)
    research_goal_search_rounds: dict[str, int] = field(default_factory=dict)
    research_metrics: dict[str, Any] = field(default_factory=dict)
    tool_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    compacted_summary: str = ""
    compacted_turn_count: int = 0
    pending_interaction: dict[str, Any] | None = None
    human_decisions: list[dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.context_preamble = _clip_context_preamble(self.context_preamble)
        self.next_step_num = max(1, _coerce_int(self.next_step_num, default=1))
        self.budget_consumption = _normalize_budget_consumption(self.budget_consumption)
        self.plan_items = normalize_plan_items(self.plan_items)
        self.compacted_turn_count = max(
            0, _coerce_int(self.compacted_turn_count, default=0)
        )

    def context_window(self, max_turns: int = 10, *, max_chars: int = 24_000) -> str:
        recent = self.turns[-max_turns:]
        required = [f"Problem: {_clip_head_tail(self.problem, 6000)}"]
        if self.current_goal:
            required.append(f"Current goal: {_clip_head_tail(self.current_goal, 2500)}")
        if self.plan_text:
            required.append(f"Plan:\n{_clip_head_tail(self.plan_text, 2500)}")
        if self.plan_items:
            lines = ["Todo checklist:"]
            for item in self.plan_items[:PLAN_ITEMS_MAX]:
                lines.append(
                    f"- [{item.get('status', 'pending')}] "
                    f"{_clip_head_tail(str(item.get('content') or ''), 200)}"
                )
            required.append("\n".join(lines))
        graph_context = self.proof_graph.context_block()
        if graph_context:
            required.append(_clip_head_tail(graph_context, 3000))
        if self.research_mode:
            accepted = [
                item
                for item in self.research_artifacts
                if str(item.get("status") or "") in ACCEPTED_ARTIFACT_STATUSES
            ][-6:]
            if accepted:
                lines = ["Accepted lemma artifacts:"]
                for item in accepted:
                    lines.append(
                        "- "
                        f"{item.get('id', '')}: {str(item.get('goal_statement') or '')[:350]} "
                        f"— {str(item.get('summary') or item.get('answer') or '')[:500]}"
                    )
                required.append("\n".join(lines))
            active_goal_id = self.proof_graph.active_goal_id
            relevant_failures = [
                item
                for item in self.research_failures
                if not active_goal_id or item.get("goal_id") == active_goal_id
            ][-4:]
            if relevant_failures:
                required.append(
                    "Relevant failed attempts:\n"
                    + "\n".join(
                        f"- {str(item.get('summary') or item.get('reason') or '')[:400]}"
                        for item in relevant_failures
                    )
                )

        optional: list[str] = []
        if self.context_preamble:
            optional.append(
                "Context supplied with the problem:\n"
                f"{_clip_head_tail(_clip_context_preamble(self.context_preamble), 3000)}"
            )
        if self.compacted_summary:
            optional.append(f"Earlier work summary:\n{self.compacted_summary[:1500]}")

        reserve_for_turns = min(6000, max(1500, max_chars // 3))
        essential_limit = max(1000, max_chars - reserve_for_turns)
        parts: list[str] = []
        for block in [*required, *optional]:
            remaining = essential_limit - len("\n".join(parts))
            if remaining <= 0:
                break
            if len(block) > remaining:
                if block in required:
                    parts.append(_clip_head_tail(block, remaining))
                break
            parts.append(block)

        essential = "\n".join(parts)
        remaining = max(0, max_chars - len(essential) - 1)
        turn_blocks: list[str] = []
        for turn in reversed(recent):
            lines = [
                f"Step {turn.step_num} [thought]: {_clip_head_tail(turn.thought, 1200)}",
                f"Step {turn.step_num} [action]: {turn.action.name}({_clip_head_tail(str(turn.action.args), 600)})",
                f"Step {turn.step_num} [observation]: {_clip_head_tail(turn.observation.output, 800)}",
            ]
            for review in turn.reviews:
                marker = (
                    review.verdict.upper()
                    if review.verdict.upper() in {"PASS", "FAIL", "UNAVAILABLE"}
                    else "FAIL"
                )
                lines.append(f"  Review ({review.reviewer}): {marker}")
                if review.issues:
                    lines.append(
                        "    Issues: " + _clip_head_tail("; ".join(review.issues), 600)
                    )
            block = "\n".join(lines)
            if len(block) + 1 > remaining:
                break
            turn_blocks.append(block)
            remaining -= len(block) + 1
        turn_blocks.reverse()
        return "\n".join([essential, *turn_blocks]).strip()

    def last_turn(self) -> ReActTurn | None:
        return self.turns[-1] if self.turns else None

    def to_checkpoint(self, *, strategy: str = "react") -> dict[str, Any]:
        """Serialize the complete resumable trace without discarding evidence."""
        inferred_next_step = max((turn.step_num for turn in self.turns), default=0) + 1
        self.next_step_num = max(self.next_step_num, inferred_next_step)
        return {
            "schema_version": 4,
            "strategy": strategy,
            "problem": self.problem,
            "current_goal": self.current_goal,
            "plan_text": self.plan_text,
            "plan_items": [dict(item) for item in self.plan_items],
            "formalization_plan": dict(self.formalization_plan),
            "context_preamble": _clip_context_preamble(self.context_preamble),
            "claim_check": dict(self.claim_check) if self.claim_check else {},
            "mid_verifications": [dict(item) for item in self.mid_verifications],
            "next_step_num": self.next_step_num,
            "budget_consumption": dict(self.budget_consumption),
            "proof_graph": self.proof_graph.to_dict(),
            "research_mode": self.research_mode,
            "research_artifacts": [dict(item) for item in self.research_artifacts],
            "research_failures": [dict(item) for item in self.research_failures],
            "research_goal_rounds": dict(self.research_goal_rounds),
            "research_goal_search_rounds": dict(self.research_goal_search_rounds),
            "research_metrics": dict(self.research_metrics),
            "tool_stats": {name: dict(stats) for name, stats in self.tool_stats.items()},
            "compacted_summary": self.compacted_summary,
            "compacted_turn_count": self.compacted_turn_count,
            "pending_interaction": (
                dict(self.pending_interaction) if self.pending_interaction else None
            ),
            "human_decisions": [dict(item) for item in self.human_decisions],
            "project_context": {
                "project_id": self.project_context.project_id,
                "user_id": self.project_context.user_id,
                "facts": list(self.project_context.facts),
                "intuitions": list(self.project_context.intuitions),
                "tricks": list(self.project_context.tricks),
            },
            "turns": [_turn_to_checkpoint(turn) for turn in self.turns],
        }

    @classmethod
    def from_checkpoint(
        cls,
        checkpoint: dict[str, Any],
        *,
        project_context: ProjectContext | None = None,
    ) -> ReActTrace:
        """Hydrate a trace, accepting both complete and legacy snapshots."""
        if not isinstance(checkpoint, dict):
            raise ValueError("Checkpoint must be a mapping.")

        schema_version = checkpoint.get("schema_version")
        if schema_version not in {None, 2, 3, 4}:
            raise ValueError(
                f"Unsupported checkpoint schema version: {schema_version!r}"
            )

        raw_problem = checkpoint.get("problem")
        if not isinstance(raw_problem, str):
            raise ValueError("Malformed checkpoint: problem must be a string.")

        raw_turns = checkpoint.get("turns", [])
        if not isinstance(raw_turns, list):
            raise ValueError("Malformed checkpoint: turns must be a list.")
        for index, raw_turn in enumerate(raw_turns):
            _validate_checkpoint_turn(raw_turn, index=index)

        raw_context = checkpoint.get("project_context", {})
        if not isinstance(raw_context, dict):
            raise ValueError("Malformed checkpoint: project_context must be an object.")
        for field_name in ("facts", "intuitions", "tricks"):
            raw_items = raw_context.get(field_name, [])
            if not isinstance(raw_items, list):
                raise ValueError(
                    f"Malformed checkpoint: project_context.{field_name} must be a list."
                )

        budgets = checkpoint.get("budget_consumption", checkpoint.get("budgets", {}))
        if not isinstance(budgets, dict):
            raise ValueError(
                "Malformed checkpoint: budget consumption must be an object."
            )
        raw_pending = checkpoint.get("pending_interaction")
        if raw_pending is not None and not isinstance(raw_pending, dict):
            raise ValueError(
                "Malformed checkpoint: pending_interaction must be an object."
            )
        raw_decisions = checkpoint.get("human_decisions", [])
        if not isinstance(raw_decisions, list):
            raise ValueError("Malformed checkpoint: human_decisions must be a list.")
        raw_research_metrics = checkpoint.get("research_metrics", {})
        if not isinstance(raw_research_metrics, dict):
            raise ValueError(
                "Malformed checkpoint: research_metrics must be an object."
            )
        raw_claim_check = checkpoint.get("claim_check", {})
        if raw_claim_check is None:
            raw_claim_check = {}
        if not isinstance(raw_claim_check, dict):
            raise ValueError("Malformed checkpoint: claim_check must be an object.")
        raw_formalization_plan = checkpoint.get("formalization_plan")
        if raw_formalization_plan is not None and not isinstance(
            raw_formalization_plan, dict
        ):
            raise ValueError(
                "Malformed checkpoint: formalization_plan must be an object."
            )

        turns = [_turn_from_checkpoint(entry) for entry in raw_turns]
        inferred_next_step = max((turn.step_num for turn in turns), default=0) + 1
        raw_next_step = checkpoint.get(
            "next_step_num",
            checkpoint.get("next_step", inferred_next_step),
        )
        if project_context is None:
            project_context = ProjectContext(
                project_id=raw_context.get("project_id"),
                user_id=raw_context.get("user_id"),
                facts=list(raw_context.get("facts") or []),
                intuitions=list(raw_context.get("intuitions") or []),
                tricks=list(raw_context.get("tricks") or []),
            )
        return cls(
            problem=raw_problem,
            turns=turns,
            current_goal=str(checkpoint.get("current_goal") or ""),
            project_context=project_context,
            plan_text=str(checkpoint.get("plan_text") or ""),
            plan_items=normalize_plan_items(checkpoint.get("plan_items")),
            formalization_plan=(
                dict(raw_formalization_plan) if raw_formalization_plan else {}
            ),
            context_preamble=str(checkpoint.get("context_preamble") or ""),
            claim_check=dict(raw_claim_check),
            mid_verifications=[
                dict(item)
                for item in (checkpoint.get("mid_verifications") or [])
                if isinstance(item, dict)
            ],
            next_step_num=max(
                inferred_next_step,
                _coerce_int(raw_next_step, default=inferred_next_step),
            ),
            budget_consumption=_normalize_budget_consumption(budgets),
            proof_graph=ProofGraph.from_dict(checkpoint.get("proof_graph")),
            research_mode=bool(checkpoint.get("research_mode", False)),
            research_artifacts=[
                dict(item)
                for item in (checkpoint.get("research_artifacts") or [])
                if isinstance(item, dict)
            ],
            research_failures=[
                dict(item)
                for item in (checkpoint.get("research_failures") or [])
                if isinstance(item, dict)
            ],
            research_goal_rounds=_normalize_budget_consumption(
                checkpoint.get("research_goal_rounds")
            ),
            research_goal_search_rounds=_normalize_budget_consumption(
                checkpoint.get("research_goal_search_rounds")
            ),
            research_metrics=dict(raw_research_metrics),
            tool_stats={
                str(name): dict(stats)
                for name, stats in (checkpoint.get("tool_stats") or {}).items()
                if isinstance(stats, dict)
            },
            compacted_summary=str(checkpoint.get("compacted_summary") or ""),
            compacted_turn_count=max(
                0, _coerce_int(checkpoint.get("compacted_turn_count"), default=0)
            ),
            pending_interaction=(dict(raw_pending) if raw_pending else None),
            human_decisions=[
                dict(item) for item in raw_decisions if isinstance(item, dict)
            ],
        )


class HumanInputRequired(RuntimeError):
    """Control-flow signal indicating a durable run pause, not a solve error."""

    def __init__(self, interaction: dict[str, Any]) -> None:
        super().__init__(str(interaction.get("question") or "Human input required."))
        self.interaction = dict(interaction)


def _validate_checkpoint_turn(raw: Any, *, index: int) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"Malformed checkpoint: turn {index} must be an object.")
    raw_reviews = raw.get("reviews", [])
    if not isinstance(raw_reviews, list):
        raise ValueError(f"Malformed checkpoint: turn {index} reviews must be a list.")
    for review_index, raw_review in enumerate(raw_reviews):
        if not isinstance(raw_review, dict):
            raise ValueError(
                "Malformed checkpoint: "
                f"turn {index} review {review_index} must be an object."
            )
        for field_name in ("issues", "suggestions"):
            raw_items = raw_review.get(field_name, [])
            if not isinstance(raw_items, list):
                raise ValueError(
                    "Malformed checkpoint: "
                    f"turn {index} review {review_index} {field_name} must be a list."
                )


def _coerce_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_budget_consumption(raw: Any) -> dict[str, int]:
    if not isinstance(raw, dict):
        return {}
    return {str(key): max(0, _coerce_int(value)) for key, value in raw.items()}


def _turn_to_checkpoint(turn: ReActTurn) -> dict[str, Any]:
    return {
        "step_num": turn.step_num,
        "thought": turn.thought,
        "action": {
            "name": turn.action.name,
            "args": dict(turn.action.args),
        },
        "observation": {
            "success": turn.observation.success,
            "output": turn.observation.output,
            "lean_code": turn.observation.lean_code,
            "error": turn.observation.error,
            "metadata": dict(turn.observation.metadata),
        },
        "reviews": [
            {
                "reviewer": review.reviewer,
                "verdict": review.verdict,
                "issues": list(review.issues),
                "suggestions": list(review.suggestions),
                "confidence": review.confidence,
            }
            for review in turn.reviews
        ],
    }


def _turn_from_checkpoint(raw: dict[str, Any]) -> ReActTurn:
    raw_action = raw.get("action") or {}
    if isinstance(raw_action, dict):
        action_name = str(raw_action.get("name") or "think")
        action_args = raw_action.get("args") or {}
        if not isinstance(action_args, dict):
            action_args = {"value": action_args}
    else:
        action_name = str(raw_action or "think")
        action_args = {}

    raw_observation = raw.get("observation")
    if isinstance(raw_observation, dict):
        observation = ToolObservation(
            success=bool(raw_observation.get("success", True)),
            output=str(raw_observation.get("output") or ""),
            lean_code=raw_observation.get("lean_code"),
            error=raw_observation.get("error"),
            metadata=(
                dict(raw_observation.get("metadata") or {})
                if isinstance(raw_observation.get("metadata"), dict)
                else {}
            ),
        )
    else:
        observation = ToolObservation(
            success=bool(raw.get("success", True)),
            output=str(raw_observation or ""),
            lean_code=raw.get("lean_code"),
            error=raw.get("error"),
            metadata=(
                dict(raw.get("metadata") or {})
                if isinstance(raw.get("metadata"), dict)
                else {}
            ),
        )

    reviews: list[ReviewResult] = []
    for raw_review in raw.get("reviews") or []:
        if not isinstance(raw_review, dict):
            continue
        try:
            confidence = float(raw_review.get("confidence") or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0
        reviews.append(
            ReviewResult(
                reviewer=str(raw_review.get("reviewer") or "unknown"),
                verdict=str(raw_review.get("verdict") or "FAIL"),
                issues=[str(item) for item in (raw_review.get("issues") or [])],
                suggestions=[
                    str(item) for item in (raw_review.get("suggestions") or [])
                ],
                confidence=confidence,
            )
        )

    return ReActTurn(
        thought=str(raw.get("thought") or ""),
        action=Action(name=action_name, args=dict(action_args)),
        observation=observation,
        reviews=reviews,
        step_num=max(0, _coerce_int(raw.get("step_num"))),
    )


def best_effort_answer(trace: ReActTrace) -> str:
    """Return the best available answer from a trace that may not have concluded."""
    for turn in reversed(trace.turns):
        if turn.action.name == "conclude":
            return turn.action.args.get("answer", "")
    if trace.turns:
        return trace.turns[-1].thought
    return "No solution found."


@dataclass
class ReActSolution:
    problem: str
    turns: list[ReActTurn]
    final_answer: str
    lean_proofs: list[str] = field(default_factory=list)
    verification_status: str = "best_effort"
    verification_issues: list[str] = field(default_factory=list)
    trace: ReActTrace | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    # Total LLM calls consumed by this solve (actor + critic + codegen +
    # tactic generation), counted via the shared counter in the LLM layer.
    llm_call_count: int = 0
    verification_outcome: VerificationOutcome | None = field(default=None, compare=False)

    def summary(self) -> str:
        parts = [f"Problem: {self.problem}\n"]
        for turn in self.turns:
            marker = "✓" if all(r.verdict == "PASS" for r in turn.reviews) else "○"
            parts.append(f"  {marker} Step {turn.step_num}: {turn.thought[:120]}")
        parts.append(f"\nAnswer: {self.final_answer}")
        if self.lean_proofs:
            parts.append(f"\nLean proofs verified: {len(self.lean_proofs)}")
        return "\n".join(parts)
