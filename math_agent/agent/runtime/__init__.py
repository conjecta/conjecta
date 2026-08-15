"""Per-solve runtime state extracted from ``ReActAgent.solve``.

The solve flow is organized as explicit phases (intake, human-decision
resume, act loop, finalize) that share one mutable :class:`SolveContext`
instead of ~40 scattered locals. Behavior is unchanged: this package only
holds the state and budget predicates.
"""

from math_agent.agent.runtime.budgets import (
    llm_calls_exhausted,
    tool_calls_exhausted,
    wall_clock_deadline,
)
from math_agent.agent.runtime.context import SolveContext

__all__ = [
    "SolveContext",
    "llm_calls_exhausted",
    "tool_calls_exhausted",
    "wall_clock_deadline",
]
