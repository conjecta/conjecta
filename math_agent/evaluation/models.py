from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


_JUDGES = frozenset({"exact", "numeric", "contains", "formal", "formal_reject"})
_NUMERIC_MATCH_MODES = frozenset({"any", "last", "all"})


@dataclass(frozen=True)
class EvalCase:
    id: str
    problem: str
    judge: str
    expected: Any = None
    require_formal_verification: bool = False
    tolerance: float = 1e-9
    tags: tuple[str, ...] = ()
    numeric_match_mode: str = "all"

    def __post_init__(self):
        if (
            self.judge == "numeric"
            and self.numeric_match_mode not in _NUMERIC_MATCH_MODES
        ):
            raise ValueError(
                f"Evaluation case {self.id!r} has unsupported numeric_match_mode "
                f"{self.numeric_match_mode!r}."
            )

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "EvalCase":
        case_id = str(raw.get("id") or "").strip()
        problem = str(raw.get("problem") or "").strip()
        judge = str(raw.get("judge") or "").strip().lower()
        if not case_id:
            raise ValueError("Evaluation case requires a non-empty id.")
        if not problem:
            raise ValueError(f"Evaluation case {case_id!r} requires a problem.")
        if judge not in _JUDGES:
            raise ValueError(
                f"Evaluation case {case_id!r} has unsupported judge {judge!r}."
            )
        expected = raw.get("expected")
        if judge not in {"formal", "formal_reject"} and (
            expected is None or expected == ""
        ):
            raise ValueError(f"Evaluation case {case_id!r} requires an expected value.")
        try:
            tolerance = float(raw.get("tolerance", 1e-9))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Evaluation case {case_id!r} has invalid tolerance."
            ) from exc
        raw_tags = raw.get("tags") or []
        if not isinstance(raw_tags, list):
            raise ValueError(f"Evaluation case {case_id!r} tags must be a list.")
        numeric_match_mode = str(raw.get("numeric_match_mode") or "all").lower()
        if numeric_match_mode not in _NUMERIC_MATCH_MODES:
            raise ValueError(
                f"Evaluation case {case_id!r} has unsupported numeric_match_mode "
                f"{numeric_match_mode!r}."
            )
        return cls(
            id=case_id,
            problem=problem,
            judge=judge,
            expected=expected,
            require_formal_verification=bool(
                raw.get("require_formal_verification", judge == "formal")
            ),
            tolerance=max(0.0, tolerance),
            tags=tuple(str(tag) for tag in raw_tags),
            numeric_match_mode=numeric_match_mode,
        )


@dataclass(frozen=True)
class TrialResult:
    case_id: str
    trial: int
    correct: bool
    final_answer: str
    verification_status: str
    lean_proof_count: int
    false_verified: bool
    latency_seconds: float
    step_count: int
    tool_call_count: int
    planned_goal_count: int = 0
    proved_goal_count: int = 0
    # prove_by_lemmas conversion for this trial: attempted = tool calls seen,
    # succeeded = calls whose tool observation reported success (i.e. the
    # assembled theorem verified in Lean).
    prove_by_lemmas_attempts: int = 0
    prove_by_lemmas_successes: int = 0
    research_goal_rounds: int = 0
    counterexample_count: int = 0
    replan_count: int = 0
    peak_parallel_goals: int = 0
    tool_call_distribution: dict[str, int] = field(default_factory=dict)
    wall_time_breakdown: dict[str, float] = field(default_factory=dict)
    tool_time_breakdown: dict[str, float] = field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    llm_calls: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "trial": self.trial,
            "correct": self.correct,
            "final_answer": self.final_answer,
            "verification_status": self.verification_status,
            "lean_proof_count": self.lean_proof_count,
            "false_verified": self.false_verified,
            "latency_seconds": self.latency_seconds,
            "step_count": self.step_count,
            "tool_call_count": self.tool_call_count,
            "planned_goal_count": self.planned_goal_count,
            "proved_goal_count": self.proved_goal_count,
            "prove_by_lemmas_attempts": self.prove_by_lemmas_attempts,
            "prove_by_lemmas_successes": self.prove_by_lemmas_successes,
            "lemma_success_rate": (
                self.prove_by_lemmas_successes / self.prove_by_lemmas_attempts
                if self.prove_by_lemmas_attempts
                else 0.0
            ),
            "research_goal_rounds": self.research_goal_rounds,
            "counterexample_count": self.counterexample_count,
            "replan_count": self.replan_count,
            "peak_parallel_goals": self.peak_parallel_goals,
            "tool_call_distribution": dict(self.tool_call_distribution),
            "wall_time_breakdown": dict(self.wall_time_breakdown),
            "tool_time_breakdown": dict(self.tool_time_breakdown),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls": self.llm_calls,
            "error": self.error,
        }


@dataclass(frozen=True)
class EvalSummary:
    case_count: int
    trial_count: int
    accuracy: float
    pass_at_k: float
    verified_count: int
    false_verified_count: int
    false_verified_rate: float
    average_latency_seconds: float
    p95_latency_seconds: float
    average_steps: float
    average_tool_calls: float
    by_tag: dict[str, dict[str, Any]] = field(default_factory=dict)
    # pass_at_1: per-case mean success rate. pass_k: the k pass_at_k uses
    # (unbiased estimator, clamped to the per-case trial count).
    pass_at_1: float = 0.0
    pass_k: int = 8
    lemma_success_rate: float = 0.0
    average_research_goal_rounds: float = 0.0
    counterexample_trigger_rate: float = 0.0
    average_peak_parallel_goals: float = 0.0
    tool_call_distribution: dict[str, int] = field(default_factory=dict)
    wall_time_breakdown: dict[str, float] = field(default_factory=dict)
    tool_time_breakdown: dict[str, float] = field(default_factory=dict)
    average_input_tokens: float = 0.0
    average_total_tokens: float = 0.0
    median_total_tokens: float = 0.0
    average_llm_calls: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "trial_count": self.trial_count,
            "accuracy": self.accuracy,
            "pass_at_k": self.pass_at_k,
            "pass_at_1": self.pass_at_1,
            "pass_k": self.pass_k,
            "verified_count": self.verified_count,
            "false_verified_count": self.false_verified_count,
            "false_verified_rate": self.false_verified_rate,
            "average_latency_seconds": self.average_latency_seconds,
            "p95_latency_seconds": self.p95_latency_seconds,
            "average_steps": self.average_steps,
            "average_tool_calls": self.average_tool_calls,
            "by_tag": self.by_tag,
            "lemma_success_rate": self.lemma_success_rate,
            "average_research_goal_rounds": self.average_research_goal_rounds,
            "counterexample_trigger_rate": self.counterexample_trigger_rate,
            "average_peak_parallel_goals": self.average_peak_parallel_goals,
            "tool_call_distribution": dict(self.tool_call_distribution),
            "wall_time_breakdown": dict(self.wall_time_breakdown),
            "tool_time_breakdown": dict(self.tool_time_breakdown),
            "average_input_tokens": self.average_input_tokens,
            "average_total_tokens": self.average_total_tokens,
            "median_total_tokens": self.median_total_tokens,
            "average_llm_calls": self.average_llm_calls,
        }
