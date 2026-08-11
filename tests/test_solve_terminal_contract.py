"""Contract tests for solve terminal verification statuses and done events."""

from __future__ import annotations

import pytest

from math_agent.agent.verification import (
    TERMINAL_VERIFICATION_STATUSES,
    from_legacy_label,
    legacy_label,
    outcome_best_effort,
    outcome_blocked,
    outcome_reviewed,
    outcome_unreviewed,
    outcome_verified,
)
from math_agent.knowledge.trust import KnowledgeTrustPolicy, ReviewQueueStatus


@pytest.mark.parametrize(
    ("outcome", "label"),
    [
        (outcome_verified(("ev-1",)), "verified"),
        (outcome_reviewed(), "reviewed"),
        (outcome_unreviewed(), "unreviewed"),
        (outcome_best_effort(("budget",)), "best_effort"),
        (outcome_blocked(("reviewer",)), "blocked"),
    ],
)
def test_terminal_labels_are_exactly_the_public_five(outcome, label):
    assert label in TERMINAL_VERIFICATION_STATUSES
    assert legacy_label(outcome) == label
    assert legacy_label(from_legacy_label(label)) == label


def test_terminal_set_has_no_extra_or_missing_labels():
    assert TERMINAL_VERIFICATION_STATUSES == frozenset({
        "verified",
        "reviewed",
        "unreviewed",
        "best_effort",
        "blocked",
    })


def test_done_event_shape_accepts_each_terminal_status():
    for status in sorted(TERMINAL_VERIFICATION_STATUSES):
        event = {
            "type": "done",
            "summary": "answer",
            "final_answer": "answer",
            "lean_proofs": [],
            "strategy": "normal",
            "verification_status": status,
            "verification_issues": [],
        }
        assert event["verification_status"] in TERMINAL_VERIFICATION_STATUSES
        assert event["type"] == "done"


def test_knowledge_trust_policy_is_single_source():
    assert KnowledgeTrustPolicy.SOLVE_RETRIEVAL == frozenset({
        "approved",
        "reviewed",
        "verified",
    })
    assert KnowledgeTrustPolicy.REVIEW_QUEUE == frozenset(
        status.value for status in ReviewQueueStatus
    )
    assert KnowledgeTrustPolicy.admits_for_solve("reviewed")
    assert not KnowledgeTrustPolicy.admits_for_solve("candidate")
    assert not KnowledgeTrustPolicy.admits_for_solve("open")
