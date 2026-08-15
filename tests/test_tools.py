from __future__ import annotations

import asyncio
import json
import logging

import pytest
from unittest.mock import AsyncMock

from math_agent.agent.react_state import Action, ProjectContext
from math_agent.agent.react_agent import ReActAgent
from math_agent.agent.tools import ToolContext, ToolRegistry, ToolResult
from math_agent.config import AgentConfig, KnowledgeConfig


@pytest.mark.asyncio
async def test_builtin_tools_respect_enabled_list():
    registry = ToolRegistry(enabled_tools=["compute", "searching", "search_mathlib"])
    assert "compute" in registry.available
    assert "searching" in registry.available
    assert "search_mathlib" in registry.available
    assert "fetch_url" not in registry.available
    assert "web_fetch" not in registry.available


@pytest.mark.asyncio
async def test_registered_plugin_is_prompted_validated_and_dispatched():
    async def double(value: str, _ctx: ToolContext) -> ToolResult:
        return ToolResult(name="double", output=str(int(value) * 2), success=True)

    registry = ToolRegistry(enabled_tools=[])
    registry.register(
        "double",
        double,
        description="double an integer",
        args_example='{"value": "21"}',
        arg_map="value",
    )

    visible = registry.describe_visible_tools()
    plugin = next(item for item in visible if item.name == "double")
    assert plugin.category == "plugin"
    assert "double an integer" in registry.format_tool_list(visible)

    action = Action(name="double", args={"value": "21"})
    agent = ReActAgent(
        llm=object(),
        critic_llm=object(),
        config=AgentConfig(reviewers_enabled=[]),
        tool_registry=registry,
    )
    assert agent._validate_action(action) is None
    observation = await registry.execute_action(action)
    assert observation.success is True
    assert observation.output == "42"


def test_registered_plugin_rejects_duplicates_and_mcp_namespace():
    async def noop(value: str, _ctx: ToolContext) -> ToolResult:
        return ToolResult(name="noop", output=value, success=True)

    registry = ToolRegistry(enabled_tools=[])
    kwargs = {
        "description": "no operation",
        "args_example": '{"value": "..."}',
        "arg_map": "value",
    }
    registry.register("noop", noop, **kwargs)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("noop", noop, **kwargs)
    with pytest.raises(ValueError, match="reserved"):
        registry.register("mcp_fake", noop, **kwargs)


@pytest.mark.asyncio
async def test_searching_returns_clean_error_without_llm(monkeypatch):
    async def fake_tavily(query: str, **kwargs: object) -> str:
        return "Tavily search unavailable (set TAVILY_API_KEY)."

    async def fake_ddg(query: str, **kwargs: object) -> str:
        return "DuckDuckGo search failed: boom"

    monkeypatch.setattr("math_agent.search.tavily.tavily_search", fake_tavily)
    monkeypatch.setattr("math_agent.search.duckduckgo.duckduckgo_search", fake_ddg)
    registry = ToolRegistry(enabled_tools=["searching"])
    result = await registry.call("searching", "Riemann hypothesis", ToolContext())
    assert result.success is False
    assert "tavily" in result.output.lower()
    assert "traceback" not in result.output.lower()


@pytest.mark.asyncio
async def test_searching_uses_llm(monkeypatch):
    async def fake_tavily(query: str, **kwargs: object) -> str:
        return "Tavily search unavailable (set TAVILY_API_KEY)."

    async def fake_search_content(query: str, llm: object) -> str:
        return "search results"

    async def fake_ddg(query: str, **kwargs: object) -> str:
        return "DuckDuckGo search failed: boom"

    monkeypatch.setattr("math_agent.search.tavily.tavily_search", fake_tavily)
    monkeypatch.setattr("math_agent.search.duckduckgo.duckduckgo_search", fake_ddg)
    monkeypatch.setattr(
        "math_agent.tools.builtin.search._llm_search_content", fake_search_content
    )
    registry = ToolRegistry(enabled_tools=["searching"])
    result = await registry.call(
        "searching", "Riemann hypothesis", ToolContext(llm=object())
    )
    assert result.success is True
    assert "search results" in result.output


