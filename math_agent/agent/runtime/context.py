"""Mutable per-solve state shared by the solve phases.

``SolveContext`` replaces the ~40 locals that ``ReActAgent.solve`` used to
thread through its inline blocks. Every phase method on ``ReActAgent``
(intake, human-decision resume, act loop, finalize) reads and mutates this
one object. Field names mirror the original locals so the extracted code
stays a line-for-line move.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from math_agent.agent.react_state import ReActTrace
from math_agent.agent.verification import VerificationOutcome
from math_agent.types import EventCallback
from math_agent.verification import GoalEvaluator, GoalRun

if TYPE_CHECKING:
    from math_agent.agent.conclude_gate import ConcludeGate


@dataclass
class SolveContext:
    """Everything one ``solve()`` run carries between its phases."""

    # --- solve() arguments -------------------------------------------------
    problem: str
    on_checkpoint: Callable[[dict[str, Any]], None] | None = None
    attachments: list[dict] | None = None
    require_formal_verification: bool = False
    human_decision: dict[str, Any] | None = None

    # --- intake/setup (filled by _begin_solve) ------------------------------
    trace: ReActTrace | None = None
    run_log: logging.Logger = logging.getLogger("math_agent.agent")
    emit: EventCallback | None = None
    goal_run: GoalRun | None = None
    goal_evaluator: GoalEvaluator | None = None
    deadline: float = 0.0
    max_wall_seconds: float = 0.0
    max_llm_calls: int = 0
    conclude_gate: ConcludeGate | None = None

    # --- solution accumulators ----------------------------------------------
    final_answer: str = ""
    candidate_answer: str = ""
    accepted_formal_evidence_id: str = ""
    outcome: VerificationOutcome | None = None
    verification_status: str = ""
    verification_issues: list[str] = field(default_factory=list)

    # --- termination flags ---------------------------------------------------
    terminated_without_synthesis: bool = False
    wall_time_exhausted: bool = False
    llm_budget_exhausted: bool = False

    # --- loop budget/cursor state (filled by _derive_loop_state) -------------
    conclusion_revisions: int = 0
    conclusion_budget_exhausted: bool = False
    search_mathlib_count: int = 0
    tool_calls: int = 0
    next_step_num: int = 1

    def checkpoint(self) -> None:
        """Fire the checkpoint callback with the current trace snapshot.

        Mirrors ``_trace_snapshot(trace, strategy="react")`` in react_agent;
        inlined here so this module never imports back into react_agent.
        """
        if self.on_checkpoint:
            self.on_checkpoint(self.trace.to_checkpoint(strategy="react"))
