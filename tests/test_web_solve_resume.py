from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import math_agent.web.agent_factory as web_app
import math_agent.web.solve_session as solve_session
from math_agent.agent.react_state import (
    CONTEXT_PREAMBLE_MAX_CHARS,
    Action,
    ProjectContext,
    ReActSolution,
    ReActTrace,
    ReActTurn,
    ToolObservation,
)
from math_agent.agent.context_augmentor import AugmentationResult
from math_agent.agent.supervisor import SupervisorAgent
from math_agent.billing.models import LLMResponse
from math_agent.agent.supervisor_intake import IntakeResult
from math_agent.config import AgentConfig


@pytest.mark.asyncio
async def test_cloud_enabled_checkpoint_resume_uses_tenant_local_store(monkeypatch):
    trace = ReActTrace(
        problem="Prove P",
        current_goal="prove the remaining case",
        next_step_num=3,
        project_context=ProjectContext(project_id="project-from-checkpoint"),
    )
    trace.turns.append(
        ReActTurn(
            thought="Handled the base case.",
            action=Action(name="think", args={"text": "base case"}),
            observation=ToolObservation(success=True, output="recorded"),
            step_num=2,
        )
    )
    checkpoint = trace.to_checkpoint()
    captured: dict[str, Any] = {}

    class FakeAgent:
        async def solve(self, problem: str, **kwargs: Any) -> ReActSolution:
            captured["problem"] = problem
            captured["prior_trace"] = kwargs["prior_trace"]
            return ReActSolution(problem=problem, turns=[], final_answer="done")

    local_store = SimpleNamespace(
        get_checkpoint=lambda checkpoint_id: checkpoint,
        list_turns=lambda _project_id: [],
    )
    cloud_store = SimpleNamespace(get_checkpoint=lambda checkpoint_id: None)
    config = SimpleNamespace(lean=SimpleNamespace(enabled=False, lean_path=None))
    monkeypatch.setattr(solve_session, "load_config", lambda: config)
    monkeypatch.setattr(
        solve_session,
        "new_session_logger",
        lambda problem, model: ("resume-session", logging.getLogger("test.resume")),
    )
    async def build_agent(**kwargs):
        captured["project_id"] = kwargs["project_context"].project_id
        return FakeAgent()

    monkeypatch.setattr(web_app, "_build_agent", build_agent)
    monkeypatch.setattr(web_app, "_maybe_knowledge_store", lambda user_id=None: cloud_store)
    monkeypatch.setattr(web_app, "_project_store", lambda user_id=None: local_store)
    monkeypatch.setattr(web_app, "default_model_string", lambda config: "openai/gpt-5.6-sol")
    monkeypatch.setattr(web_app, "prefix_history", lambda problem, history: problem)
    monkeypatch.setattr(
        web_app,
        "persist_pending_turn",
        lambda *_args, **_kwargs: {
            "id": "pending-resume",
            "problem": "Prove P",
            "answer": "",
        },
    )
    def persist_turn(_store, project_id, *_args, **_kwargs):
        captured["persisted_project_id"] = project_id

    monkeypatch.setattr(web_app, "persist_turn", persist_turn)

    events = [
        event
        async for event in solve_session.stream_solve_events(
            {"problem": "", "checkpoint_id": "checkpoint-1", "mode": "react"}
        )
    ]

    assert not [event for event in events if event["type"] == "error"]
    assert captured["problem"] == "Prove P"
    assert captured["prior_trace"] is checkpoint
    assert captured["project_id"] == "project-from-checkpoint"
    assert captured["persisted_project_id"] == "project-from-checkpoint"
    assert events[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_matching_checkpoint_hydrates_trace_without_llm_summary(monkeypatch):
    problem = "Prove P"
    trace = ReActTrace(
        problem=problem,
        current_goal="prove the inductive step",
        plan_text="Base case, then induction.",
        context_preamble="Previously fetched bounded source context.",
        next_step_num=7,
        budget_consumption={
            "conclusion_revisions": 1,
            "search_mathlib_calls": 3,
        },
    )
    trace.turns.append(
        ReActTurn(
            thought="Established the base case.",
            action=Action(name="think", args={"text": "base"}),
            observation=ToolObservation(success=True, output="base established"),
            step_num=4,
        )
    )
    checkpoint = trace.to_checkpoint()
    critic_llm = SimpleNamespace(complete=AsyncMock(side_effect=AssertionError("no summary")))
    captured: dict[str, Any] = {}

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
        captured["initial_trace"] = initial_trace
        assert initial_trace is not None
        solution = ReActSolution(
            problem=react_problem,
            turns=initial_trace.turns,
            final_answer="continued",
            trace=initial_trace,
        )
        return solution, initial_trace

    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=critic_llm,
        config=AgentConfig(memory_consolidation_enabled=False),
        project_context=ProjectContext(project_id="project-1"),
    )
    agent._intake = SimpleNamespace(
        analyze=AsyncMock(return_value=IntakeResult(strategy="react"))
    )
    agent._augmentor = SimpleNamespace(
        augment=AsyncMock(
            return_value=AugmentationResult(prompt=problem, memories_used=[])
        )
    )
    monkeypatch.setattr(agent, "_run_react", fake_run_react.__get__(agent))

    await agent.solve(problem, prior_trace=checkpoint)

    critic_llm.complete.assert_not_awaited()
    hydrated = captured["initial_trace"]
    assert hydrated.problem == problem
    assert hydrated.current_goal == "prove the inductive step"
    assert hydrated.plan_text == "Base case, then induction."
    assert hydrated.context_preamble == "Previously fetched bounded source context."
    assert hydrated.next_step_num == 7
    assert hydrated.budget_consumption == trace.budget_consumption
    assert len(hydrated.turns) == 1
    assert hydrated.turns[0].step_num == 4




