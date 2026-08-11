"""Supervisor follow-up intent routing."""
from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from math_agent.agent.context_augmentor import AugmentationResult
from math_agent.agent.memory_consolidation import MemoryConsolidator
from math_agent.agent.react_state import ReActSolution, ReActTrace
from math_agent.agent.supervisor import SupervisorAgent
from math_agent.agent.supervisor_intake import IntakeResult
from math_agent.config import AgentConfig


@dataclass
class FakeAugmentor:
    calls: list

    async def augment(self, problem: str, project_id: str, **kwargs) -> AugmentationResult:
        self.calls.append((problem, project_id))
        return AugmentationResult(prompt=f"AUGMENTED::{problem}", memories_used=[])


@pytest.mark.asyncio
async def test_clarify_skips_knowledge_augment_and_uses_light_react(monkeypatch):
    events: list[dict] = []

    async def on_event(event: dict) -> None:
        events.append(event)

    intake = IntakeResult(strategy="react", intent="clarify")
    fake_intake = SimpleNamespace(analyze=AsyncMock(return_value=intake))
    augmentor = FakeAugmentor(calls=[])

    captured: dict = {}

    async def fake_run_react(
        self,
        problem,
        emit,
        run_log,
        session_id=None,
        attachments=None,
        config=None,
        context_preamble="",
        initial_trace=None,
        require_formal_verification=False,
        planning=None,
        strategy="react",
    ):
        captured["problem"] = problem
        captured["config"] = config
        captured["context_preamble"] = context_preamble
        captured["initial_trace"] = initial_trace
        captured["require_formal_verification"] = require_formal_verification
        solution = ReActSolution(problem=problem, turns=[], final_answer="because 1+1=2")
        trace = initial_trace or ReActTrace(
            problem=problem,
            current_goal=problem,
            context_preamble=context_preamble,
        )
        return solution, trace

    monkeypatch.setattr(SupervisorAgent, "_run_react", fake_run_react)

    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=MagicMock(),
        config=AgentConfig(memory_consolidation_enabled=True),
    )
    agent._intake = fake_intake
    agent._augmentor = augmentor

    solution = await agent.solve(
        "Conversation so far:\nUser: What is 1+1?\nAssistant: 2\n\nCurrent question:\nWhy?",
        on_event=on_event,
        has_conversation_history=True,
    )

    fake_intake.analyze.assert_awaited_once()
    assert fake_intake.analyze.await_args.kwargs.get("has_history") is True
    assert augmentor.calls == []
    assert captured["problem"].startswith("Conversation so far:")
    assert "AUGMENTED::" not in captured["problem"]
    assert captured["config"] is not None
    assert captured["config"].reviewers_enabled == []
    assert captured["config"].max_react_steps == 4
    assert any(
        e.get("type") == "stage_status" and "追问" in (e.get("message") or "")
        for e in events
    )
    assert solution.final_answer == "because 1+1=2"


@pytest.mark.asyncio
async def test_new_problem_keeps_full_pipeline(monkeypatch):
    intake = IntakeResult(strategy="react", intent="new_problem")
    fake_intake = SimpleNamespace(analyze=AsyncMock(return_value=intake))
    augmentor = FakeAugmentor(calls=[])
    captured: dict = {}

    async def fake_run_react(
        self,
        problem,
        emit,
        run_log,
        session_id=None,
        attachments=None,
        config=None,
        context_preamble="",
        initial_trace=None,
        require_formal_verification=False,
        planning=None,
        strategy="react",
    ):
        captured["problem"] = problem
        captured["config"] = config
        captured["context_preamble"] = context_preamble
        captured["initial_trace"] = initial_trace
        captured["require_formal_verification"] = require_formal_verification
        return ReActSolution(problem=problem, turns=[], final_answer="ok"), ReActTrace(
            problem=problem,
            current_goal=problem,
            context_preamble=context_preamble,
        )

    monkeypatch.setattr(SupervisorAgent, "_run_react", fake_run_react)

    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=MagicMock(),
        config=AgentConfig(memory_consolidation_enabled=False),
    )
    agent._intake = fake_intake
    agent._augmentor = augmentor

    await agent.solve("Prove that sqrt(2) is irrational", has_conversation_history=False)

    assert len(augmentor.calls) == 1
    assert captured["problem"] == "Prove that sqrt(2) is irrational"
    assert captured["context_preamble"] == "AUGMENTED::"
    assert captured["config"] is None or captured["config"].reviewers_enabled == agent.config.reviewers_enabled


