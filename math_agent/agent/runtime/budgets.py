"""Budget predicates for one solve run.

Extracted verbatim from the inline checks in ``ReActAgent.solve``; the
semantics (``None``/non-positive = unlimited, per budget kind) must not
change.
"""

from __future__ import annotations

import asyncio


def wall_clock_deadline(max_wall_seconds: float) -> float:
    """Absolute loop-time deadline for a solve's wall-clock budget."""
    return asyncio.get_running_loop().time() + max(0.0, float(max_wall_seconds))


def llm_calls_exhausted(calls: int, max_llm_calls: int) -> bool:
    """Whether the per-problem LLM-call budget is spent.

    ``max_llm_calls <= 0`` means unlimited (matches the legacy inline check).
    """
    return max_llm_calls > 0 and calls >= max_llm_calls


def tool_calls_exhausted(tool_calls: int, max_tool_calls: int | None) -> bool:
    """Whether the tool-call budget is spent.

    ``max_tool_calls is None`` means unlimited.
    """
    return max_tool_calls is not None and tool_calls >= max_tool_calls
