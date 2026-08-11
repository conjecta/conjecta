from unittest.mock import MagicMock

from math_agent.agent.formal_evidence import formal_evidence_id
from math_agent.agent.knowledge.promotion import promote_verified_lean


def _evidence_id(code, target_claim="Verify."):
    return formal_evidence_id(
        action_name="lean_check",
        target_claim=target_claim,
        artifact=code,
    )


def test_promote_verified_lean_adds_fact():
    store = MagicMock()
    store.add_fact = MagicMock(return_value={"id": "fact-1"})
    code = """
import Mathlib

lemma add_comm_nat (a b : ℕ) : a + b = b + a := by
  exact Nat.add_comm a b
"""
    evidence_id = _evidence_id(code)
    result = promote_verified_lean(
        store, "proj-1", code, evidence_id=evidence_id, accepted_evidence_id=evidence_id
    )
    assert "fact-1" in result
    store.add_fact.assert_called_once()
    args = store.add_fact.call_args.args
    kwargs = store.add_fact.call_args.kwargs
    assert args[0] == "proj-1"
    assert "add_comm_nat" in kwargs.get("statement", "")


def test_promote_without_store():
    result = promote_verified_lean(
        None,
        "proj-1",
        "lemma x : True := trivial",
        evidence_id="formal-abc",
        accepted_evidence_id="formal-abc",
    )
    assert "No knowledge store" in result


def test_promote_verified_lean_skips_mismatched_evidence_id():
    store = MagicMock()
    store.add_fact = MagicMock(return_value={"id": "fact-1"})
    code = "theorem add_comm_nat (a b : ℕ) : a + b = b + a := by exact Nat.add_comm a b"
    evidence_id = _evidence_id(code)
    result = promote_verified_lean(
        store,
        "proj-1",
        code,
        evidence_id=evidence_id,
        accepted_evidence_id="formal-wrong",
    )
    assert "does not match" in result
    store.add_fact.assert_not_called()


def test_promote_verified_lean_skips_without_accepted_evidence_id():
    store = MagicMock()
    store.add_fact = MagicMock(return_value={"id": "fact-1"})
    code = "theorem t : True := by trivial"
    evidence_id = _evidence_id(code)
    result = promote_verified_lean(
        store, "proj-1", code, evidence_id=evidence_id, accepted_evidence_id=""
    )
    assert "No accepted evidence ID" in result
    store.add_fact.assert_not_called()


def test_promote_verified_lean_defaults_to_candidate_status():
    store = MagicMock()
    store.add_fact = MagicMock(return_value={"id": "fact-1"})
    code = "theorem t : True := by trivial"
    evidence_id = _evidence_id(code)
    promote_verified_lean(
        store, "proj-1", code, evidence_id=evidence_id, accepted_evidence_id=evidence_id
    )
    kwargs = store.add_fact.call_args.kwargs
    assert kwargs.get("status") == "candidate"


def test_promote_verified_lean_uses_approved_when_verified():
    store = MagicMock()
    store.add_fact = MagicMock(return_value={"id": "fact-1"})
    code = "theorem t : True := by trivial"
    evidence_id = _evidence_id(code)
    promote_verified_lean(
        store,
        "proj-1",
        code,
        evidence_id=evidence_id,
        accepted_evidence_id=evidence_id,
        status="approved",
    )
    kwargs = store.add_fact.call_args.kwargs
    assert kwargs.get("status") == "approved"


def test_promote_verified_lean_extracts_statement_from_code_not_docstring():
    store = MagicMock()
    store.add_fact = MagicMock(return_value={"id": "fact-1"})
    code = """
/- This is a docstring that could be arbitrary prose. -/
-- Another comment
lemma add_comm_nat (a b : ℕ) : a + b = b + a := by
  exact Nat.add_comm a b
"""
    evidence_id = _evidence_id(code)
    promote_verified_lean(
        store, "proj-1", code, evidence_id=evidence_id, accepted_evidence_id=evidence_id
    )
    kwargs = store.add_fact.call_args.kwargs
    statement = kwargs.get("statement", "")
    assert "docstring" not in statement.lower()
    assert "arbitrary prose" not in statement.lower()
    assert "add_comm_nat" in statement
