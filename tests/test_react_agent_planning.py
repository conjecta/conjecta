import json

import pytest

from math_agent.agent.planner import UnifiedPlanner
from math_agent.agent.react_agent import ReActAgent
from math_agent.agent.react_state import (
    Action,
    ReActTrace,
    ReActTurn,
    ToolObservation,
)
from math_agent.billing.models import LLMResponse
from math_agent.config import AgentConfig

CONCLUDE = '{"thought": "done", "action": {"name": "conclude", "args": {"answer": "42"}}}'
HARD_PROBLEM = "Prove that sqrt(2) is irrational."
EASY_PROBLEM = "compute 12*9"
UNMATCHED_PROBLEM = "Determine the chromatic number of the Petersen graph."
HARD_CLASSIFICATION = '{"difficulty": "hard", "reason": "proof required"}'
EASY_CLASSIFICATION = '{"difficulty": "easy", "reason": "trivial arithmetic"}'


def _plan(strategy="Use contradiction.", steps=None, done_when="answer stated"):
    return json.dumps(
        {
            "informal": {
                "strategy": strategy,
                "steps": steps or ["assume rational", "derive contradiction"],
                "watch_outs": ["coprimality"],
                "done_when": done_when,
            },
            "formalization": {"restatement": "", "goal_type": "", "lemmas": []},
        }
    )


class RecordingLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.log = []
        self.complete_systems = []
        self.stream_messages = []

    def _next(self):
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def complete(
        self, messages, system="", temperature=None, response_format=None, *, logprobs=False
    ):
        self.log.append("complete")
        self.complete_systems.append(system)
        return LLMResponse(
            text=self._next(), prompt_tokens=1, completion_tokens=1, total_tokens=2
        )

    async def stream(
        self, messages, system="", temperature=None, response_format=None, *, logprobs=False
    ):
        self.log.append("stream")
        self.stream_messages.append(messages)
        yield LLMResponse(
            text=self._next(), prompt_tokens=0, completion_tokens=0, total_tokens=0
        )
        yield LLMResponse(text="", prompt_tokens=1, completion_tokens=1, total_tokens=2)


def make_agent(llm, **config_kwargs):
    config_kwargs.setdefault("reviewers_enabled", [])
    return ReActAgent(llm=llm, critic_llm=llm, config=AgentConfig(**config_kwargs))


@pytest.mark.asyncio
async def test_non_easy_prompt_plans_before_loop():
    llm = RecordingLLM([HARD_CLASSIFICATION, _plan(strategy="1. strategy"), CONCLUDE])
    events = []

    async def on_event(event):
        events.append(event)

    await make_agent(llm).solve(HARD_PROBLEM, on_event=on_event)
    # First complete call classifies prompt difficulty; the second is the planner.
    assert llm.log == ["complete", "complete", "stream"]
    from math_agent.agent.prompts import PROMPT_DIFFICULTY_SYSTEM

    assert llm.complete_systems[0] == PROMPT_DIFFICULTY_SYSTEM
    # The planner system prompt is wrapped by with_time_context(), so it now
    # carries a date preamble after UNIFIED plan instructions.
    assert llm.complete_systems[1].startswith(UnifiedPlanner.SYSTEM)
    assert "Current date (UTC):" in llm.complete_systems[1]
    assert "1. strategy" in str(llm.stream_messages[0][0].content)
    assert any(
        e.get("type") == "stage_status" and e.get("stage") == "planning" for e in events
    )


@pytest.mark.asyncio
async def test_unmatched_prompt_also_plans():
    llm = RecordingLLM([HARD_CLASSIFICATION, _plan(), CONCLUDE])
    await make_agent(llm).solve(UNMATCHED_PROBLEM)
    assert llm.log == ["complete", "complete", "stream"]


@pytest.mark.asyncio
async def test_easy_prompt_skips_planning():
    llm = RecordingLLM([EASY_CLASSIFICATION, CONCLUDE])
    await make_agent(llm).solve(EASY_PROBLEM)
    assert llm.log == ["complete", "stream"]


@pytest.mark.asyncio
async def test_planning_disabled_skips_planning():
    llm = RecordingLLM([CONCLUDE])
    await make_agent(llm, planning_enabled=False).solve(HARD_PROBLEM)
    assert llm.log == ["stream"]


