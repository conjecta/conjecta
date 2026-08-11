from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from math_agent.agent.react_state import Action
from math_agent.agent.state import ReasoningState, ReasoningStep, StepType
from math_agent.agent.tools import ToolContext, ToolRegistry
from math_agent.lean.runner import LeanResult
from math_agent.tools.lean import format_lean_result, formalize_statement


@pytest.mark.asyncio
async def test_builtin_tools_without_lean():
    registry = ToolRegistry(enabled_tools=["compute", "search"])
    assert "compute" in registry.available
    assert "formalize" not in registry.available
    assert "lean_check" not in registry.available


@pytest.mark.asyncio
async def test_lean_tools_registered_when_runner_present():
    runner = MagicMock()
    codegen = MagicMock()
    registry = ToolRegistry(
        enabled_tools=["formalize", "lean_check"],
        lean_runner=runner,
        lean_codegen=codegen,
    )
    assert "formalize" in registry.available
    assert "lean_check" in registry.available


@pytest.mark.asyncio
async def test_formalize_tool_unavailable_without_codegen():
    registry = ToolRegistry(enabled_tools=["formalize"])
    result = await registry.call("formalize", "1 + 1 = 2")
    assert result.success is False
    assert "unavailable" in result.output.lower()


@pytest.mark.asyncio
async def test_lean_check_tool_with_mock_runner():
    runner = MagicMock()
    runner.check_proof = AsyncMock(
        return_value=LeanResult(success=True, errors=[], warnings=[], uses_sorry=False)
    )
    registry = ToolRegistry(
        enabled_tools=["lean_check"],
        lean_runner=runner,
    )
    result = await registry.call(
        "lean_check",
        "theorem t : 1 + 1 = 2 := by norm_num",
        ToolContext(lean_runner=runner),
    )
    assert result.success is True
    assert "PASSED" in result.output
    assert result.lean_code is not None
    runner.check_proof.assert_awaited_once()


def test_format_lean_result_passed():
    text = format_lean_result("theorem t : True := trivial", LeanResult(success=True))
    assert "PASSED" in text
    assert "theorem t" in text


def test_format_lean_result_failed():
    text = format_lean_result("bad code", LeanResult(success=False, errors=["type mismatch"]))
    assert "FAILED" in text
    assert "type mismatch" in text


@pytest.mark.asyncio
async def test_formalize_statement_uses_codegen():
    codegen = MagicMock()
    codegen.generate_and_verify = AsyncMock(
        return_value=(
            "theorem t : True := trivial",
            LeanResult(success=True, uses_sorry=False),
        )
    )
    state = ReasoningState(problem="prove True")
    output, code = await formalize_statement(
        "True holds",
        lean_codegen=codegen,
        state=state,
    )
    assert code is not None
    assert "PASSED" in output
    args = codegen.generate_and_verify.await_args
    step: ReasoningStep = args.args[0]
    assert step.step_type == StepType.FORMALIZATION
    assert step.content == "True holds"


def test_format_lean_result_draft_ok():
    text = format_lean_result(
        "theorem t : True := by sorry",
        LeanResult(success=True, uses_sorry=True, draft=True),
    )
    assert "DRAFT OK" in text
    assert "NOT a complete proof" in text


@pytest.mark.asyncio
async def test_lean_check_tool_draft_mode_reports_draft_ok():
    runner = MagicMock()
    runner.check_proof = AsyncMock(
        return_value=LeanResult(success=True, uses_sorry=True, draft=True)
    )
    registry = ToolRegistry(enabled_tools=["lean_check"], lean_runner=runner)

    result = await registry.call(
        "lean_check",
        json.dumps({"code": "theorem t : True := by sorry", "draft": True}),
        ToolContext(lean_runner=runner),
    )

    assert result.success is False  # draft pass is not proof evidence
    assert "DRAFT OK" in result.output
    assert "formalize" not in result.output  # no regeneration hint on draft progress
    assert runner.check_proof.await_args.kwargs.get("draft") is True


@pytest.mark.asyncio
async def test_lean_check_tool_json_args_default_strict():
    runner = MagicMock()
    runner.check_proof = AsyncMock(
        return_value=LeanResult(success=True, errors=[], warnings=[], uses_sorry=False)
    )
    registry = ToolRegistry(enabled_tools=["lean_check"], lean_runner=runner)

    result = await registry.call(
        "lean_check",
        json.dumps({"code": "theorem t : 1 + 1 = 2 := by norm_num"}),
        ToolContext(lean_runner=runner),
    )

    assert result.success is True
    assert "PASSED" in result.output
    assert runner.check_proof.await_args.kwargs.get("draft") is False


@pytest.mark.asyncio
async def test_lean_check_execute_action_maps_draft_arg():
    runner = MagicMock()
    runner.check_proof = AsyncMock(
        return_value=LeanResult(success=True, uses_sorry=True, draft=True)
    )
    registry = ToolRegistry(enabled_tools=["lean_check"], lean_runner=runner)

    observation = await registry.execute_action(
        Action(
            name="lean_check",
            args={"code": "theorem t : True := by sorry", "draft": True},
        ),
        ToolContext(lean_runner=runner),
    )

    assert "DRAFT OK" in observation.output
    assert runner.check_proof.await_args.kwargs.get("draft") is True
