from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from math_agent.agent.state import ReasoningState, ReasoningStep
from math_agent.config import LeanConfig
from math_agent.lean.codegen import LeanCodegen
from math_agent.lean.result import LeanResult


class _Runner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    async def check_proof(self, _code):
        self.calls += 1
        return self.results.pop(0)


@pytest.mark.asyncio
async def test_codegen_does_not_repair_unsafe_source():
    runner = _Runner(
        [LeanResult(success=False, failure_kind="unsafe_source", errors=["blocked"])]
    )
    codegen = LeanCodegen(llm=object(), runner=runner, config=LeanConfig())
    codegen._generate = AsyncMock(return_value="constant bad : False")
    codegen._critic_repair = AsyncMock(return_value="constant bad : False")
    codegen._repair = AsyncMock()

    _, result = await codegen.generate_and_verify(
        ReasoningStep(content="False"), ReasoningState(problem="False")
    )

    assert result is not None and result.failure_kind == "unsafe_source"
    assert runner.calls == 1
    codegen._repair.assert_not_awaited()


@pytest.mark.asyncio
async def test_codegen_stops_when_repair_returns_unchanged_code():
    runner = _Runner(
        [LeanResult(success=False, failure_kind="type_mismatch", errors=["bad type"])]
    )
    codegen = LeanCodegen(llm=object(), runner=runner, config=LeanConfig())
    codegen._generate = AsyncMock(return_value="theorem t : True := by rfl")
    codegen._critic_repair = AsyncMock(return_value="theorem t : True := by rfl")
    codegen._repair = AsyncMock(return_value="theorem t : True := by rfl")

    _, result = await codegen.generate_and_verify(
        ReasoningStep(content="True"), ReasoningState(problem="True")
    )

    assert result is not None and result.success is False
    assert runner.calls == 1
    codegen._repair.assert_awaited_once()