class FakeKnowledgeStore:
    def search_facts(self, project_id, query, limit=5):
        return [{"statement": f"{query} fact in {project_id}"}]

    def search_intuitions(self, project_id, query, limit=5):
        return [
            {"title": "use compactness", "body": "look for a convergent subsequence"}
        ]

    def search_tricks(self, project_id, query, limit=5):
        return [{"title": "diagonalize", "body": "split into eigenspaces"}]


@pytest.mark.asyncio
async def test_search_knowledge_uses_registry_store_with_project_context():
    registry = ToolRegistry(
        enabled_tools=["search_knowledge"],
        knowledge_store=FakeKnowledgeStore(),
    )
    observation = await registry.execute_action(
        Action(name="search_knowledge", args={"query": "spectral"}),
        ToolContext(project_context=ProjectContext(project_id="proj1")),
    )

    assert observation.success is True
    assert "spectral fact in proj1" in observation.output
    assert "use compactness" in observation.output


@pytest.mark.asyncio
async def test_fetch_url_tool_registered_and_fetches_content(monkeypatch):
    from math_agent.net_safety import SafeFetchResponse

    async def fake_fetch(url, *, timeout_seconds, headers, max_bytes):
        return SafeFetchResponse(
            url=url,
            status_code=200,
            headers={"content-type": "text/html"},
            content=b"<html><body><p>Important math content here.</p></body></html>",
        )

    monkeypatch.setattr("math_agent.tools.builtin.search.fetch_public_url", fake_fetch)
    registry = ToolRegistry(enabled_tools=["fetch_url"])
    assert "fetch_url" in registry.available
    result = await registry.call(
        "fetch_url", "https://example.com/paper", ToolContext()
    )
    assert result.success is True
    assert "Important math content here" in result.output
    assert "Source: https://example.com/paper" in result.output


@pytest.mark.asyncio
async def test_search_web_alias_uses_real_search(monkeypatch):
    from math_agent.agent.react_state import Action

    async def fake_search(query: str, **kwargs: object) -> str:
        return f"web results for {query}"

    monkeypatch.setattr("math_agent.tools.builtin.search._search", fake_search)
    registry = ToolRegistry(enabled_tools=["search"])
    action = Action(name="search_web", args={"query": "Riemann hypothesis"})
    obs = await registry.execute_action(action, ToolContext())
    assert obs.success is True
    assert "web results for Riemann hypothesis" in obs.output


@pytest.mark.asyncio
async def test_search_knowledge_fallback_uses_registry_knowledge_config(monkeypatch):
    """When the registry has no explicit knowledge_store, the fallback KnowledgeStore
    should be constructed with the registry's knowledge_config."""
    captured = {}

    class _FakeKnowledgeStore:
        def __init__(self, **kwargs):
            captured["init_kwargs"] = kwargs

        def search_facts(self, project_id, query, limit=5):
            return []

        def search_intuitions(self, project_id, query, limit=5):
            return []

        def search_tricks(self, project_id, query, limit=5):
            return []

    monkeypatch.setattr(
        "math_agent.knowledge.supabase.KnowledgeStore", _FakeKnowledgeStore
    )

    cfg = KnowledgeConfig(embedding_enabled=True, hybrid_search_top_k=7)
    registry = ToolRegistry(enabled_tools=["search_knowledge"], knowledge_config=cfg)
    observation = await registry.execute_action(
        Action(name="search_knowledge", args={"query": "test"}),
        ToolContext(project_context=ProjectContext(project_id="proj1")),
    )
    assert observation.success is True
    assert captured["init_kwargs"].get("knowledge_config") is cfg


def test_tactic_search_registered_when_lean_configured():
    registry = ToolRegistry(
        enabled_tools=["tactic_search"],
        lean_runner=AsyncMock(),
        llm=object(),
    )
    assert "tactic_search" in registry.available


