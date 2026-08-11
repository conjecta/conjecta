"""Unit tests for mid-trace verification checkpoints."""
from __future__ import annotations

import asyncio
import json
import logging

import pytest

from math_agent.agent.mid_verify import (
    MidVerifyResult,
    format_mid_verify_note,
    run_mid_verify,
)
from math_agent.agent.react_agent import ReActAgent
from math_agent.agent.react_state import (
    Action,
    ReActTrace,
    ReActTurn,
    ToolObservation,
)
from math_agent.billing.models import LLMResponse
from math_agent.config import AgentConfig


class FakeLLM:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = 0

    async def complete(self, messages, system=None, temperature=None, response_format=None):
        text = self.responses[self.calls]
        self.calls += 1
        return LLMResponse(
            text=text,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


class FakeRegistry:
    def __init__(self, available, observation=None):
        self.available = list(available)
        self.observation = observation
        self.calls: list[Action] = []

    async def execute_action(self, action, context):
        self.calls.append(action)
        return self.observation


def _turn(thought="We derived the value.", action_name="think", output="x = 42."):
    return ReActTurn(
        thought=thought,
        action=Action(name=action_name, args={"text": thought}),
        observation=ToolObservation(success=True, output=output),
        step_num=1,
    )


PROBLEM = "Prove that for every n >= 1 the sum of the first n odd numbers equals n^2."


@pytest.mark.asyncio
async def test_judge_not_checkable_returns_unchecked():
    critic = FakeLLM([
        '{"checkable": false, "claim": "", "method": "none", "reason": "planning step"}'
    ])
    result = await run_mid_verify(
        turn=_turn(),
        problem=PROBLEM,
        llm=FakeLLM([]),
        critic_llm=critic,
        tool_registry=None,
    )
    assert result.checked is False
    assert format_mid_verify_note(result) == ""


@pytest.mark.asyncio
async def test_heuristic_skips_judge_for_pure_prose_step():
    """Steps with no numeric/relational content never reach the judge LLM."""
    critic = FakeLLM([])
    turn = ReActTurn(
        thought="I will look up the relevant literature before proving.",
        action=Action(name="search", args={"query": "odd sum identity proof"}),
        observation=ToolObservation(success=True, output="several papers found"),
        step_num=1,
    )
    result = await run_mid_verify(
        turn=turn,
        problem=PROBLEM,
        llm=FakeLLM([]),
        critic_llm=critic,
        tool_registry=None,
    )
    assert result.checked is False
    assert result.summary.startswith("heuristic:")
    assert critic.calls == 0


@pytest.mark.asyncio
async def test_compute_counterexample_marks_failed(monkeypatch):
    async def fake_refute(**kwargs):
        return {
            "counterexample_found": True,
            "summary": "n=0 violates the claim",
            "revised_statement": "Require n >= 1.",
        }

    monkeypatch.setattr(
        "math_agent.agent.mid_verify.run_computational_refute", fake_refute
    )
    critic = FakeLLM([
        '{"checkable": true, "claim": "sum holds for all n", "method": "compute", "reason": "numeric"}'
    ])
    result = await run_mid_verify(
        turn=_turn(),
        problem=PROBLEM,
        llm=FakeLLM([]),
        critic_llm=critic,
        tool_registry=FakeRegistry(["compute"]),
    )
    assert result.checked is True
    assert result.method == "compute"
    assert result.passed is False
    assert result.revised_claim == "Require n >= 1."
    note = format_mid_verify_note(result)
    assert "[mid-verify FAILED]" in note
    assert "Do not build on this claim" in note


@pytest.mark.asyncio
async def test_compute_clean_marks_passed(monkeypatch):
    async def fake_refute(**kwargs):
        return {"counterexample_found": False, "summary": "tested n up to 1000"}

    monkeypatch.setattr(
        "math_agent.agent.mid_verify.run_computational_refute", fake_refute
    )
    critic = FakeLLM([
        '{"checkable": true, "claim": "sum holds for all n", "method": "compute", "reason": "numeric"}'
    ])
    result = await run_mid_verify(
        turn=_turn(),
        problem=PROBLEM,
        llm=FakeLLM([]),
        critic_llm=critic,
        tool_registry=FakeRegistry(["compute"]),
    )
    assert result.checked is True
    assert result.passed is True
    assert "[mid-verify]" in format_mid_verify_note(result)


@pytest.mark.asyncio
async def test_compute_unavailable_status_unchecked(monkeypatch):
    async def fake_refute(**kwargs):
        return {"counterexample_found": False, "summary": "none", "status": "unavailable"}

    monkeypatch.setattr(
        "math_agent.agent.mid_verify.run_computational_refute", fake_refute
    )
    critic = FakeLLM([
        '{"checkable": true, "claim": "c", "method": "compute", "reason": "r"}'
    ])
    result = await run_mid_verify(
        turn=_turn(),
        problem=PROBLEM,
        llm=FakeLLM([]),
        critic_llm=critic,
        tool_registry=FakeRegistry([]),
    )
    assert result.checked is False


@pytest.mark.asyncio
async def test_lean_formalize_unavailable_unchecked():
    critic = FakeLLM([
        '{"checkable": true, "claim": "a <= b", "method": "lean", "reason": "derivation"}'
    ])
    result = await run_mid_verify(
        turn=_turn(),
        problem=PROBLEM,
        llm=FakeLLM([]),
        critic_llm=critic,
        tool_registry=FakeRegistry(["compute"]),
    )
    assert result.checked is False


@pytest.mark.asyncio
async def test_lean_failure_marks_failed():
    observation = ToolObservation(success=False, output="type mismatch", error="lean_error")
    registry = FakeRegistry(["formalize"], observation=observation)
    critic = FakeLLM([
        '{"checkable": true, "claim": "a <= b", "method": "lean", "reason": "derivation"}'
    ])
    result = await run_mid_verify(
        turn=_turn(),
        problem=PROBLEM,
        llm=FakeLLM([]),
        critic_llm=critic,
        tool_registry=registry,
    )
    assert result.checked is True
    assert result.method == "lean"
    assert result.passed is False
    assert registry.calls[0].name == "formalize"
    assert registry.calls[0].args == {"statement": "a <= b"}


async def _emit(event):
    return None


def _deadline():
    return asyncio.get_running_loop().time() + 60.0


def _agent(config: AgentConfig, critic: FakeLLM) -> ReActAgent:
    return ReActAgent(llm=FakeLLM([]), critic_llm=critic, config=config)


def _trace_with_turns(count: int, problem: str = PROBLEM) -> ReActTrace:
    trace = ReActTrace(problem=problem)
    for index in range(count):
        trace.turns.append(
            ReActTurn(
                thought="derived partial sum k = k^2",
                action=Action(name="think", args={"text": "partial sum k = k^2"}),
                observation=ToolObservation(success=True, output="k = 2"),
                step_num=index + 1,
            )
        )
    trace.next_step_num = count + 1
    return trace


@pytest.mark.asyncio
async def test_maybe_mid_verify_disabled_by_default():
    critic = FakeLLM([])
    agent = _agent(AgentConfig(), critic)
    trace = _trace_with_turns(2)
    issue = await agent._maybe_mid_verify(
        trace, trace.turns[-1], _emit, _deadline(), logging.getLogger("test")
    )
    assert issue is None
    assert critic.calls == 0


@pytest.mark.asyncio
async def test_maybe_mid_verify_interval_skips_odd_turns():
    critic = FakeLLM([])
    agent = _agent(
        AgentConfig(mid_verify_enabled=True, mid_verify_every=2), critic
    )
    trace = _trace_with_turns(1)
    issue = await agent._maybe_mid_verify(
        trace, trace.turns[-1], _emit, _deadline(), logging.getLogger("test")
    )
    assert issue is None
    assert critic.calls == 0


@pytest.mark.asyncio
async def test_maybe_mid_verify_budget_exhausted():
    critic = FakeLLM([])
    agent = _agent(
        AgentConfig(mid_verify_enabled=True, mid_verify_every=1, mid_verify_max_calls=0),
        critic,
    )
    trace = _trace_with_turns(1)
    issue = await agent._maybe_mid_verify(
        trace, trace.turns[-1], _emit, _deadline(), logging.getLogger("test")
    )
    assert issue is None
    assert critic.calls == 0


@pytest.mark.asyncio
async def test_maybe_mid_verify_skips_easy_prompt():
    critic = FakeLLM([])
    agent = _agent(
        AgentConfig(mid_verify_enabled=True, mid_verify_every=1), critic
    )
    trace = _trace_with_turns(1, problem="1+1=?")
    issue = await agent._maybe_mid_verify(
        trace, trace.turns[-1], _emit, _deadline(), logging.getLogger("test")
    )
    assert issue is None
    assert critic.calls == 0


@pytest.mark.asyncio
async def test_maybe_mid_verify_skips_research_mode():
    critic = FakeLLM([])
    agent = _agent(
        AgentConfig(mid_verify_enabled=True, mid_verify_every=1), critic
    )
    trace = _trace_with_turns(1)
    trace.research_mode = True
    issue = await agent._maybe_mid_verify(
        trace, trace.turns[-1], _emit, _deadline(), logging.getLogger("test")
    )
    assert issue is None
    assert critic.calls == 0


@pytest.mark.asyncio
async def test_maybe_mid_verify_failure_injects_note_and_flags_exhaustion(monkeypatch):
    async def fake_refute(**kwargs):
        return {
            "counterexample_found": True,
            "summary": "counterexample at n=0",
            "revised_statement": "Require n >= 1.",
        }

    monkeypatch.setattr(
        "math_agent.agent.mid_verify.run_computational_refute", fake_refute
    )
    judge = '{"checkable": true, "claim": "sum holds", "method": "compute", "reason": "r"}'
    hard = '{"difficulty": "hard", "reason": "proof required"}'
    critic = FakeLLM([hard, judge, judge])
    agent = _agent(
        AgentConfig(
            mid_verify_enabled=True,
            mid_verify_every=1,
            mid_verify_max_calls=3,
            mid_verify_max_corrections=2,
        ),
        critic,
    )
    trace = _trace_with_turns(1)

    first = await agent._maybe_mid_verify(
        trace, trace.turns[-1], _emit, _deadline(), logging.getLogger("test")
    )
    assert first is not None and "sum holds" in first
    assert "unresolved" not in first
    assert "[mid-verify FAILED]" in trace.turns[-1].observation.output
    assert trace.budget_consumption["mid_verify_calls"] == 1
    assert trace.budget_consumption["mid_verify_corrections"] == 1
    assert len(trace.mid_verifications) == 1

    second = await agent._maybe_mid_verify(
        trace, trace.turns[-1], _emit, _deadline(), logging.getLogger("test")
    )
    assert second is not None
    assert trace.budget_consumption["mid_verify_corrections"] == 2
    # Once the correction budget is exhausted, the issue itself flags the
    # claim as unresolved so it surfaces in the final verification issues.
    assert "unresolved" in second


def test_checkpoint_roundtrip_preserves_mid_verify_fields():
    trace = ReActTrace(problem=PROBLEM)
    trace.mid_verifications.append(
        MidVerifyResult(
            checked=True, method="compute", claim="c", passed=False, summary="s"
        ).to_dict()
    )
    restored = ReActTrace.from_checkpoint(trace.to_checkpoint())
    assert restored.mid_verifications[0]["claim"] == "c"
    assert restored.mid_verifications[0]["passed"] is False


def _action(name, args, thought="Act."):
    return json.dumps(
        {"thought": thought, "action": {"name": name, "args": args}},
        ensure_ascii=False,
    )


class StreamFakeLLM(FakeLLM):
    async def stream(self, messages, system=None, response_format=None, *, logprobs=False):
        yield await self.complete(messages, system, response_format)


@pytest.mark.asyncio
async def test_solve_hook_does_not_consume_tool_budget(monkeypatch):
    async def fake_refute(**kwargs):
        return {"counterexample_found": False, "summary": "no violation found"}

    monkeypatch.setattr(
        "math_agent.agent.mid_verify.run_computational_refute", fake_refute
    )
    llm = StreamFakeLLM([
        _action("think", {"text": "partial sum = k^2"}, thought="Derived partial sum = k^2."),
        _action("conclude", {"answer": "by induction"}, thought="Done."),
    ])
    critic = FakeLLM([
        '{"difficulty": "hard", "reason": "proof required"}',
        '{"checkable": true, "claim": "partial sum = k^2", "method": "compute", "reason": "numeric"}'
    ])
    config = AgentConfig(
        max_react_steps=5,
        reviewers_enabled=[],
        planning_enabled=False,
        mid_verify_enabled=True,
        mid_verify_every=1,
    )
    agent = ReActAgent(llm=llm, critic_llm=critic, config=config)
    solution = await agent.solve(PROBLEM)
    assert solution.final_answer == "by induction"
    trace = solution.trace
    assert trace.budget_consumption.get("tool_calls", 0) == 0
    assert trace.budget_consumption.get("mid_verify_calls", 0) == 1
    assert "[mid-verify]" in trace.turns[0].observation.output
