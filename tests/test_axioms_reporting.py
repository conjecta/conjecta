from __future__ import annotations

from types import SimpleNamespace

import pytest

from math_agent.agent.formal_evidence import attach_formal_evidence
from math_agent.agent.react_state import Action, ToolObservation
from math_agent.agent.tools import ToolContext, ToolRegistry
from math_agent.lean.result import LeanResult
from math_agent.lean.runner import parse_axioms_output
from math_agent.tools.lean import append_axioms_line


class FakeRunner:
    def __init__(self, axioms=None, *, raises: bool = False):
        self.axioms = axioms
        self.raises = raises

    async def check_proof(self, code: str, draft: bool = False):
        return LeanResult(success=True, errors=[], uses_sorry=False)

    async def print_axioms(self, code: str, declaration: str):
        if self.raises:
            raise RuntimeError("boom")
        return self.axioms


def test_parse_axioms_output_list():
    output = "info: 'conjecta_target' depends on axioms: [propext, Classical.choice, Quot.sound]"
    assert parse_axioms_output(output) == ["propext", "Classical.choice", "Quot.sound"]


def test_parse_axioms_output_none_dependent():
    output = "'conjecta_target' does not depend on any axioms"
    assert parse_axioms_output(output) == []


def test_parse_axioms_output_unrecognized():
    assert parse_axioms_output("") is None
    assert parse_axioms_output("some unrelated compiler output") is None


@pytest.mark.asyncio
async def test_append_axioms_line_appends_summary():
    runner = FakeRunner(axioms=["propext"])
    output = await append_axioms_line(
        runner, "theorem conjecta_target : True := by trivial", "Lean verification: PASSED"
    )
    assert output.endswith("Axioms: propext")


@pytest.mark.asyncio
async def test_append_axioms_line_none_axioms():
    runner = FakeRunner(axioms=[])
    output = await append_axioms_line(
        runner, "theorem conjecta_target : True := by trivial", "Lean verification: PASSED"
    )
    assert output.endswith("Axioms: none")


@pytest.mark.asyncio
async def test_append_axioms_line_is_best_effort():
    """Probe failures and unrecognized results must not alter the output."""
    for runner in (
        FakeRunner(axioms=None),
        FakeRunner(raises=True),
        None,
        SimpleNamespace(),  # no print_axioms at all
    ):
        output = await append_axioms_line(
            runner, "theorem conjecta_target : True := by trivial", "Lean verification: PASSED"
        )
        assert output == "Lean verification: PASSED"


@pytest.mark.asyncio
async def test_lean_check_output_includes_axioms_line():
    runner = FakeRunner(axioms=["propext", "Classical.choice"])
    registry = ToolRegistry(enabled_tools=["lean_check"], lean_runner=runner)
    result = await registry.call(
        "lean_check",
        "theorem t : 1 + 1 = 2 := by norm_num",
        ToolContext(lean_runner=runner),
    )
    assert result.success is True
    assert "Axioms: propext, Classical.choice" in result.output


def test_attach_formal_evidence_captures_axioms():
    action = Action(
        name="lean_check",
        args={"code": "theorem t : True := by trivial"},
    )
    observation = ToolObservation(
        success=True,
        output="Lean verification: PASSED\nAxioms: propext",
        lean_code=action.args["code"],
    )

    result = attach_formal_evidence(action, observation, target_claim="True")
    evidence = result.metadata["formal_evidence"]

    assert evidence["axioms"] == ["propext"]


def test_attach_formal_evidence_axioms_absent_without_line():
    action = Action(
        name="lean_check",
        args={"code": "theorem t : True := by trivial"},
    )
    observation = ToolObservation(
        success=True,
        output="Lean verification: PASSED",
        lean_code=action.args["code"],
    )

    result = attach_formal_evidence(action, observation, target_claim="True")

    assert result.metadata["formal_evidence"]["axioms"] is None