@pytest.mark.asyncio
async def test_tactic_search_returns_proof_when_search_succeeds(monkeypatch):
    from math_agent.lean.proof_search import ProofSearchResult

    async def fake_search(self, theorem: str, max_attempts: int | None = None):
        return ProofSearchResult(
            success=True,
            proof="theorem t : True := by trivial",
            attempts=2,
        )

    monkeypatch.setattr("math_agent.lean.proof_search.ProofSearch.search", fake_search)
    registry = ToolRegistry(
        enabled_tools=["tactic_search"],
        lean_runner=AsyncMock(),
        llm=object(),
    )
    ctx = ToolContext(llm=object())
    result = await registry.call(
        "tactic_search",
        '{"theorem_statement": "theorem t : True := by trivial", "max_attempts": 5}',
        ctx,
    )
    assert result.success is True
    assert "Proof found" in result.output
    assert result.lean_code == "theorem t : True := by trivial"


@pytest.mark.asyncio
async def test_tactic_search_unavailable_without_llm():
    registry = ToolRegistry(
        enabled_tools=["tactic_search"],
        lean_runner=AsyncMock(),
    )
    result = await registry.call(
        "tactic_search",
        '{"theorem_statement": "theorem t : True := by trivial"}',
        ToolContext(),
    )
    assert result.success is False
    assert "unavailable" in result.output.lower()


@pytest.mark.asyncio
async def test_tactic_search_invalid_json_args():
    registry = ToolRegistry(
        enabled_tools=["tactic_search"],
        lean_runner=AsyncMock(),
        llm=object(),
    )
    ctx = ToolContext(llm=object())
    result = await registry.call("tactic_search", "not json", ctx)
    assert result.success is False
    assert "Invalid JSON" in result.output


@pytest.mark.asyncio
async def test_tactic_search_requires_non_empty_theorem():
    registry = ToolRegistry(
        enabled_tools=["tactic_search"],
        lean_runner=AsyncMock(),
        llm=object(),
    )
    ctx = ToolContext(llm=object())
    result = await registry.call(
        "tactic_search",
        '{"theorem_statement": "   "}',
        ctx,
    )
    assert result.success is False
    assert "non-empty" in result.output


@pytest.mark.asyncio
async def test_tactic_search_reports_failure_when_exhausted(monkeypatch):
    from math_agent.lean.proof_search import ProofSearchResult

    async def fake_search(self, theorem: str):
        return ProofSearchResult(
            success=False,
            proof=theorem,
            attempts=3,
            error="exhausted",
        )

    monkeypatch.setattr("math_agent.lean.proof_search.ProofSearch.search", fake_search)
    registry = ToolRegistry(
        enabled_tools=["tactic_search"],
        lean_runner=AsyncMock(),
        llm=object(),
    )
    ctx = ToolContext(
        llm=object(),
        agent_config=AgentConfig(tactic_search_wall_seconds=0.5),
    )
    result = await registry.call(
        "tactic_search",
        '{"theorem_statement": "theorem t : False := by"}',
        ctx,
    )
    assert result.success is False
    assert "No proof found" in result.output
    assert "exhausted" in result.output


@pytest.mark.asyncio
async def test_tactic_search_times_out(monkeypatch):
    import asyncio

    async def fake_search(self, theorem: str):
        await asyncio.sleep(10)

    monkeypatch.setattr("math_agent.lean.proof_search.ProofSearch.search", fake_search)
    registry = ToolRegistry(
        enabled_tools=["tactic_search"],
        lean_runner=AsyncMock(),
        llm=object(),
    )
    ctx = ToolContext(
        llm=object(),
        agent_config=AgentConfig(tactic_search_wall_seconds=0.01),
    )
    result = await registry.call(
        "tactic_search",
        '{"theorem_statement": "theorem t : True := by trivial"}',
        ctx,
    )
    assert result.success is False
    assert "timed out" in result.output.lower()


