from __future__ import annotations

from math_agent.agent.formal_evidence import (
    attach_formal_evidence,
    extract_formal_declarations,
)
from math_agent.agent.react_state import Action, ToolObservation


def test_extract_formal_declarations_preserves_order_and_signature():
    code = """
import Mathlib

lemma helper (n : Nat) : n = n := by rfl

theorem conjecta_target : True := by trivial
"""

    declarations = extract_formal_declarations(code)

    assert [item["name"] for item in declarations] == [
        "helper",
        "conjecta_target",
    ]
    assert declarations[-1]["signature"] == ": True"


def test_lean_check_evidence_uses_actual_declaration_not_target_copy():
    action = Action(
        name="lean_check",
        args={
            "code": "theorem easier : True := by trivial",
            "declaration": "easier",
        },
    )
    observation = ToolObservation(
        success=True,
        output="Lean verification: PASSED",
        lean_code=action.args["code"],
    )

    result = attach_formal_evidence(
        action,
        observation,
        target_claim="Prove the Riemann hypothesis.",
    )
    evidence = result.metadata["formal_evidence"]

    assert evidence["requested_claim"] == "Prove the Riemann hypothesis."
    assert evidence["declared_claim"] == ": True"
    assert evidence["primary_declaration"]["name"] == "easier"
    assert evidence["statement_bound"] is True


def test_requested_declaration_must_exist_to_bind_statement():
    action = Action(
        name="lean_check",
        args={
            "code": "theorem actual : True := by trivial",
            "declaration": "missing",
        },
    )
    observation = ToolObservation(
        success=True,
        output="Lean verification: PASSED",
        lean_code=action.args["code"],
    )

    result = attach_formal_evidence(action, observation, target_claim="True")

    assert result.metadata["formal_evidence"]["statement_bound"] is False


def test_declaration_extraction_ignores_comments_and_strings():
    code = '''
theorem actual : True := by trivial
-- theorem fake : False := by trivial
def text := "theorem alsoFake : False := by trivial"
'''

    declarations = extract_formal_declarations(code)

    assert [item["name"] for item in declarations] == ["actual"]


def test_prove_by_lemmas_observation_binds_formal_evidence():
    """prove_by_lemmas is a formal action: its verified code must get an evidence ID."""
    code = (
        "lemma step_one : True := by trivial\n\n"
        "theorem conjecta_target : True := by\n  exact step_one\n"
    )
    action = Action(
        name="prove_by_lemmas",
        args={"statement": "Prove True.", "lemmas": "[]"},
    )
    observation = ToolObservation(
        success=True,
        output="Lean verification: PASSED",
        lean_code=code,
    )

    result = attach_formal_evidence(action, observation, target_claim="Prove True.")
    evidence = result.metadata["formal_evidence"]

    assert evidence["id"].startswith("formal-")
    assert evidence["action"] == "prove_by_lemmas"
    assert evidence["primary_declaration"]["name"] == "conjecta_target"
    assert evidence["statement_bound"] is True
    assert "Formal evidence ID:" in result.output
