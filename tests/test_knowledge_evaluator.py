from __future__ import annotations

from unittest.mock import Mock, call

from math_agent.agent.knowledge_evaluator import KnowledgeEvaluator


def test_verified_items_are_immutable_except_for_catalog_typed_score_updates():
    store = Mock()
    evaluator = KnowledgeEvaluator(llm=object(), knowledge_store=store)
    verified = {
        "id": "verified-fact",
        "status": "verified",
        "statement": "Trusted statement",
        "why": "Trusted explanation",
        "source_type": "lean_verified",
    }
    spare = {"id": "candidate-fact", "status": "candidate", "statement": "Spare"}

    counts = evaluator._apply_ops(
        "project",
        {
            "scores": [{"id": "verified-fact", "kind": "trick", "score": 0.9}],
            "revisions": [{
                "id": "verified-fact",
                "kind": "trick",
                "fields": {
                    "statement": "Forged statement",
                    "why": "Forged explanation",
                    "source_type": "manual",
                },
            }],
            "discards": [{"id": "verified-fact", "kind": "trick"}],
        },
        [verified, spare],
        [],
        [],
    )

    assert counts == {"revised": 0, "discarded": 0, "proposed": 0, "scored": 1}
    store.set_score.assert_called_once_with("project", "verified-fact", "fact", 0.9)
    store.update_item.assert_not_called()
    store.delete_item.assert_not_called()


def test_approved_and_verified_items_are_protected_from_revisions_and_discards():
    store = Mock()
    evaluator = KnowledgeEvaluator(llm=object(), knowledge_store=store)
    candidate = {"id": "candidate-intuition", "status": "candidate", "title": "Old", "body": "Old"}
    approved = {"id": "approved-trick", "status": "approved", "title": "Trick", "body": "Body"}
    spare = {"id": "spare-fact", "status": "candidate", "statement": "Spare"}

    counts = evaluator._apply_ops(
        "project",
        {
            "revisions": [{
                "id": "candidate-intuition",
                "kind": "fact",
                "fields": {"body": "Revised"},
            }],
            "discards": [{"id": "approved-trick", "kind": "fact"}],
        },
        [spare],
        [candidate],
        [approved],
    )

    assert counts == {"revised": 1, "discarded": 0, "proposed": 0, "scored": 0}
    assert store.update_item.call_args == call(
        "project", "candidate-intuition", "intuition", {"body": "Revised"}
    )
    store.delete_item.assert_not_called()
