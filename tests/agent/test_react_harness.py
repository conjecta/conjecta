"""Tests for the ReAct harness mechanisms: token-budget compaction, context
overflow recovery, prompt-cache-stable system prompts, the update_plan todo
action, and pre/post tool hooks.
"""
from __future__ import annotations

import json
import logging

import pytest

from math_agent.agent import hooks
from math_agent.agent.prompts import (
    build_react_native_system_prompt,
    build_react_system_prompt,
)
from math_agent.agent.react_agent import ReActAgent
from math_agent.agent.react_state import (
    Action,
    ReActTrace,
    ReActTurn,
    ToolObservation,
)
from math_agent.agent.tools import ToolRegistry, ToolResult
from math_agent.billing.models import LLMResponse
from math_agent.config import AgentConfig


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.messages_seen = []

    def _next_response(self) -> LLMResponse:
        response = self.responses[min(self.calls, len(self.responses) - 1)]
        self.calls += 1
        if isinstance(response, LLMResponse):
            return response
        return LLMResponse(
            text=response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )

    async def complete(
        self,
        messages,
        system=None,
        temperature=None,
        response_format=None,
        *,
        logprobs=False,
    ):
        return self._next_response()

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
        self.messages_seen.append(list(messages))
        response = self._next_response()
        yield LLMResponse(
            text=response.text,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )
        yield LLMResponse(
            text="",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            mean_logprob=response.mean_logprob,
        )


def _action(name, args, *, thought="Act."):
    return json.dumps(
        {"thought": thought, "action": {"name": name, "args": args}},
        ensure_ascii=False,
    )


def _conclude(answer):
    return _action("conclude", {"answer": answer}, thought="Done.")


def _turn(step_num, output="ok", *, action_name="think", chars=0):
    return ReActTurn(
        thought=f"thought {step_num}",
        action=Action(name=action_name, args={"text": f"t{step_num}"}),
        observation=ToolObservation(success=True, output=output * chars or output),
        step_num=step_num,
    )


def _config(**overrides):
    base = {"max_react_steps": 6, "reviewers_enabled": [], "planning_enabled": False}
    base.update(overrides)
    return AgentConfig(**base)


@pytest.fixture(autouse=True)
def _clear_hooks():
    hooks.clear_hooks()
    yield
    hooks.clear_hooks()


# ---------------------------------------------------------------------------
# 1. Token-budget-triggered proactive compaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_budget_compacts_before_window_overflow():
    # Four turns with bulky observations: well inside the 10-turn window, but
    # the rendered context far exceeds a 500-token budget.
    trace = ReActTrace(problem="p", current_goal="g")
    trace.turns = [_turn(i + 1, output="x", chars=2000) for i in range(4)]
    critic = FakeLLM(["running summary of earlier steps"])
    agent = ReActAgent(
        llm=FakeLLM([_conclude("done")]),
        critic_llm=critic,
        config=_config(react_context_max_tokens=500),
    )

    await agent._maybe_compact_context(trace, logging.getLogger("test"))

    assert trace.compacted_turn_count > 0
    assert trace.compacted_summary == "running summary of earlier steps"
    assert critic.calls == 1


@pytest.mark.asyncio
async def test_token_budget_not_exceeded_keeps_turns_uncompacted():
    trace = ReActTrace(problem="p", current_goal="g")
    trace.turns = [_turn(i + 1) for i in range(3)]
    critic = FakeLLM(["summary"])
    agent = ReActAgent(
        llm=FakeLLM([_conclude("done")]),
        critic_llm=critic,
        config=_config(react_context_max_tokens=100_000),
    )

    await agent._maybe_compact_context(trace, logging.getLogger("test"))

    assert trace.compacted_turn_count == 0
    assert trace.compacted_summary == ""
    assert critic.calls == 0


@pytest.mark.asyncio
async def test_window_overflow_semantics_unchanged():
    # Twelve tiny turns: the classic 10-turn window overflow still compacts
    # exactly the two oldest turns even with a generous token budget.
    trace = ReActTrace(problem="p", current_goal="g")
    trace.turns = [_turn(i + 1) for i in range(12)]
    critic = FakeLLM(["window summary"])
    agent = ReActAgent(
        llm=FakeLLM([_conclude("done")]),
        critic_llm=critic,
        config=_config(react_context_max_tokens=10**9),
    )

    await agent._maybe_compact_context(trace, logging.getLogger("test"))

    assert trace.compacted_turn_count == 2
    assert trace.compacted_summary == "window summary"


# ---------------------------------------------------------------------------
# 2. Context overflow recovery
# ---------------------------------------------------------------------------


