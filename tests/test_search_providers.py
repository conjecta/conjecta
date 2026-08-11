from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from math_agent.agent.tools import ToolContext, ToolRegistry
from math_agent.billing.models import LLMResponse
from math_agent.search.arxiv import arxiv_search, is_arxiv_failure_message
from math_agent.search.duckduckgo import (
    duckduckgo_search,
    is_duckduckgo_failure_message,
)
from math_agent.search.semantic_scholar import (
    is_scholar_failure_message,
    scholar_search,
)

ARXIV_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/1234.56789v1</id>
    <title>  A Note on   Tensor Decompositions </title>
    <summary>We study tensor decompositions and prove a bound.</summary>
    <published>2024-01-15T00:00:00Z</published>
    <author><name>Alice Zhang</name></author>
    <author><name>Bob Li</name></author>
  </entry>
</feed>
"""

ARXIV_EMPTY_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"></feed>
"""

SCHOLAR_JSON = {
    "data": [
        {
            "title": "Tensor Networks for Fun and Profit",
            "abstract": "An abstract about tensor networks.",
            "authors": [{"name": "Carol Wang"}],
            "year": 2023,
            "citationCount": 42,
            "url": "https://www.semanticscholar.org/paper/abc",
            "externalIds": {"ArXiv": "2301.00001"},
        }
    ]
}

DDG_HTML = """
<html><body>
<div class="result">
  <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Farticle">Example &amp; Article</a>
  <a class="result__snippet">A snippet about <b>tensors</b>.</a>
</div>
</body></html>
"""

DDG_EMPTY_HTML = "<html><body><div>No results</div></body></html>"


class _FakeResponse:
    def __init__(self, *, text: str = "", json_data: object = None, status_code: int = 200):
        self.text = text
        self._json_data = json_data
        self.status_code = status_code
        self.headers: dict[str, str] = {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPError(f"status {self.status_code}")

    def json(self) -> object:
        return self._json_data


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse) -> None:
    class _FakeClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> "_FakeClient":
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def get(self, url: str, **kwargs: object) -> _FakeResponse:
            return response

        async def post(self, url: str, **kwargs: object) -> _FakeResponse:
            return response

    monkeypatch.setattr("httpx.AsyncClient", _FakeClient)


# --- arXiv ---


def test_arxiv_search_parses_entries(monkeypatch: pytest.MonkeyPatch):
    _patch_httpx(monkeypatch, _FakeResponse(text=ARXIV_XML))
    output = asyncio.run(arxiv_search("tensor decomposition"))
    assert not is_arxiv_failure_message(output)
    assert "A Note on Tensor Decompositions" in output
    assert "Alice Zhang" in output
    assert "2024-01-15" in output
    assert "arxiv.org/abs/1234.56789" in output


def test_arxiv_search_empty_query():
    output = asyncio.run(arxiv_search("   "))
    assert is_arxiv_failure_message(output)


def test_arxiv_search_no_results(monkeypatch: pytest.MonkeyPatch):
    _patch_httpx(monkeypatch, _FakeResponse(text=ARXIV_EMPTY_XML))
    output = asyncio.run(arxiv_search("zzzznothing"))
    assert is_arxiv_failure_message(output)
    assert output.startswith("No arXiv results")


def test_arxiv_search_http_error(monkeypatch: pytest.MonkeyPatch):
    _patch_httpx(monkeypatch, _FakeResponse(status_code=500))
    output = asyncio.run(arxiv_search("tensors"))
    assert is_arxiv_failure_message(output)
    assert output.startswith("arXiv search failed")


# --- Semantic Scholar ---


def test_scholar_search_parses_papers(monkeypatch: pytest.MonkeyPatch):
    _patch_httpx(monkeypatch, _FakeResponse(json_data=SCHOLAR_JSON))
    output = asyncio.run(scholar_search("tensor networks"))
    assert not is_scholar_failure_message(output)
    assert "Tensor Networks for Fun and Profit" in output
    assert "Citations: 42" in output
    assert "arXiv:2301.00001" in output


def test_scholar_search_rate_limited(monkeypatch: pytest.MonkeyPatch):
    _patch_httpx(monkeypatch, _FakeResponse(status_code=429))
    output = asyncio.run(scholar_search("tensor networks"))
    assert is_scholar_failure_message(output)
    assert output.startswith("Semantic Scholar rate limit")


def test_scholar_search_empty_query():
    output = asyncio.run(scholar_search(""))
    assert is_scholar_failure_message(output)


# --- DuckDuckGo ---


def test_duckduckgo_search_parses_results(monkeypatch: pytest.MonkeyPatch):
    _patch_httpx(monkeypatch, _FakeResponse(text=DDG_HTML))
    output = asyncio.run(duckduckgo_search("tensors"))
    assert not is_duckduckgo_failure_message(output)
    assert "Example & Article" in output
    assert "https://example.com/article" in output
    assert "A snippet about tensors." in output