@pytest.mark.asyncio
async def test_matching_source_history_resume_skips_intake_fetch_and_summary(monkeypatch):
    problem = (
        "Conversation so far:\nUser: Read the linked paper.\n\n"
        "Current question:\nFormalize https://example.com/paper in Lean 4."
    )
    checkpoint = ReActTrace(
        problem=problem,
        current_goal="continue the Lean proof",
        context_preamble="Saved bounded source context.",
    ).to_checkpoint()
    main_llm = SimpleNamespace(
        complete=AsyncMock(side_effect=AssertionError("no intake LLM"))
    )
    critic_llm = SimpleNamespace(
        complete=AsyncMock(side_effect=AssertionError("no relevance LLM"))
    )
    fetch_sources = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "math_agent.agent.supervisor_intake.fetch_sources_from_prompt",
        fetch_sources,
    )
    captured: dict[str, Any] = {}

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
        captured["initial_trace"] = initial_trace
        captured["require_formal_verification"] = require_formal_verification
        assert initial_trace is not None
        solution = ReActSolution(
            problem=react_problem,
            turns=initial_trace.turns,
            final_answer="continued",
            trace=initial_trace,
        )
        return solution, initial_trace

    agent = SupervisorAgent(
        llm=main_llm,
        critic_llm=critic_llm,
        config=AgentConfig(memory_consolidation_enabled=False),
    )
    agent._augmentor = SimpleNamespace(
        augment=AsyncMock(side_effect=AssertionError("no augmentation"))
    )
    monkeypatch.setattr(agent, "_run_react", fake_run_react.__get__(agent))

    await agent.solve(
        problem,
        prior_trace=checkpoint,
        has_conversation_history=True,
    )

    fetch_sources.assert_not_awaited()
    main_llm.complete.assert_not_awaited()
    critic_llm.complete.assert_not_awaited()
    agent._augmentor.augment.assert_not_awaited()
    assert captured["initial_trace"].current_goal == "continue the Lean proof"
    assert captured["require_formal_verification"] is True


