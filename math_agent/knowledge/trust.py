"""Single source of truth for knowledge and review-queue trust statuses."""

from __future__ import annotations

from enum import Enum


class KnowledgeStatus(str, Enum):
    """Lifecycle statuses for facts / intuitions / tricks / graph nodes."""

    CANDIDATE = "candidate"
    APPROVED = "approved"
    REVIEWED = "reviewed"
    VERIFIED = "verified"
    REJECTED = "rejected"


class ReviewQueueStatus(str, Enum):
    """Human review-queue item statuses (project store)."""

    OPEN = "open"
    HELD = "held"
    APPROVED = "approved"
    REJECTED = "rejected"


class KnowledgeTrustPolicy:
    """Central admission sets for knowledge retrieval and administration.

    Solve-time retrieval admits ``approved``, ``reviewed``, and ``verified``.
    ``reviewed`` is included because consolidation may promote panel-accepted
    candidates to that status before an explicit admin approval; excluding it
    would drop live memories from subsequent solves.
    """

    SOLVE_RETRIEVAL: frozenset[str] = frozenset({
        KnowledgeStatus.APPROVED.value,
        KnowledgeStatus.REVIEWED.value,
        KnowledgeStatus.VERIFIED.value,
    })
    PROTECTED_FROM_REWRITE: frozenset[str] = frozenset({
        KnowledgeStatus.APPROVED.value,
        KnowledgeStatus.REVIEWED.value,
        KnowledgeStatus.VERIFIED.value,
    })
    REVIEW_QUEUE: frozenset[str] = frozenset(s.value for s in ReviewQueueStatus)

    @classmethod
    def admits_for_solve(cls, status: str | None) -> bool:
        return (status or "").strip().lower() in cls.SOLVE_RETRIEVAL
