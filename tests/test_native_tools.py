"""Tests for the native function-calling protocol (phase C).

Covers: backend tool_call parsing/accumulation, the dual-protocol ReAct loop
(native path, multi-tool_call handling, think degradation, legacy fallback),
and MCP schema passthrough.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from math_agent.agent.react_agent import ReActAgent
from math_agent.agent.tools import ToolRegistry
from math_agent.billing.models import LLMResponse, ToolCall
from math_agent.config import AgentConfig
from math_agent.llm.base import Message
from math_agent.llm.openai import OpenAICompatibleBackend


class NativeFakeLLM:
    """Queue-based fake backend that speaks native function calling.

    Each queued step is ``(thought_chunks, tool_calls)``: the thought chunks
    are streamed as content deltas, and the tool_calls are attached to the
    final summary chunk.
    """

    supports_native_tools = True

    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = 0
        self.system_prompts = []
        self.tools_seen = []
        self.response_formats = []

    def _next_step(self):
        step = self.steps[min(self.calls, len(self.steps) - 1)]
        self.calls += 1
        return step

    async def complete(
        self,
        messages,
        system=None,
        temperature=None,
        response_format=None,
        *,
        logprobs=False,
        tools=None,
    ):
        thought_chunks, tool_calls = self._next_step()
        return LLMResponse(
            text="".join(thought_chunks),
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            tool_calls=tuple(tool_calls) if tool_calls else None,
        )

    async def stream(
        self,
        messages,
        system=None,
        temperature=None,
        response_format=None,
        *,
        logprobs=False,
        tools=None,
    ):
        self.system_prompts.append(system or "")
        self.tools_seen.append(tools)
        self.response_formats.append(response_format)
        thought_chunks, tool_calls = self._next_step()
        for chunk in thought_chunks:
            yield LLMResponse(
                text=chunk,
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            )
        yield LLMResponse(
            text="",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            tool_calls=tuple(tool_calls) if tool_calls else None,
        )


class LegacyFlagLLM(NativeFakeLLM):
    """Native-capable-looking fake with the flag off: must use the JSON path."""

    supports_native_tools = False


def _json_step(name, args, thought="Act."):
    return (
        [json.dumps(
            {"thought": thought, "action": {"name": name, "args": args}},
            ensure_ascii=False,
        )],
        None,
    )


def _config(**overrides):
    base = dict(max_react_steps=5, reviewers_enabled=[], planning_enabled=False)
    base.update(overrides)
    return AgentConfig(**base)


def _collect(events):
    async def _on_event(event):
        events.append(event)

    return _on_event


def _make_agent(llm, **config_overrides):
    critic = NativeFakeLLM([_json_step("conclude", {"answer": "critic"})])
    return ReActAgent(llm=llm, critic_llm=critic, config=_config(**config_overrides))


@pytest.mark.asyncio
async def test_native_full_step_streams_thought_and_applies_tool_call():
    llm = NativeFakeLLM([
        (
            ["Current target: final answer. ", "2+2 equals 4."],
            [ToolCall(name="conclude", arguments={"answer": "4"})],
        )
    ])
    agent = _make_agent(llm)
    events = []
    solution = await agent.solve("What is 2+2?", on_event=_collect(events))

    assert solution.final_answer == "4"
    turn = solution.turns[0]
    assert turn.action.name == "conclude"
    assert turn.action.args == {"answer": "4"}
    # Thought is the plain streamed text, no JSON blob parsing involved.
    assert turn.thought == "Current target: final answer. 2+2 equals 4."

    token_events = [e["content"] for e in events if e.get("type") == "token"]
    assert token_events == ["Current target: final answer. ", "2+2 equals 4."]

    # The native system prompt drops the JSON protocol and asks for one call.
    system = llm.system_prompts[0]
    assert "Output ONLY valid JSON" not in system
    assert "exactly ONE tool" in system
    # No JSON response format is forced; tool schemas are passed instead.
    assert llm.response_formats[0] is None
    schemas = llm.tools_seen[0]
    assert schemas, "expected tool schemas to be passed to the backend"
    by_name = {s["function"]["name"]: s for s in schemas}
    assert "conclude" in by_name
    assert by_name["conclude"]["function"]["parameters"]["required"] == ["answer"]


@pytest.mark.asyncio
async def test_native_multiple_tool_calls_uses_first(caplog):
    llm = NativeFakeLLM([
        (
            ["Answering now."],
            [
                ToolCall(name="conclude", arguments={"answer": "42"}),
                ToolCall(name="think", arguments={"text": "extra call"}),
            ],
        )
    ])
    agent = _make_agent(llm)
    with caplog.at_level("WARNING", logger="math_agent.agent"):
        solution = await agent.solve("What is the answer?")

    assert solution.final_answer == "42"
    assert solution.turns[0].action.name == "conclude"
    assert any("tool_calls" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_native_no_tool_call_degrades_to_think():
    llm = NativeFakeLLM([
        (["I should think first."], None),
        (
            ["Now I know."],
            [ToolCall(name="conclude", arguments={"answer": "done"})],
        ),
    ])
    agent = _make_agent(llm)
    solution = await agent.solve("Think about 1+1.")

    first, second = solution.turns[0], solution.turns[1]
    assert first.action.name == "think"
    assert first.action.args == {"text": "I should think first."}
    assert first.thought == "I should think first."
    assert second.action.name == "conclude"
    assert solution.final_answer == "done"


@pytest.mark.asyncio
async def test_legacy_json_path_when_flag_disabled():
    llm = LegacyFlagLLM([_json_step("conclude", {"answer": "legacy"})])
    agent = _make_agent(llm)
    events = []
    solution = await agent.solve("What is 2+2?", on_event=_collect(events))

    assert solution.final_answer == "legacy"
    assert solution.turns[0].action.name == "conclude"
    # Legacy path: JSON response format forced, no tool schemas passed.
    assert llm.response_formats[0] == {"type": "json_object"}
    assert llm.tools_seen[0] is None
    assert "Output ONLY valid JSON" in llm.system_prompts[0]


class _StubMcpClient:
    def __init__(self, input_schema):
        self.tools = {
            "mcp_solver": {
                "definition": {
                    "name": "solver",
                    "description": "external solver",
                    "input_schema": input_schema,
                },
                "server_name": "stub",
            }
        }

    @property
    def health(self):
        return {}


def test_native_tool_schemas_builtin_and_mcp_passthrough():
    input_schema = {
        "type": "object",
        "properties": {"expr": {"type": "string", "description": "expression"}},
        "required": ["expr"],
    }
    registry = ToolRegistry(
        enabled_tools=["compute", "search"],
        mcp_client=_StubMcpClient(input_schema),
    )
    descriptions = registry.describe_visible_tools(progressive=False)
    schemas = registry.native_tool_schemas(descriptions)
    by_name = {s["function"]["name"]: s["function"] for s in schemas}

    # Special actions always present with the static parameter table.
    assert by_name["think"]["parameters"]["required"] == ["text"]
    assert by_name["conclude"]["parameters"]["required"] == ["answer"]
    assert by_name["set_goal"]["parameters"]["required"] == ["goal"]
    # Builtins: static table; the search tool is disclosed as search_web.
    assert by_name["compute"]["parameters"]["required"] == ["code"]
    assert "search_web" in by_name
    # MCP tool: its own JSON schema passes through unchanged.
    assert by_name["mcp_solver"]["parameters"] == input_schema


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def _chunk(delta, usage=None):
    return _ns(choices=[_ns(delta=delta)], usage=usage)


def _tc_delta(index, name=None, arguments=None):
    return _ns(index=index, function=_ns(name=name, arguments=arguments))


def _make_stream_backend(monkeypatch, chunks):
    import openai

    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=_async_iter(chunks))
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: fake_client)
    return OpenAICompatibleBackend(model="gpt-5.5", api_key="sk-test")


async def _async_iter(items):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_stream_accumulates_tool_call_arguments_across_chunks(monkeypatch):
    chunks = [
        _chunk(_ns(content="thinking... ")),
        _chunk(_ns(content=None, tool_calls=[_tc_delta(0, name="compute", arguments='{"code": "pr')])),
        # A second, interleaved tool_call on another index (out of order).
        _chunk(_ns(content=None, tool_calls=[_tc_delta(1, name="think", arguments='{"text": "hi"}')])),
        _chunk(_ns(content=None, tool_calls=[_tc_delta(0, arguments='int(2+2)"}')])),
        _ns(choices=[], usage=_ns(prompt_tokens=5, completion_tokens=4, total_tokens=9)),
    ]
    backend = _make_stream_backend(monkeypatch, chunks)

    responses = [r async for r in backend.stream([Message(role="user", content="hi")])]

    assert responses[0].text == "thinking... "
    final = responses[-1]
    assert final.tool_calls == (
        ToolCall(name="compute", arguments={"code": "print(2+2)"}),
        ToolCall(name="think", arguments={"text": "hi"}),
    )


@pytest.mark.asyncio
async def test_complete_parses_tool_calls(monkeypatch):
    import openai

    response = _ns(
        choices=[
            _ns(
                message=_ns(
                    content="",
                    tool_calls=[
                        _ns(function=_ns(name="search_mathlib", arguments='{"query": "Nat.dvd_gcd"}'))
                    ],
                )
            )
        ],
        usage=_ns(prompt_tokens=10, completion_tokens=3, total_tokens=13),
    )
    fake_client = MagicMock()
    fake_client.chat.completions.create = AsyncMock(return_value=response)
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: fake_client)
    backend = OpenAICompatibleBackend(model="gpt-5.5", api_key="sk-test")

    result = await backend.complete(
        [Message(role="user", content="hi")],
        tools=[{"type": "function", "function": {"name": "search_mathlib"}}],
    )

    assert result.tool_calls == (
        ToolCall(name="search_mathlib", arguments={"query": "Nat.dvd_gcd"}),
    )
    # The tools kwarg was forwarded to the API call.
    kwargs = fake_client.chat.completions.create.call_args.kwargs
    assert kwargs["tools"] == [
        {"type": "function", "function": {"name": "search_mathlib"}}
    ]


@pytest.mark.asyncio
async def test_stream_without_tool_calls_yields_none(monkeypatch):
    chunks = [_chunk(_ns(content="plain text"))]
    backend = _make_stream_backend(monkeypatch, chunks)

    responses = [r async for r in backend.stream([Message(role="user", content="hi")])]

    assert responses[-1].tool_calls is None
