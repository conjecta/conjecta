"""Direct unit tests for the extracted conclude gate.

The same paths are covered end-to-end via ``ReActAgent.solve`` elsewhere;
these tests pin the gate's own contract (``ConcludeDecision``) after the
extraction from the main loop.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from math_agent.agent.conclude_gate import ConcludeGate
from math_agent.agent.react_agent import ReActAgent
from math_agent.agent.react_state import Action, ReActTrace
from math_agent.billing.models import LLMResponse
from math_agent.config import AgentConfig
from math_agent.verification import GoalEvaluator, GoalRun, SuccessCriteria


class FakeLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def _next(self) -> LLMResponse:
        response = self.responses[self.calls]
        self.calls += 1
        if isinstance(response, LLMResponse):
            return response
        return LLMResponse(
            text=response, prompt_tokens=0, completion_tokens=0, total_tokens=0
        )

    async def complete(
        self, messages, system=None, temperature=None, response_format=None, *, logprobs=False
    ):
        return self._next()

    async def stream(
        self, messages, system=None, temperature=None, response_format=None, *, logprobs=False
    ):
        response = self._next()
        yield response
        yield LLMResponse(text="", prompt_tokens=0, completion_tokens=0, total_tokens=0)


async def _emit(_event):
    return None


def _deadline():
    return asyncio.get_running_loop().time() + 60.0


def _gate(agent: ReActAgent, problem: str, *, require_formal: bool) -> ConcludeGate:
    goal_run = GoalRun.new(
        problem=problem,
        criteria=SuccessCriteria(
            require_final_answer=True,
            require_formal_verification=require_formal,
            min_report_count=len(agent.reviewers),
            required_report_sources=tuple(r.name for r in agent.reviewers),
        ),
    )
    return ConcludeGate(
        agent,
        run_log=logging.getLogger("test.conclude_gate"),
        emit=_emit,
        on_checkpoint=None,
        deadline=_deadline(),
        goal_run=goal_run,
        goal_evaluator=GoalEvaluator(),
        require_formal_verification=require_formal,
    )


def _handle_kwargs(trace: ReActTrace, answer: str) -> dict:
    return {
        "trace": trace,
        "action": Action(name="conclude", args={"answer": answer}),
        "thought": "Done.",
        "step_num": 1,
        "candidate_answer": answer,
        "action_confidence": None,
        "conclusion_revisions": 0,
        "verification_issues": [],
        "accepted_formal_evidence_id": "",
    }


@pytest.mark.asyncio
async def test_unbound_formal_evidence_revises_without_reviewer_call():
    problem = "Prove that sqrt(2) is irrational."
    critic = FakeLLM([])
    agent = ReActAgent(
        llm=FakeLLM([]),
        critic_llm=critic,
        config=AgentConfig(reviewers_enabled=["critic"], planning_enabled=False),
    )
    trace = ReActTrace(problem=problem, current_goal=problem)
    gate = _gate(agent, problem, require_formal=True)

    decision = await gate.handle(**_handle_kwargs(trace, "42"))

    assert decision.revise is True
    assert trace.turns[-1].observation.error == "missing_formal_evidence"
    assert decision.verification_issues == ["Formal verification report is required."]
    assert critic.calls == 0


@pytest.mark.asyncio
async def test_easy_prompt_skip_accepts_without_reviewer_call():
    problem = "12*9=?"
    critic = FakeLLM([])
    agent = ReActAgent(
        llm=FakeLLM([]),
        critic_llm=critic,
        config=AgentConfig(
            reviewers_enabled=["critic"],
            planning_enabled=False,
            skip_review_on_easy_prompt=True,
        ),
    )
    trace = ReActTrace(problem=problem, current_goal=problem)
    gate = _gate(agent, problem, require_formal=False)

    decision = await gate.handle(**_handle_kwargs(trace, "108"))

    assert decision.revise is False
    assert decision.final_answer == "108"
    assert decision.verification_status == "unreviewed"
    assert critic.calls == 0
    metadata = trace.turns[-1].observation.metadata
    assert metadata["skipped_review"] is True
    assert metadata["skip_review_reason"] == "easy_prompt"


@pytest.mark.asyncio
async def test_exhausted_revisions_return_best_effort_and_surface_issues():
    problem = "Prove that for every n >= 1 the sum of the first n odd numbers equals n^2."
    critic = FakeLLM([
        '{"difficulty": "hard", "reason": "proof required"}',
        "VERDICT: FAIL\nISSUES: gap in inductive step\nSUGGESTIONS: fix\nCONFIDENCE: 0.9",
    ])
    agent = ReActAgent(
        llm=FakeLLM([]),
        critic_llm=critic,
        config=AgentConfig(
            reviewers_enabled=["critic"],
            planning_enabled=False,
            max_conclusion_revisions=0,
        ),
    )
    trace = ReActTrace(problem=problem, current_goal=problem)
    gate = _gate(agent, problem, require_formal=False)

    decision = await gate.handle(**_handle_kwargs(trace, "by induction"))

    assert decision.revise is False
    assert decision.final_answer == "by induction"
    assert decision.verification_status == "best_effort"
    assert "gap in inductive step" in decision.verification_issues
    assert critic.calls == 2  # difficulty classification + one review


@pytest.mark.asyncio
async def test_failing_review_within_budget_revises_and_counts():
    problem = "Prove that for every n >= 1 the sum of the first n odd numbers equals n^2."
    critic = FakeLLM([
        '{"difficulty": "hard", "reason": "proof required"}',
        "VERDICT: FAIL\nISSUES: gap\nSUGGESTIONS: fix\nCONFIDENCE: 0.9",
    ])
    agent = ReActAgent(
        llm=FakeLLM([]),
        critic_llm=critic,
        config=AgentConfig(
            reviewers_enabled=["critic"],
            planning_enabled=False,
            max_conclusion_revisions=2,
        ),
    )
    trace = ReActTrace(problem=problem, current_goal=problem)
    gate = _gate(agent, problem, require_formal=False)

    decision = await gate.handle(**_handle_kwargs(trace, "by induction"))

    assert decision.revise is True
    assert decision.conclusion_revisions == 1
    assert decision.verification_issues == ["gap"]
    assert trace.budget_consumption["conclusion_revisions"] == 1


@pytest.mark.asyncio
async def test_negative_revision_budget_means_unlimited_revisions():
    """max_conclusion_revisions < 0 must not trip the exhausted-budget path."""
    problem = "Prove that for every n >= 1 the sum of the first n odd numbers equals n^2."
    critic = FakeLLM([
        '{"difficulty": "hard", "reason": "proof required"}',
        "VERDICT: FAIL\nISSUES: gap\nSUGGESTIONS: fix\nCONFIDENCE: 0.9",
    ])
    agent = ReActAgent(
        llm=FakeLLM([]),
        critic_llm=critic,
        config=AgentConfig(
            reviewers_enabled=["critic"],
            planning_enabled=False,
            max_conclusion_revisions=-1,
        ),
    )
    trace = ReActTrace(problem=problem, current_goal=problem)
    gate = _gate(agent, problem, require_formal=False)

    decision = await gate.handle(**_handle_kwargs(trace, "by induction"))

    assert decision.revise is True
    assert decision.conclusion_revisions == 1
