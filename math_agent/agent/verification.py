"""单一真相源:solve 终态的正交证据协议。

四个正交维度 + 派生旧字符串标签 + 命名谓词。纯函数,无项目内依赖。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


# Public wire labels for terminal solve events (done / blocked exits).
TERMINAL_VERIFICATION_STATUSES: frozenset[str] = frozenset({
    "verified",
    "reviewed",
    "unreviewed",
    "best_effort",
    "blocked",
})


class Completion(str, Enum):
    COMPLETED = "completed"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"


class Review(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NOT_APPLICABLE = "not_applicable"


class Formal(str, Enum):
    VERIFIED = "verified"
    FAILED = "failed"
    NOT_ATTEMPTED = "not_attempted"
    UNAVAILABLE = "unavailable"


class Fidelity(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNCERTAIN = "uncertain"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class VerificationOutcome:
    """Construct via the outcome_* factory functions or from_legacy_label; the is_* predicates assume factory-coherent field combinations and do not cross-check dimensions."""
    completion: Completion
    review: Review
    formal: Formal
    fidelity: Fidelity
    evidence_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()


def outcome_verified(evidence_ids: tuple[str, ...] = ()) -> VerificationOutcome:
    return VerificationOutcome(
        Completion.COMPLETED, Review.SKIPPED, Formal.VERIFIED, Fidelity.PASSED,
        evidence_ids=tuple(evidence_ids),
    )


def outcome_reviewed() -> VerificationOutcome:
    return VerificationOutcome(
        Completion.COMPLETED, Review.PASSED, Formal.NOT_ATTEMPTED, Fidelity.NOT_APPLICABLE
    )


def outcome_unreviewed() -> VerificationOutcome:
    return VerificationOutcome(
        Completion.COMPLETED, Review.SKIPPED, Formal.NOT_ATTEMPTED, Fidelity.NOT_APPLICABLE
    )


def outcome_blocked(limitations: tuple[str, ...] = ()) -> VerificationOutcome:
    return VerificationOutcome(
        Completion.BLOCKED, Review.NOT_APPLICABLE, Formal.NOT_ATTEMPTED,
        Fidelity.NOT_APPLICABLE, limitations=tuple(limitations),
    )


def outcome_best_effort(limitations: tuple[str, ...] = ()) -> VerificationOutcome:
    return VerificationOutcome(
        Completion.INCOMPLETE, Review.NOT_APPLICABLE, Formal.NOT_ATTEMPTED,
        Fidelity.NOT_APPLICABLE, limitations=tuple(limitations),
    )


def legacy_label(o: VerificationOutcome) -> str:
    if o.completion is Completion.BLOCKED:
        return "blocked"
    if o.completion is Completion.INCOMPLETE:
        return "best_effort"
    if o.formal is Formal.VERIFIED and o.fidelity is not Fidelity.FAILED:
        return "verified"
    if o.review is Review.PASSED:
        return "reviewed"
    if o.review is Review.SKIPPED:
        return "unreviewed"
    return "best_effort"


def from_legacy_label(label: str) -> VerificationOutcome:
    """把门控读到的旧字符串还原成 outcome,使谓词给出正确答案。

    仅需谓词正确,不要求与 legacy_label 严格互逆。`proved`(proof-graph 词表)
    在少数门控里等同 formal verified。
    """
    key = (label or "").strip().lower()
    if key == "blocked":
        return outcome_blocked()
    if key in ("best_effort", "", "failed"):
        return outcome_best_effort()
    if key in ("verified", "proved"):
        return outcome_verified()
    if key == "reviewed":
        return outcome_reviewed()
    if key == "unreviewed":
        return outcome_unreviewed()
    return outcome_best_effort()


def is_completed(o: VerificationOutcome) -> bool:
    return o.completion is Completion.COMPLETED


def is_review_backed(o: VerificationOutcome) -> bool:
    return o.formal is Formal.VERIFIED or o.review is Review.PASSED
