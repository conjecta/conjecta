"""Consistency tests for the canonical Lean failure-kind -> policy mapping.

math_agent/lean/failure_policy.py is the single source of truth; the codegen
repair loop, the lemma executor, and the supervisor escalation router must all
derive from it (no drifted local copies).
"""
from __future__ import annotations

import inspect

from math_agent.agent import supervisor
from math_agent.lean.codegen import _is_repairable
from math_agent.lean.failure_policy import (
    ABORT_LEAN_FAILURES,
    REPAIR_LEAN_FAILURES,
    RETRY_LEAN_FAILURES,
    lean_failure_policy,
)
from math_agent.lean.verifier import LeanDiagnosticKind

_EXPECTED = {
    LeanDiagnosticKind.TIMEOUT: "retry",
    LeanDiagnosticKind.LEAN_UNAVAILABLE: "retry",
    LeanDiagnosticKind.BAD_IMPORT: "repair",
    LeanDiagnosticKind.SYNTAX: "repair",
    LeanDiagnosticKind.UNKNOWN_CONSTANT: "repair",
    LeanDiagnosticKind.TYPE_MISMATCH: "repair",
    LeanDiagnosticKind.MISSING_INSTANCE: "repair",
    LeanDiagnosticKind.LEAN_ERROR: "repair",
    LeanDiagnosticKind.UNSOLVED_GOALS: "repair",
    LeanDiagnosticKind.TERMINATION: "abort",
    LeanDiagnosticKind.PLACEHOLDER: "replan",
    LeanDiagnosticKind.UNSAFE_SOURCE: "replan",
}


def test_every_diagnostic_kind_maps_to_exactly_one_policy():
    policies = set()
    for kind in LeanDiagnosticKind:
        policy = lean_failure_policy(kind.value)
        assert policy in {"retry", "repair", "replan", "abort"}
        policies.add(policy)
        # Exactly one policy: the kind's value lives in at most one named set.
        membership = [
            kind.value in RETRY_LEAN_FAILURES,
            kind.value in REPAIR_LEAN_FAILURES,
            kind.value in ABORT_LEAN_FAILURES,
        ]
        assert sum(membership) <= 1
    assert policies == {"retry", "repair", "replan", "abort"}


def test_canonical_mapping_matches_spec():
    for kind, expected in _EXPECTED.items():
        assert lean_failure_policy(kind.value) == expected, kind
    # Unclassified failures are repairable (historical codegen behavior).
    assert lean_failure_policy(None) == "repair"


def test_codegen_repair_set_agrees_with_canonical_mapping():
    candidates = [kind.value for kind in LeanDiagnosticKind] + [None]
    for kind in candidates:
        assert _is_repairable(kind) == (lean_failure_policy(kind) == "repair"), kind


def test_supervisor_derives_from_canonical_mapping():
    """The supervisor must not keep drifted literal failure-kind sets."""
    assert not hasattr(supervisor, "_INFRA_LEAN_FAILURES")
    assert not hasattr(supervisor, "_REPAIR_LEAN_FAILURES")
    source = inspect.getsource(supervisor)
    assert "lean_failure_policy" in source