@pytest.mark.asyncio
async def test_different_problem_summary_bounds_v2_action_arguments():
    large_code = "theorem huge : True := by\n" + ("  trivial\n" * 8_000)
    prior = ReActTrace(problem="Formalize the old theorem")
    for step_num in range(1, 16):
        prior.turns.append(
            ReActTurn(
                thought=f"Formal attempt {step_num}",
                action=Action(
                    name="lean_check",
                    args={"code": large_code, "label": f"attempt-{step_num}"},
                ),
                observation=ToolObservation(success=False, output="failed"),
                step_num=step_num,
            )
        )
    critic_llm = SimpleNamespace(
        complete=AsyncMock(
            side_effect=[
                LLMResponse(
                    text=(
                        '{"related":true,"reason":"same area",'
                        '"preamble":"Use the prior failed approach only as a warning."}'
                    ),
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                ),
            ]
        )
    )
    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=critic_llm,
        config=AgentConfig(memory_consolidation_enabled=False),
    )

    async def emit(_event: dict) -> None:
        return None

    checkpoint = prior.to_checkpoint()
    checkpoint["strategy"] = "s" * 20_000
    checkpoint["completed_stages"] = ["stage" * 4_000]

    result = await agent._maybe_inject_prior_trace(
        "Prove a different theorem",
        "",
        checkpoint,
        emit,
        logging.getLogger("test.resume-summary"),
    )

    assert "prior failed approach" in result
    # Relevance judgement and preamble synthesis are one merged critic call.
    assert critic_llm.complete.await_count == 1
    for call in critic_llm.complete.await_args_list:
        prompt = call.args[0][0].content
        assert isinstance(prompt, str)
        assert len(prompt) <= CONTEXT_PREAMBLE_MAX_CHARS
        assert large_code not in prompt


@pytest.mark.asyncio
async def test_empty_prior_trace_short_circuits_without_llm():
    """A prior trace with no turns carries no work; no critic call is made."""
    critic_llm = SimpleNamespace(
        complete=AsyncMock(side_effect=AssertionError("no relevance LLM"))
    )
    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=critic_llm,
        config=AgentConfig(memory_consolidation_enabled=False),
    )

    async def emit(_event: dict) -> None:
        return None

    checkpoint = ReActTrace(problem="Prove the old theorem").to_checkpoint()
    result = await agent._maybe_inject_prior_trace(
        "Prove a new theorem",
        "augmented context",
        checkpoint,
        emit,
        logging.getLogger("test.resume-empty"),
    )

    assert result == "augmented context"
    critic_llm.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_unrelated_prior_trace_leaves_context_untouched():
    critic_llm = SimpleNamespace(
        complete=AsyncMock(
            return_value=LLMResponse(
                text='{"related":false,"reason":"different topic","preamble":""}',
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            )
        )
    )
    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=critic_llm,
        config=AgentConfig(memory_consolidation_enabled=False),
    )

    async def emit(_event: dict) -> None:
        return None

    prior = ReActTrace(problem="Prove the old theorem")
    prior.turns.append(
        ReActTurn(
            thought="old work",
            action=Action(name="think", args={"text": "old"}),
            observation=ToolObservation(success=True, output="old"),
            step_num=1,
        )
    )
    result = await agent._maybe_inject_prior_trace(
        "Prove a new theorem",
        "augmented context",
        prior.to_checkpoint(),
        emit,
        logging.getLogger("test.resume-unrelated"),
    )

    assert result == "augmented context"
    assert critic_llm.complete.await_count == 1