@pytest.mark.asyncio
async def test_execute_action_tactic_search_passes_structured_args(monkeypatch):
    from math_agent.lean.proof_search import ProofSearchResult

    async def fake_search(self, theorem: str, max_attempts: int | None = None):
        return ProofSearchResult(success=True, proof=theorem, attempts=1)

    monkeypatch.setattr("math_agent.lean.proof_search.ProofSearch.search", fake_search)
    registry = ToolRegistry(
        enabled_tools=["tactic_search"],
        lean_runner=AsyncMock(),
        llm=object(),
    )
    action = Action(
        name="tactic_search",
        args={
            "theorem_statement": "theorem t : True := by trivial",
            "max_attempts": 4,
        },
    )
    obs = await registry.execute_action(action, ToolContext(llm=object()))
    assert obs.success is True
    assert "Proof found" in obs.output


@pytest.mark.asyncio
async def test_tactic_search_clamps_max_attempts_above_config_budget(monkeypatch):
    from math_agent.lean.proof_search import ProofSearchResult

    captured: dict[str, int | None] = {"max_attempts": None}

    async def fake_search(self, theorem: str, max_attempts: int | None = None):
        # max_attempts is now held by the ProofSearch instance, not passed again.
        captured["max_attempts"] = self.max_attempts
        return ProofSearchResult(success=True, proof=theorem, attempts=1)

    monkeypatch.setattr("math_agent.lean.proof_search.ProofSearch.search", fake_search)
    registry = ToolRegistry(
        enabled_tools=["tactic_search"],
        lean_runner=AsyncMock(),
        llm=object(),
    )
    ctx = ToolContext(
        llm=object(),
        agent_config=AgentConfig(tactic_search_max_attempts=5),
    )
    result = await registry.call(
        "tactic_search",
        '{"theorem_statement": "theorem t : True := by trivial", "max_attempts": 100}',
        ctx,
    )
    assert result.success is True
    assert captured["max_attempts"] == 5


def test_registry_forwards_premise_retriever_to_context():
    class DummyRetriever:
        pass

    retriever = DummyRetriever()
    registry = ToolRegistry(premise_retriever=retriever)
    ctx = registry._context_with_defaults(None)
    assert ctx.premise_retriever is retriever
    ctx2 = registry._context_with_defaults(ToolContext())
    assert ctx2.premise_retriever is retriever


@pytest.mark.asyncio
async def test_tactic_search_passes_premise_retriever(monkeypatch):
    from math_agent.lean.proof_search import ProofSearchResult

    class DummyRetriever:
        pass

    retriever = DummyRetriever()

    async def fake_search(self, theorem: str, max_attempts: int | None = None):
        assert self.premise_retriever is retriever
        assert self.generator.premise_retriever is retriever
        return ProofSearchResult(success=True, proof=theorem, attempts=1)

    monkeypatch.setattr("math_agent.lean.proof_search.ProofSearch.search", fake_search)
    registry = ToolRegistry(
        enabled_tools=["tactic_search"],
        lean_runner=AsyncMock(),
        llm=object(),
        premise_retriever=retriever,
    )
    result = await registry.call(
        "tactic_search",
        '{"theorem_statement": "theorem t : True := by trivial"}',
        ToolContext(llm=object()),
    )
    assert result.success is True


@pytest.mark.asyncio
async def test_formalize_tool_uses_premise_retriever(monkeypatch):
    from types import SimpleNamespace

    class DummyRetriever:
        pass

    retriever = DummyRetriever()
    config = SimpleNamespace(max_repair_attempts=3)
    original_codegen = SimpleNamespace(llm=object(), runner=object(), config=config)
    captured = {}

    async def fake_formalize(statement, *, lean_codegen, state):
        captured["lean_codegen"] = lean_codegen
        return "PASSED", "theorem t : True := by trivial"

    monkeypatch.setattr("math_agent.tools.lean.formalize_statement", fake_formalize)
    registry = ToolRegistry(
        enabled_tools=["formalize"],
        lean_codegen=original_codegen,
        premise_retriever=retriever,
    )
    result = await registry.call("formalize", "True is true", ToolContext())
    assert result.success is True
    assert captured["lean_codegen"].premise_retriever is retriever
    assert captured["lean_codegen"].llm is original_codegen.llm
    assert captured["lean_codegen"].runner is original_codegen.runner
    assert captured["lean_codegen"].config is original_codegen.config


