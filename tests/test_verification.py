import pytest

from math_agent.agent.verification import (
    Completion,
    Fidelity,
    Formal,
    Review,
    VerificationOutcome,
    from_legacy_label,
    is_completed,
    is_review_backed,
    legacy_label,
    outcome_best_effort,
    outcome_blocked,
    outcome_reviewed,
    outcome_unreviewed,
    outcome_verified,
)

# (factory outcome, expected legacy label)
FACTORY_LABEL_CASES = [
    (outcome_verified(), "verified"),
    (outcome_reviewed(), "reviewed"),
    (outcome_unreviewed(), "unreviewed"),
    (outcome_blocked(), "blocked"),
    (outcome_best_effort(), "best_effort"),
]


@pytest.mark.parametrize("outcome,label", FACTORY_LABEL_CASES)
def test_legacy_label_matches_current_mapping(outcome, label):
    assert legacy_label(outcome) == label


def test_verified_requires_fidelity_not_failed():
    good = VerificationOutcome(
        Completion.COMPLETED, Review.SKIPPED, Formal.VERIFIED, Fidelity.PASSED
    )
    bad = VerificationOutcome(
        Completion.COMPLETED, Review.SKIPPED, Formal.VERIFIED, Fidelity.FAILED
    )
    assert legacy_label(good) == "verified"
    assert legacy_label(bad) != "verified"


def test_blocked_and_incomplete_take_precedence():
    o = VerificationOutcome(
        Completion.BLOCKED, Review.PASSED, Formal.VERIFIED, Fidelity.PASSED
    )
    assert legacy_label(o) == "blocked"
    o2 = VerificationOutcome(
        Completion.INCOMPLETE, Review.PASSED, Formal.VERIFIED, Fidelity.PASSED
    )
    assert legacy_label(o2) == "best_effort"


@pytest.mark.parametrize(
    "label,completed,backed",
    [
        ("verified", True, True),
        ("reviewed", True, True),
        ("unreviewed", True, False),
        ("best_effort", False, False),
        ("blocked", False, False),
        ("proved", True, True),  # proof-graph vocab that reaches gates as evidence
    ],
)
def test_from_legacy_label_predicates(label, completed, backed):
    o = from_legacy_label(label)
    assert is_completed(o) is completed
    assert is_review_backed(o) is backed


def test_is_review_backed_true_set_equals_reviewed_verified():
    backed = {
        lbl
        for lbl in ("verified", "reviewed", "unreviewed", "best_effort", "blocked")
        if is_review_backed(from_legacy_label(lbl))
    }
    assert backed == {"verified", "reviewed"}
