from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from math_agent.agent.react_agent import _needs_searching_fallback
from math_agent.agent.react_state import ToolObservation
from math_agent.agent.tools import ToolContext, ToolRegistry
from math_agent.billing.models import LLMResponse


def test_needs_searching_fallback_on_empty_web_results():
    obs = ToolObservation(success=True, output="No web search results found for: foo")
    assert _needs_searching_fallback(obs) is True


def test_needs_searching_fallback_on_successful_results():
    obs = ToolObservation(success=True, output="1. Summary: something useful")
    assert _needs_searching_fallback(obs) is False


def test_searching_tool_falls_back_to_llm_when_tavily_unavailable(monkeypatch):
    async def run() -> None:
        async def fake_tavily(query: str, **kwargs: object) -> str:
            return "Tavily search unavailable (set TAVILY_API_KEY)."

        async def fake_ddg(query: str, **kwargs: object) -> str:
            return "DuckDuckGo search failed: boom"

        monkeypatch.setattr("math_agent.search.tavily.tavily_search", fake_tavily)
        monkeypatch.setattr(
            "math_agent.search.duckduckgo.duckduckgo_search", fake_ddg
        )
        llm = MagicMock()
        llm.complete = AsyncMock(
            return_value=LLMResponse(
                text="Tensor paper summary.",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            )
        )
        registry = ToolRegistry(enabled_tools=["searching"])
        ctx = ToolContext(llm=llm)
        result = await registry.call("searching", "arXiv 1905.00802", ctx)
        assert result.success is True
        assert "Tensor paper summary" in result.output
        llm.complete.assert_awaited_once()

    asyncio.run(run())