def test_duckduckgo_search_no_results(monkeypatch: pytest.MonkeyPatch):
    _patch_httpx(monkeypatch, _FakeResponse(text=DDG_EMPTY_HTML))
    output = asyncio.run(duckduckgo_search("zzzznothing"))
    assert is_duckduckgo_failure_message(output)
    assert output.startswith("No web search results")


# --- Tool wiring ---


def test_new_search_tools_registered():
    registry = ToolRegistry(enabled_tools=["search_arxiv", "search_scholar"])
    assert "search_arxiv" in registry.available
    assert "search_scholar" in registry.available


def test_search_arxiv_tool_success(monkeypatch: pytest.MonkeyPatch):
    async def run() -> None:
        _patch_httpx(monkeypatch, _FakeResponse(text=ARXIV_XML))
        registry = ToolRegistry(enabled_tools=["search_arxiv"])
        result = await registry.call("search_arxiv", "tensor decomposition", ToolContext())
        assert result.success is True
        assert "A Note on Tensor Decompositions" in result.output

    asyncio.run(run())


def test_search_tool_falls_back_to_duckduckgo(monkeypatch: pytest.MonkeyPatch):
    async def run() -> None:
        async def fake_tavily(query: str, **kwargs: object) -> str:
            return "Tavily search unavailable (set TAVILY_API_KEY)."

        async def fake_ddg(query: str, **kwargs: object) -> str:
            return "1. Fallback Result\nsome snippet\n(https://example.com)"

        monkeypatch.setattr("math_agent.search.tavily.tavily_search", fake_tavily)
        monkeypatch.setattr("math_agent.search.duckduckgo.duckduckgo_search", fake_ddg)
        registry = ToolRegistry(enabled_tools=["search"])
        result = await registry.call("search", "tensors", ToolContext())
        assert result.success is True
        assert "[web search via DuckDuckGo]" in result.output
        assert "Fallback Result" in result.output

    asyncio.run(run())


def test_search_tool_reports_failure_when_both_providers_fail(
    monkeypatch: pytest.MonkeyPatch,
):
    async def run() -> None:
        async def fake_tavily(query: str, **kwargs: object) -> str:
            return "Tavily search unavailable (set TAVILY_API_KEY)."

        async def fake_ddg(query: str, **kwargs: object) -> str:
            return "DuckDuckGo search failed: boom"

        monkeypatch.setattr("math_agent.search.tavily.tavily_search", fake_tavily)
        monkeypatch.setattr("math_agent.search.duckduckgo.duckduckgo_search", fake_ddg)
        registry = ToolRegistry(enabled_tools=["search"])
        result = await registry.call("search", "tensors", ToolContext())
        assert result.success is False

    asyncio.run(run())


def test_searching_tool_chain_tavily_then_ddg_then_llm(
    monkeypatch: pytest.MonkeyPatch,
):
    async def run() -> None:
        async def failing_tavily(query: str, **kwargs: object) -> str:
            return "Tavily search unavailable (set TAVILY_API_KEY)."

        async def failing_ddg(query: str, **kwargs: object) -> str:
            return "DuckDuckGo search timed out after 20s"

        monkeypatch.setattr("math_agent.search.tavily.tavily_search", failing_tavily)
        monkeypatch.setattr("math_agent.search.duckduckgo.duckduckgo_search", failing_ddg)
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
        result = await registry.call("searching", "arXiv 1905.00802", ToolContext(llm=llm))
        assert result.success is True
        assert result.output.startswith("[model knowledge, not from live search]")
        assert "Tensor paper summary" in result.output
        llm.complete.assert_awaited_once()

    asyncio.run(run())


def test_searching_tool_stops_at_duckduckgo(monkeypatch: pytest.MonkeyPatch):
    async def run() -> None:
        async def failing_tavily(query: str, **kwargs: object) -> str:
            return "Tavily search unavailable (set TAVILY_API_KEY)."

        async def ok_ddg(query: str, **kwargs: object) -> str:
            return "1. DDG Result\nsnippet\n(https://example.com)"

        monkeypatch.setattr("math_agent.search.tavily.tavily_search", failing_tavily)
        monkeypatch.setattr("math_agent.search.duckduckgo.duckduckgo_search", ok_ddg)
        llm = MagicMock()
        llm.complete = AsyncMock()
        registry = ToolRegistry(enabled_tools=["searching"])
        result = await registry.call("searching", "tensors", ToolContext(llm=llm))
        assert result.success is True
        assert "DDG Result" in result.output
        llm.complete.assert_not_awaited()

    asyncio.run(run())