@pytest.mark.asyncio
async def test_resumed_trace_skips_planning():
    llm = RecordingLLM([CONCLUDE])
    trace = ReActTrace(problem=HARD_PROBLEM, current_goal=HARD_PROBLEM)
    trace.turns.append(
        ReActTurn(
            thought="t",
            action=Action(name="think", args={"text": "x"}),
            observation=ToolObservation(success=True, output="ok"),
            step_num=1,
        )
    )
    trace.next_step_num = 2
    await make_agent(llm).solve(HARD_PROBLEM, initial_trace=trace)
    assert llm.log == ["stream"]


@pytest.mark.asyncio
async def test_planning_failure_falls_back_to_plain_solve():
    llm = RecordingLLM([HARD_CLASSIFICATION, RuntimeError("boom"), CONCLUDE])
    solution = await make_agent(llm).solve(HARD_PROBLEM)
    assert solution.final_answer == "42"
    assert llm.log == ["complete", "complete", "stream"]


@pytest.mark.asyncio
async def test_plan_truncated_to_planning_max_chars():
    llm = RecordingLLM([
        HARD_CLASSIFICATION,
        json.dumps({"informal": "x" * 5000, "formalization": {}}),
        CONCLUDE,
    ])
    await make_agent(llm, planning_max_chars=100).solve(HARD_PROBLEM)
    content = str(llm.stream_messages[0][0].content)
    assert "x" * 100 in content
    assert "x" * 101 not in content


@pytest.mark.asyncio
async def test_actor_window_uses_react_context_max_chars(monkeypatch):
    from math_agent.agent.react_state import ReActTrace

    seen = {}
    original = ReActTrace.context_window

    def spy(self, max_turns=10, *, max_chars=24_000):
        seen["max_chars"] = max_chars
        return original(self, max_turns, max_chars=max_chars)

    monkeypatch.setattr(ReActTrace, "context_window", spy)
    llm = RecordingLLM([HARD_CLASSIFICATION, _plan(), CONCLUDE])
    await make_agent(llm, react_context_max_chars=9000).solve(HARD_PROBLEM)
    assert seen["max_chars"] == 9000


@pytest.mark.asyncio
async def test_actor_window_defaults_to_16000(monkeypatch):
    from math_agent.agent.react_state import ReActTrace

    seen = {}
    original = ReActTrace.context_window

    def spy(self, max_turns=10, *, max_chars=24_000):
        seen["max_chars"] = max_chars
        return original(self, max_turns, max_chars=max_chars)

    monkeypatch.setattr(ReActTrace, "context_window", spy)
    llm = RecordingLLM([HARD_CLASSIFICATION, _plan(), CONCLUDE])
    await make_agent(llm).solve(HARD_PROBLEM)
    assert seen["max_chars"] == 16_000


@pytest.mark.asyncio
async def test_research_trace_keeps_research_window_cap(monkeypatch):
    from math_agent.agent.react_state import ReActTrace

    seen = {}
    original = ReActTrace.context_window

    def spy(self, max_turns=10, *, max_chars=24_000):
        seen["max_chars"] = max_chars
        return original(self, max_turns, max_chars=max_chars)

    monkeypatch.setattr(ReActTrace, "context_window", spy)
    llm = RecordingLLM([CONCLUDE])
    agent = make_agent(llm, react_context_max_chars=9000, planning_enabled=False)
    trace = ReActTrace(problem="Prove X", current_goal="Prove X", research_mode=True)
    await agent.solve("Prove X", initial_trace=trace)
    assert seen["max_chars"] == 24_000


@pytest.mark.asyncio
async def test_pending_interaction_resume_skips_planning():
    llm = RecordingLLM([CONCLUDE])
    agent = make_agent(llm)
    trace = ReActTrace(problem=HARD_PROBLEM, current_goal=HARD_PROBLEM)
    trace.pending_interaction = {
        "kind": "tool_approval",
        "request_id": "r1",
        "allowed_decisions": ["approve", "reject", "edit", "respond"],
        "details": {
            "action": {"name": "think", "args": {"text": "x"}},
            "step_num": 1,
        },
    }
    await agent.solve(
        HARD_PROBLEM,
        initial_trace=trace,
        human_decision={"request_id": "r1", "decision": "approve"},
    )
    assert llm.log == ["stream"]
