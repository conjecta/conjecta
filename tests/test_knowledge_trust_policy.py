"""Tests for the central knowledge trust policy."""

from __future__ import annotations

from math_agent.knowledge.trust import (
    KnowledgeStatus,
    KnowledgeTrustPolicy,
    ReviewQueueStatus,
)
from math_agent.web.knowledge_selection import TRUSTED_KNOWLEDGE_STATUSES
from math_agent.web.project_store import TRUSTED_KNOWLEDGE_STATUSES as STORE_TRUSTED
from math_agent.web.project_store import VALID_REVIEW_STATUSES


def test_solve_retrieval_matches_policy_across_modules():
    assert TRUSTED_KNOWLEDGE_STATUSES is KnowledgeTrustPolicy.SOLVE_RETRIEVAL
    assert STORE_TRUSTED is KnowledgeTrustPolicy.SOLVE_RETRIEVAL
    assert VALID_REVIEW_STATUSES == set(KnowledgeTrustPolicy.REVIEW_QUEUE)


def test_knowledge_and_review_queue_status_values():
    assert {s.value for s in KnowledgeStatus} == {
        "candidate",
        "approved",
        "reviewed",
        "verified",
        "rejected",
    }
    assert {s.value for s in ReviewQueueStatus} == {
        "open",
        "held",
        "approved",
        "rejected",
    }
