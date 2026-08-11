from __future__ import annotations


import pytest

from math_agent.billing.models import LLMResponse
from math_agent.config import LeanConfig, default_config
from math_agent.lean.proof_search import ProofSearch, TacticGenerator
from math_agent.lean.runner import LeanRunner
from math_agent.llm.base import Message


class _StubLLM:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        temperature: float | None = None,
        response_format: dict[str, str] | None = None,
    ) -> LLMResponse:
        response = self._responses[self._idx % len(self._responses)]
        self._idx += 1
        return LLMResponse(
            text=response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_tactic_search_solves_simple_identity() -> None:
    lean_config: LeanConfig = default_config().lean
    runner = LeanRunner(lean_config)
    llm = _StubLLM(["1. rfl"])
    generator = TacticGenerator(llm)
    search = ProofSearch(generator=generator, runner=runner, max_attempts=4)
    result = await search.search("theorem ex : 1 = 1 := by")
    assert result.success, result.error
    assert "rfl" in result.proof


def test_default_config_exposes_lean_defaults() -> None:
    """Smoke test that default_config yields a usable Lean configuration."""
    cfg = default_config()
    assert isinstance(cfg.lean, LeanConfig)
    assert cfg.lean.enabled is False
    assert cfg.lean.mathlib_dep is True
