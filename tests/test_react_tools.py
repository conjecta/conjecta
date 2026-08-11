
import pytest

from math_agent.agent.react_state import Action, ToolObservation
from math_agent.agent.tools import ToolContext, ToolRegistry


@pytest.mark.asyncio
async def test_execute_action_compute():
    registry = ToolRegistry(enabled_tools=["compute"])
    action = Action(name="compute", args={"code": "print(2 + 2)"})
    obs = await registry.execute_action(action, ToolContext())
    assert isinstance(obs, ToolObservation)
    assert obs.success is True
    assert "4" in obs.output


@pytest.mark.asyncio
async def test_execute_action_compute_blocks_os():
    registry = ToolRegistry(enabled_tools=["compute"])
    action = Action(name="compute", args={"code": "import os\nprint(os.getcwd())"})
    obs = await registry.execute_action(action, ToolContext())
    assert obs.success is False


@pytest.mark.asyncio
async def test_execute_unknown_action():
    registry = ToolRegistry(enabled_tools=["compute"])
    obs = await registry.execute_action(Action(name="unknown", args={}), ToolContext())
    assert obs.success is False
    assert "Unknown action" in obs.output


def test_lean_runner_and_codegen_properties():
    registry = ToolRegistry(enabled_tools=["compute"])
    assert registry.lean_runner is None
    assert registry.lean_codegen is None


@pytest.mark.asyncio
async def test_execute_action_searching_uses_llm(monkeypatch):
    async def fake_tavily(query: str, **kwargs: object) -> str:
        return "Tavily search unavailable (set TAVILY_API_KEY)."

    async def fake_search_content(query: str, llm: object) -> str:
        return f"LLM search results for {query}"

    async def fake_ddg(query: str, **kwargs: object) -> str:
        return "DuckDuckGo search failed: boom"

    monkeypatch.setattr("math_agent.search.tavily.tavily_search", fake_tavily)
    monkeypatch.setattr("math_agent.search.duckduckgo.duckduckgo_search", fake_ddg)
    monkeypatch.setattr("math_agent.agent.tools._llm_search_content", fake_search_content)
    registry = ToolRegistry(enabled_tools=["searching"])
    action = Action(name="searching", args={"query": "Riemann hypothesis"})
    ctx = ToolContext(llm=object())  # any non-None object triggers the LLM path
    obs = await registry.execute_action(action, ctx)
    assert isinstance(obs, ToolObservation)
    assert obs.success is True
    assert "LLM search results for Riemann hypothesis" in obs.output


@pytest.mark.asyncio
async def test_execute_action_searching_without_llm(monkeypatch):
    async def fake_tavily(query: str, **kwargs: object) -> str:
        return "Tavily search unavailable (set TAVILY_API_KEY)."

    async def fake_ddg(query: str, **kwargs: object) -> str:
        return "DuckDuckGo search failed: boom"

    monkeypatch.setattr("math_agent.search.tavily.tavily_search", fake_tavily)
    monkeypatch.setattr("math_agent.search.duckduckgo.duckduckgo_search", fake_ddg)
    registry = ToolRegistry(enabled_tools=["searching"])
    action = Action(name="searching", args={"query": "Riemann hypothesis"})
    obs = await registry.execute_action(action, ToolContext())
    assert isinstance(obs, ToolObservation)
    assert obs.success is False
    assert "tavily" in obs.output.lower()
