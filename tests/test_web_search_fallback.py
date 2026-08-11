from unittest.mock import AsyncMock

import pytest

from math_agent.search.web_search import web_search_with_fallback


@pytest.mark.asyncio
async def test_web_search_falls_back_to_duckduckgo(monkeypatch):
    monkeypatch.setattr(
        "math_agent.search.web_search.tavily_search",
        AsyncMock(return_value="Tavily search unavailable (set TAVILY_API_KEY)."),
    )
    monkeypatch.setattr(
        "math_agent.search.web_search.duckduckgo_search",
        AsyncMock(return_value="1. IMU Fields Medal\n(https://www.mathunion.org/)"),
    )

    provider, text = await web_search_with_fallback("latest Fields Medal")
    assert provider == "duckduckgo"
    assert "mathunion.org" in text


@pytest.mark.asyncio
async def test_web_search_prefers_tavily(monkeypatch):
    monkeypatch.setattr(
        "math_agent.search.web_search.tavily_search",
        AsyncMock(return_value="Summary: 2026 Fields Medal winners"),
    )
    ddg = AsyncMock(return_value="should not be used")
    monkeypatch.setattr("math_agent.search.web_search.duckduckgo_search", ddg)

    provider, text = await web_search_with_fallback("latest Fields Medal")
    assert provider == "tavily"
    assert "2026" in text
    ddg.assert_not_awaited()