@pytest.mark.asyncio
async def test_source_context_is_bounded_and_original_problem_stays_separate(monkeypatch):
    problem = "Explain the main theorem of the referenced paper."
    source_text = "S" * 120_000
    intake = IntakeResult(
        strategy="react",
        intent="new_problem",
        source_label="large paper",
        source_text=source_text,
    )
    captured: dict = {}

    async def fake_run_react(
        self,
        react_problem,
        emit,
        run_log,
        session_id=None,
        attachments=None,
        config=None,
        context_preamble="",
        initial_trace=None,
        require_formal_verification=False,
        planning=None,
        strategy="react",
    ):
        captured["problem"] = react_problem
        captured["context_preamble"] = context_preamble
        trace = initial_trace or ReActTrace(
            problem=react_problem,
            current_goal=react_problem,
            context_preamble=context_preamble,
        )
        solution = ReActSolution(
            problem=react_problem,
            turns=[],
            final_answer="ok",
            trace=trace,
        )
        return solution, trace

    monkeypatch.setattr(SupervisorAgent, "_run_react", fake_run_react)
    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=MagicMock(),
        config=AgentConfig(memory_consolidation_enabled=False),
    )
    agent._intake = SimpleNamespace(analyze=AsyncMock(return_value=intake))
    agent._augmentor = SimpleNamespace(
        augment=AsyncMock(
            return_value=AugmentationResult(
                prompt=f"Prior knowledge.\n\n{problem}",
                memories_used=[],
            )
        )
    )

    solution = await agent.solve(problem)

    assert captured["problem"] == problem
    assert source_text not in captured["problem"]
    assert 0 < len(captured["context_preamble"]) <= 12_000
    assert "S" * 1_000 in captured["context_preamble"]
    assert solution.trace is not None
    assert solution.trace.problem == problem


@pytest.mark.asyncio
async def test_explicit_formal_intent_flows_to_react_acceptance(monkeypatch):
    problem = "Formalize the claim in Lean 4."
    intake = IntakeResult(
        strategy="react",
        intent="new_problem",
        require_formal_verification=True,
    )
    captured: dict = {}

    async def fake_run_react(
        self,
        react_problem,
        emit,
        run_log,
        session_id=None,
        attachments=None,
        config=None,
        context_preamble="",
        initial_trace=None,
        require_formal_verification=False,
        planning=None,
        strategy="react",
    ):
        captured["require_formal_verification"] = require_formal_verification
        trace = ReActTrace(problem=react_problem, current_goal=react_problem)
        return ReActSolution(problem=react_problem, turns=[], final_answer="ok", trace=trace), trace

    monkeypatch.setattr(SupervisorAgent, "_run_react", fake_run_react)
    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=MagicMock(),
        config=AgentConfig(memory_consolidation_enabled=False),
    )
    agent._intake = SimpleNamespace(analyze=AsyncMock(return_value=intake))
    agent._augmentor = SimpleNamespace(
        augment=AsyncMock(
            return_value=AugmentationResult(prompt=problem, memories_used=[])
        )
    )

    await agent.solve(problem)

    assert captured["require_formal_verification"] is True


@pytest.mark.asyncio
async def test_post_solve_consolidation_uses_canonical_problem_not_source_body(monkeypatch):
    problem = "Explain the referenced theorem."
    source_marker = "SOURCE_BODY_TOKEN"
    source_text = source_marker + ("S" * (40_000 - len(source_marker)))
    intake = IntakeResult(
        strategy="react",
        intent="new_problem",
        source_label="paper",
        source_text=source_text,
    )
    captured: dict = {}

    async def fake_run_react(
        self,
        react_problem,
        emit,
        run_log,
        session_id=None,
        attachments=None,
        config=None,
        context_preamble="",
        initial_trace=None,
        require_formal_verification=False,
        planning=None,
        strategy="react",
    ):
        trace = ReActTrace(
            problem=react_problem,
            current_goal=react_problem,
            context_preamble=context_preamble,
        )
        solution = ReActSolution(
            problem=react_problem,
            turns=[],
            final_answer="answer",
            trace=trace,
        )
        return solution, trace

    async def capture_consolidation(self, trace, solution):
        captured["trace"] = trace
        captured["prompt"] = self._build_prompt(trace, solution)

    monkeypatch.setattr(SupervisorAgent, "_run_react", fake_run_react)
    monkeypatch.setattr(MemoryConsolidator, "consolidate", capture_consolidation)
    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=MagicMock(),
        config=AgentConfig(memory_consolidation_enabled=True),
    )
    agent._intake = SimpleNamespace(analyze=AsyncMock(return_value=intake))
    agent._augmentor = SimpleNamespace(
        augment=AsyncMock(
            return_value=AugmentationResult(prompt=problem, memories_used=[])
        )
    )

    await agent.solve(problem)

    assert captured["trace"].problem == problem
    assert len(captured["trace"].context_preamble) <= 12_000
    assert source_text not in captured["trace"].problem
    assert source_text not in captured["prompt"]
    assert source_marker not in captured["prompt"]