@pytest.mark.asyncio
async def test_lean_check_rejects_umbrella_mathlib_import():
    registry = ToolRegistry(enabled_tools=["lean_check"], lean_runner=AsyncMock())
    result = await registry.call(
        "lean_check",
        json.dumps(
            {"code": "import Mathlib\n\ntheorem t : True := by trivial"}
        ),
        ToolContext(),
    )
    assert result.success is False
    assert "umbrella" in result.output
    registry._lean_runner.check_proof.assert_not_awaited()


@pytest.mark.asyncio
async def test_call_records_stats_when_tool_raises():
    async def boom(_args: str, _ctx: ToolContext) -> ToolResult:
        raise RuntimeError("internal secret: db password is hunter2")

    registry = ToolRegistry(enabled_tools=[])
    registry.register(
        "boom",
        boom,
        description="always raises",
        args_example='{"value": "..."}',
        arg_map="value",
    )

    result = await registry.call("boom", '{"value": "1"}', ToolContext())
    assert result.success is False

    stats = registry.tool_stats["boom"]
    assert stats["calls"] == 1
    assert stats["failures"] == 1
    assert stats["wall_seconds"] >= 0.0


@pytest.mark.asyncio
async def test_call_info_logs_do_not_leak_raw_args(caplog):
    secret_payload = "prove the secret theorem about zeta-private-123"

    async def echo(value: str, _ctx: ToolContext) -> ToolResult:
        return ToolResult(name="echo", output=value, success=True)

    registry = ToolRegistry(enabled_tools=[])
    registry.register(
        "echo",
        echo,
        description="echo the value",
        args_example='{"value": "..."}',
        arg_map="value",
    )

    with caplog.at_level(logging.INFO, logger="math_agent.tools"):
        result = await registry.call(
            "echo", json.dumps({"value": secret_payload}), ToolContext()
        )

    assert result.success is True
    info_messages = [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.INFO
    ]
    assert info_messages
    assert all(secret_payload not in message for message in info_messages)
    # Nothing derived from the args payload (keys or values) may be logged.
    assert all("arg_keys" not in message for message in info_messages)
    assert any("Tool call start: name=echo" in message for message in info_messages)


@pytest.mark.asyncio
async def test_call_error_result_hides_internal_exception_text(caplog):
    internal_detail = "connection to 10.0.0.7 refused: auth token abc123"

    async def boom(_args: str, _ctx: ToolContext) -> ToolResult:
        raise RuntimeError(internal_detail)

    registry = ToolRegistry(enabled_tools=[])
    registry.register(
        "boom",
        boom,
        description="always raises",
        args_example='{"value": "..."}',
        arg_map="value",
    )

    with caplog.at_level(logging.INFO, logger="math_agent.tools"):
        result = await registry.call("boom", '{"value": "1"}', ToolContext())

    assert result.success is False
    assert internal_detail not in result.output
    assert "boom" in result.output
    # Full detail stays in the logs for debugging.
    assert any(internal_detail in record.getMessage() or
               (record.exc_info and internal_detail in str(record.exc_info[1]))
               for record in caplog.records)


@pytest.mark.asyncio
async def test_call_cancelled_error_propagates_but_updates_stats():
    started = asyncio.Event()

    async def slow(_args: str, _ctx: ToolContext) -> ToolResult:
        started.set()
        await asyncio.sleep(60)
        return ToolResult(name="slow", output="done", success=True)

    registry = ToolRegistry(enabled_tools=[])
    registry.register(
        "slow",
        slow,
        description="sleeps",
        args_example='{"value": "..."}',
        arg_map="value",
    )

    task = asyncio.ensure_future(registry.call("slow", '{"value": "1"}', ToolContext()))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stats = registry.tool_stats["slow"]
    assert stats["calls"] == 1
    assert stats["failures"] == 0
    assert stats["wall_seconds"] >= 0.0
