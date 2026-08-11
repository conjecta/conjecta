"""Terminal failure paths surface issues directly (no escalation machinery).

The old ``trace.escalation_signal`` existed only to auto-escalate failed normal
solves into research mode. Research mode is removed, so terminal paths now
stand on their verification issues / best-effort outcomes alone.
"""
from __future__ import annotations

import json

import pytest

from math_agent.agent.react_agent import ReActAgent
from math_agent.billing.models import LLMResponse
from math_agent.config import AgentConfig


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def _next_response(self) -> LLMResponse:
        response = self.responses[self.calls]
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
        self, messages, system=None, temperature=None, response_format=None, *, logprobs=False
    ):
        return self._next_response()

    async def stream(
        self, messages, system=None, response_format=None, *, logprobs=False
    ):
        response = self._next_response()
        yield response
        yield LLMResponse(
            text="",
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            mean_logprob=response.mean_logprob,
        )


def _action(name, args, thought="Act."):
    return json.dumps(
        {"thought": thought, "action": {"name": name, "args": args}},
        ensure_ascii=False,
    )


PROBLEM = "Prove that for every n >= 1 the sum of the first n odd numbers equals n^2."


@pytest.mark.asyncio
async def test_revision_exhaustion_returns_best_effort_with_issues():
    llm = FakeLLM([_action("conclude", {"answer": "by induction"}, thought="Done.")])
    critic = FakeLLM([
        '{"difficulty": "hard", "reason": "proof required"}',
        "VERDICT: FAIL\nISSUES: gap in inductive step\nSUGGESTIONS: fix\nCONFIDENCE: 0.9",
    ])
    config = AgentConfig(
        max_react_steps=3,
        max_conclusion_revisions=0,
        reviewers_enabled=["critic"],
        planning_enabled=False,
    )
    agent = ReActAgent(llm=llm, critic_llm=critic, config=config)
    solution = await agent.solve(PROBLEM)
    assert solution.verification_status == "best_effort"
    assert "gap in inductive step" in solution.verification_issues


@pytest.mark.asyncio
async def test_identical_action_limit_records_issue():
    repeated = _action("think", {"text": "stuck on the same idea"}, thought="Stuck.")
    llm = FakeLLM([repeated, repeated, repeated])
    config = AgentConfig(
        max_react_steps=6,
        max_identical_action_repeats=2,
        reviewers_enabled=[],
        planning_enabled=False,
    )
    agent = ReActAgent(llm=llm, critic_llm=FakeLLM([]), config=config)
    solution = await agent.solve(PROBLEM)
    assert any(
        turn.observation.error == "identical_action_limit"
        for turn in solution.trace.turns
    )
    assert any(
        "identical action" in issue for issue in solution.verification_issues
    )


@pytest.mark.asyncio
async def test_successful_solve_has_no_terminal_issues():
    llm = FakeLLM([_action("conclude", {"answer": "by induction"}, thought="Done.")])
    critic = FakeLLM([
        '{"difficulty": "hard", "reason": "proof required"}',
        "VERDICT: PASS\nISSUES: none\nSUGGESTIONS: none\nCONFIDENCE: 1.0",
    ])
    config = AgentConfig(
        max_react_steps=3,
        reviewers_enabled=["critic"],
        planning_enabled=False,
    )
    agent = ReActAgent(llm=llm, critic_llm=critic, config=config)
    solution = await agent.solve(PROBLEM)
    assert solution.verification_status == "reviewed"
    assert solution.verification_issues == []