@pytest.mark.asyncio
async def test_invalid_matching_checkpoint_falls_back_without_summary(monkeypatch):
    problem = "Prove P"
    invalid_checkpoint = {
        "schema_version": 99,
        "problem": problem,
        "turns": [],
    }
    intake = SimpleNamespace(
        analyze=AsyncMock(return_value=IntakeResult(strategy="react"))
    )
    critic_llm = SimpleNamespace(
        complete=AsyncMock(side_effect=AssertionError("no malformed summary"))
    )
    captured: dict[str, Any] = {}

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
        captured["initial_trace"] = initial_trace
        trace = ReActTrace(problem=react_problem, current_goal=react_problem)
        return ReActSolution(
            problem=react_problem,
            turns=[],
            final_answer="fresh solve",
            trace=trace,
        ), trace

    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=critic_llm,
        config=AgentConfig(memory_consolidation_enabled=False),
    )
    agent._intake = intake
    agent._augmentor = SimpleNamespace(
        augment=AsyncMock(
            return_value=AugmentationResult(prompt=problem, memories_used=[])
        )
    )
    monkeypatch.setattr(agent, "_run_react", fake_run_react.__get__(agent))

    solution = await agent.solve(problem, prior_trace=invalid_checkpoint)

    intake.analyze.assert_awaited_once()
    critic_llm.complete.assert_not_awaited()
    assert captured["initial_trace"] is None
    assert solution.final_answer == "fresh solve"


@pytest.mark.parametrize("malformed_problem", [None, 17, ["P"], {"text": "P"}])
@pytest.mark.asyncio
async def test_malformed_checkpoint_problem_uses_safe_fresh_solve(
    monkeypatch,
    malformed_problem,
):
    problem = "Prove a fresh theorem"
    malformed_checkpoint = {
        "schema_version": 2,
        "problem": malformed_problem,
        "turns": [],
    }
    intake = SimpleNamespace(
        analyze=AsyncMock(return_value=IntakeResult(strategy="react"))
    )
    critic_llm = SimpleNamespace(
        complete=AsyncMock(side_effect=AssertionError("no malformed summary"))
    )

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
        assert initial_trace is None
        trace = ReActTrace(problem=react_problem, current_goal=react_problem)
        return ReActSolution(
            problem=react_problem,
            turns=[],
            final_answer="fresh solve",
            trace=trace,
        ), trace

    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=critic_llm,
        config=AgentConfig(memory_consolidation_enabled=False),
    )
    agent._intake = intake
    agent._augmentor = SimpleNamespace(
        augment=AsyncMock(
            return_value=AugmentationResult(prompt=problem, memories_used=[])
        )
    )
    monkeypatch.setattr(agent, "_run_react", fake_run_react.__get__(agent))

    solution = await agent.solve(problem, prior_trace=malformed_checkpoint)

    intake.analyze.assert_awaited_once()
    critic_llm.complete.assert_not_awaited()
    assert solution.final_answer == "fresh solve"


@pytest.mark.asyncio
async def test_transport_disconnect_with_cancel_flag_marks_run_cancelled(monkeypatch):
    config = SimpleNamespace(lean=SimpleNamespace(enabled=False, lean_path=None))
    monkeypatch.setattr(solve_session, "load_config", lambda: config)
    monkeypatch.setattr(
        solve_session,
        "new_session_logger",
        lambda problem, model: ("sess-disconnect", logging.getLogger("test.disconnect")),
    )
    monkeypatch.setattr(solve_session, "start_solve_run", AsyncMock())
    finish_run = AsyncMock()
    monkeypatch.setattr(solve_session, "finish_solve_run", finish_run)

    class EmitThenHangAgent:
        async def solve(self, problem: str, **kwargs: Any):
            await kwargs["on_event"](
                {"type": "stage_status", "stage": "plan", "message": "working"}
            )
            await asyncio.Event().wait()

    async def build_agent(**kwargs):
        return EmitThenHangAgent()

    monkeypatch.setattr(web_app, "_build_agent", build_agent)
    monkeypatch.setattr(
        web_app,
        "_project_store",
        lambda user_id=None: SimpleNamespace(get_checkpoint=lambda cid: None),
    )
    monkeypatch.setattr(web_app, "default_model_string", lambda config: "openai/gpt-5.6-sol")

    stream = solve_session.stream_solve_events(
        {"problem": "Prove P", "mode": "react", "_cancel_research": True}
    )
    assert (await stream.__anext__())["type"] == "session"
    assert (await stream.__anext__())["type"] == "stage_status"
    await stream.aclose()

    finish_run.assert_awaited_once()
    assert finish_run.await_args.kwargs["status"] == "cancelled"
