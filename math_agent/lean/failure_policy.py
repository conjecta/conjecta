"""Canonical Lean failure-kind -> recovery-policy mapping.

Single source of truth for how a Lean ``failure_kind`` (see
``LeanDiagnosticKind`` in ``math_agent.lean.verifier``) should be handled by
the codegen repair loop, the lemma executor, and the supervisor escalation
router. Policies:

- ``retry``:  infrastructure failure; rerunning the same approach may succeed.
- ``repair``: coding-level failure; the proof strategy may be fine, fix the draft.
- ``abort``:  unrecoverable; stop spending budget on this approach.
- ``replan``: fallback for anything not classified above (the proof strategy
  itself is suspect and a new plan is needed).
"""
from __future__ import annotations

from math_agent.lean.verifier import LeanDiagnosticKind

LeanFailurePolicy = str  # "retry" | "repair" | "replan" | "abort"

RETRY_LEAN_FAILURES = frozenset(
    {
        LeanDiagnosticKind.TIMEOUT.value,
        LeanDiagnosticKind.LEAN_UNAVAILABLE.value,
    }
)

REPAIR_LEAN_FAILURES = frozenset(
    {
        LeanDiagnosticKind.BAD_IMPORT.value,
        LeanDiagnosticKind.SYNTAX.value,
        LeanDiagnosticKind.UNKNOWN_CONSTANT.value,
        LeanDiagnosticKind.TYPE_MISMATCH.value,
        LeanDiagnosticKind.MISSING_INSTANCE.value,
        LeanDiagnosticKind.LEAN_ERROR.value,
        LeanDiagnosticKind.UNSOLVED_GOALS.value,
    }
)

ABORT_LEAN_FAILURES = frozenset({LeanDiagnosticKind.TERMINATION.value})


def lean_failure_policy(failure_kind: str | None) -> LeanFailurePolicy:
    """Map a Lean failure_kind to its recovery policy.

    ``None`` (unclassified failure) is treated as repairable, matching the
    historical codegen behavior.
    """
    if failure_kind is None:
        return "repair"
    if failure_kind in RETRY_LEAN_FAILURES:
        return "retry"
    if failure_kind in REPAIR_LEAN_FAILURES:
        return "repair"
    if failure_kind in ABORT_LEAN_FAILURES:
        return "abort"
    return "replan"