class OverflowThenConcludeLLM:
    def __init__(self, answer, *, overflow_times=1):
        self.answer = answer
        self.overflow_times = overflow_times
        self.stream_calls = 0

    async def complete(self, *args, **kwargs):
        raise AssertionError("actor complete() should not be called")

    async def stream(self, messages, system=None, **kwargs):
        self.stream_calls += 1
        if self.stream_calls <= self.overflow_times:
            raise RuntimeError(
                "This model's maximum context length is 65536 tokens "
                "(context_length_exceeded)"
            )
        yield LLMResponse(
            text=_conclude(self.answer),
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


@pytest.mark.asyncio
async def test_context_overflow_compacts_and_retries_once():
    trace = ReActTrace(problem="p", current_goal="g")
    trace.turns = [_turn(i + 1, output="y", chars=500) for i in range(4)]
    actor = OverflowThenConcludeLLM("recovered answer")
    critic = FakeLLM(["overflow summary"])
    agent = ReActAgent(llm=actor, critic_llm=critic, config=_config())

    solution = await agent.solve("p", initial_trace=trace)

    assert solution.final_answer == "recovered answer"
    assert actor.stream_calls == 2
    # The older half of the in-window turns was folded into the summary.
    assert trace.compacted_turn_count == 2
    assert trace.compacted_summary == "overflow summary"


@pytest.mark.asyncio
async def test_context_overflow_second_failure_propagates():
    trace = ReActTrace(problem="p", current_goal="g")
    trace.turns = [_turn(i + 1, output="y", chars=500) for i in range(4)]
    actor = OverflowThenConcludeLLM("never", overflow_times=10)
    critic = FakeLLM(["overflow summary"])
    agent = ReActAgent(llm=actor, critic_llm=critic, config=_config())

    with pytest.raises(RuntimeError, match="maximum context length"):
        await agent.solve("p", initial_trace=trace)

    # Exactly one retry after the forced compaction.
    assert actor.stream_calls == 2


# ---------------------------------------------------------------------------
# 3. Prompt cache stabilization
# ---------------------------------------------------------------------------


def test_system_prompt_static_prefix_precedes_tool_list():
    prompt_a = build_react_system_prompt(
        tool_descriptions="- compute(...)",
        require_formal_verification=False,
    )
    prompt_b = build_react_system_prompt(
        tool_descriptions="- lean_check(...)\n- search_mathlib(...)",
        require_formal_verification=False,
    )

    marker = "Available actions:\n"
    assert marker in prompt_a and marker in prompt_b
    prefix_a, tools_a = prompt_a.split(marker, 1)
    prefix_b, tools_b = prompt_b.split(marker, 1)
    # The whole static prefix (role, protocol, workflow, constraints, date)
    # is byte-identical across step-varying tool lists.
    assert prefix_a == prefix_b
    assert "- compute(...)" in tools_a
    assert "- lean_check(...)" in tools_b
    # All static content landed in the prefix; the suffix is only tools.
    assert "Output ONLY valid JSON" in prefix_a
    assert "Decision workflow" in prefix_a
    assert "Additional constraints" in prefix_a
    assert "Current date (UTC):" in prefix_a
    assert "Decision workflow" not in tools_a


def test_native_system_prompt_static_prefix_precedes_tool_list():
    prompt_a = build_react_native_system_prompt(
        tool_descriptions="- compute(...)",
        require_formal_verification=True,
    )
    prompt_b = build_react_native_system_prompt(
        tool_descriptions="- formalize(...)",
        require_formal_verification=True,
    )

    marker = "Available tools (also provided as callable functions):\n"
    assert marker in prompt_a and marker in prompt_b
    prefix_a, tools_a = prompt_a.split(marker, 1)
    prefix_b, _ = prompt_b.split(marker, 1)
    assert prefix_a == prefix_b
    assert "- compute(...)" in tools_a
    assert "Formal verification workflow" in prefix_a


# ---------------------------------------------------------------------------
# 4. update_plan todo action
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_plan_stores_items_and_injects_them_into_context():
    items = [
        {"content": "Prove lemma A", "status": "in_progress"},
        {"content": "Assemble final theorem", "status": "pending"},
    ]
    llm = FakeLLM([
        _action("update_plan", {"items": items}),
        _conclude("QED"),
    ])
    agent = ReActAgent(llm=llm, critic_llm=FakeLLM(["summary"]), config=_config())

    solution = await agent.solve("Prove something")

    assert solution.final_answer == "QED"
    assert solution.trace is not None
    assert solution.trace.plan_items == items
    # The todo list is injected into the context of the next step.
    second_step_messages = llm.messages_seen[1]
    assert "Todo checklist:" in second_step_messages[0].content
    assert "- [in_progress] Prove lemma A" in second_step_messages[0].content
    # Special action: does not consume the tool budget.
    assert solution.trace.budget_consumption.get("tool_calls", 0) == 0


@pytest.mark.asyncio
async def test_update_plan_truncates_to_twenty_items():
    items = [
        {"content": f"task {i}", "status": "pending"} for i in range(25)
    ]
    llm = FakeLLM([
        _action("update_plan", {"items": items}),
        _conclude("QED"),
    ])
    agent = ReActAgent(llm=llm, critic_llm=FakeLLM(["summary"]), config=_config())

    solution = await agent.solve("Prove something")

    assert solution.trace is not None
    assert len(solution.trace.plan_items) == 20
    assert solution.trace.plan_items[-1]["content"] == "task 19"


@pytest.mark.asyncio
async def test_update_plan_without_items_list_is_invalid():
    llm = FakeLLM([
        _action("update_plan", {"items": "not-a-list"}),
        _conclude("QED"),
    ])
    agent = ReActAgent(llm=llm, critic_llm=FakeLLM(["summary"]), config=_config())

    solution = await agent.solve("Prove something")

    assert solution.trace is not None
    first = solution.trace.turns[0]
    assert first.action.name == "update_plan"
    assert first.observation.error == "invalid_action_args"
    assert solution.trace.plan_items == []


def test_plan_items_checkpoint_round_trip():
    trace = ReActTrace(problem="p", current_goal="g")
    trace.plan_items = [
        {"content": "a", "status": "done"},
        {"content": "b", "status": "bogus-status"},
    ]
    restored = ReActTrace.from_checkpoint(trace.to_checkpoint())
    assert restored.plan_items == [
        {"content": "a", "status": "done"},
        {"content": "b", "status": "pending"},
    ]


# ---------------------------------------------------------------------------
# 5. Pre/PostToolUse hooks
# ---------------------------------------------------------------------------


def _echo_registry() -> ToolRegistry:
    registry = ToolRegistry(enabled_tools=[])

    async def _echo(text, _ctx):
        return ToolResult(name="echo", output=f"echo:{text}", success=True)

    registry.register(
        "echo",
        _echo,
        description="echo the text back",
        args_example='{"text": "..."}',
        arg_map="text",
    )
    return registry


@pytest.mark.asyncio
async def test_pre_tool_hook_blocks_call_without_consuming_budget():
    def _veto(name, args):
        if name == "echo":
            raise PermissionError("echo is disabled by policy")

    hooks.register_pre_tool_hook(_veto)
    llm = FakeLLM([
        _action("echo", {"text": "hi"}),
        _conclude("fallback answer"),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=FakeLLM(["summary"]),
        config=_config(),
        tool_registry=_echo_registry(),
    )

    solution = await agent.solve("Say hi")

    assert solution.final_answer == "fallback answer"
    assert solution.trace is not None
    first = solution.trace.turns[0]
    assert first.action.name == "echo"
    assert first.observation.success is False
    assert first.observation.error == "blocked_by_hook"
    assert "echo is disabled by policy" in first.observation.output
    # The vetoed call did not consume the tool budget.
    assert solution.trace.budget_consumption.get("tool_calls", 0) == 0


@pytest.mark.asyncio
async def test_post_tool_hook_observes_and_own_failure_is_swallowed():
    seen = []

    def _observer(name, args, observation):
        seen.append((name, dict(args), observation.output))

    def _broken(name, args, observation):
        raise RuntimeError("hook bug")

    hooks.register_post_tool_hook(_observer)
    hooks.register_post_tool_hook(_broken)
    llm = FakeLLM([
        _action("echo", {"text": "hi"}),
        _conclude("done"),
    ])
    agent = ReActAgent(
        llm=llm,
        critic_llm=FakeLLM(["summary"]),
        config=_config(),
        tool_registry=_echo_registry(),
    )

    solution = await agent.solve("Say hi")

    assert solution.final_answer == "done"
    assert seen == [("echo", {"text": "hi"}, "echo:hi")]
    assert solution.trace is not None
    assert solution.trace.budget_consumption.get("tool_calls") == 1


@pytest.mark.asyncio
async def test_hooks_do_not_fire_for_special_actions():
    calls = []

    def _pre(name, args):
        calls.append(name)

    hooks.register_pre_tool_hook(_pre)
    llm = FakeLLM([
        _action("update_plan", {"items": [{"content": "x", "status": "pending"}]}),
        _conclude("done"),
    ])
    agent = ReActAgent(llm=llm, critic_llm=FakeLLM(["summary"]), config=_config())

    solution = await agent.solve("Prove something")

    assert solution.final_answer == "done"
    assert calls == []
