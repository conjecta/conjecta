"""Consistency tests: one canonical formal-evidence tool set.

FORMAL_ACTIONS in math_agent/agent/formal_evidence.py is the single source of
truth for which tools produce formal proof evidence. The conclude gate and the
ReActAgent evidence sites (knowledge promotion, solution.lean_proofs) must
derive from it so verified tactic_search / prove_by_lemmas proofs count.
"""
from __future__ import annotations

import asyncio
import inspect
import logging

import pytest

from math_agent.agent import conclude_gate, react_agent
from math_agent.agent.conclude_gate import ConcludeGate
from math_agent.agent.formal_evidence import FORMAL_ACTIONS, attach_formal_evidence
from math_agent.agent.react_agent import ReActAgent
from math_agent.agent.react_state import (
    Action,
    ReActTrace,
    ReActTurn,
    ToolObservation,
)
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


def test_formal_actions_is_canonical_set():
    assert FORMAL_ACTIONS == frozenset(
        {"formalize", "lean_check", "tactic_search", "prove_by_lemmas"}
    )


def test_conclude_gate_and_react_agent_derive_from_formal_actions():
    """No consumer may keep a drifted literal copy of the evidence tool set."""
    gate_source = inspect.getsource(conclude_gate)
    agent_source = inspect.getsource(react_agent)
    assert "FORMAL_ACTIONS" in gate_source
    assert "FORMAL_ACTIONS" in agent_source
    for source in (gate_source, agent_source):
        assert '{"formalize", "lean_check"}' not in source


def test_attach_formal_evidence_covers_search_tools():
    """tactic_search / prove_by_lemmas observations must carry evidence IDs."""
    for action_name, args in (
        ("tactic_search", {"theorem_statement": "theorem t : True := by"}),
        ("prove_by_lemmas", {"statement": "True"}),
    ):
        observation = ToolObservation(
            success=True,
            output="proof verified",
            lean_code="theorem conjecta_target : True := by trivial",
        )
        result = attach_formal_evidence(
            Action(name=action_name, args=args),
            observation,
            target_claim="True",
        )
        assert "Formal evidence ID: formal-" in result.output
        assert result.metadata["formal_evidence"]["action"] == action_name


def _gate(agent: ReActAgent, problem: str) -> ConcludeGate:
    goal_run = GoalRun.new(
        problem=problem,
        criteria=SuccessCriteria(
            require_final_answer=True,
            require_formal_verification=True,
            min_report_count=len(agent.reviewers),
            required_report_sources=tuple(r.name for r in agent.reviewers),
        ),
    )
    return ConcludeGate(
        agent,
        run_log=logging.getLogger("test.formal_evidence_consistency"),
        emit=_emit,
        on_checkpoint=None,
        deadline=asyncio.get_running_loop().time() + 60.0,
        goal_run=goal_run,
        goal_evaluator=GoalEvaluator(),
        require_formal_verification=True,
    )


@pytest.mark.asyncio
async def test_conclude_gate_binds_tactic_search_evidence():
    """A verified tactic_search proof satisfies the formal-evidence gate."""
    problem = "Prove True in Lean."
    agent = ReActAgent(
        llm=FakeLLM([]),
        critic_llm=FakeLLM([]),
        config=AgentConfig(reviewers_enabled=[], planning_enabled=False),
    )
    trace = ReActTrace(problem=problem, current_goal=problem)
    search_observation = attach_formal_evidence(
        Action(name="tactic_search", args={"theorem_statement": "theorem t : True := by"}),
        ToolObservation(
            success=True,
            output="Proof found after 2 attempts.",
            lean_code="theorem conjecta_target : True := by trivial",
        ),
        target_claim=problem,
    )
    evidence_id = search_observation.metadata["formal_evidence"]["id"]
    trace.turns.append(
        ReActTurn(
            thought="deep search",
            action=Action(
                name="tactic_search",
                args={"theorem_statement": "theorem t : True := by"},
            ),
            observation=search_observation,
            step_num=1,
        )
    )
    gate = _gate(agent, problem)

    decision = await gate.handle(
        trace=trace,
        action=Action(
            name="conclude", args={"answer": "proved", "evidence_id": evidence_id}
        ),
        thought="Done.",
        step_num=2,
        candidate_answer="proved",
        action_confidence=None,
        conclusion_revisions=0,
        verification_issues=[],
        accepted_formal_evidence_id="",
    )

    # The evidence is bound: the gate must not reject for missing formal
    # evidence (it may still run its normal evaluation path).
    assert all(
        turn.observation.error != "missing_formal_evidence" for turn in trace.turns
    )
    assert decision is not None
