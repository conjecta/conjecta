"""Formal escalation: diagnostics-driven replan rounds for formal proofs."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from math_agent.agent.context_augmentor import AugmentationResult
from math_agent.agent.react_state import (
    Action,
    ProjectContext,
    ReActSolution,
    ReActTrace,
    ReActTurn,
    ToolObservation,
)
from math_agent.agent.supervisor import (
    SupervisorAgent,
    _lean_failure_digest,
    _select_deep_search_route,
)
from math_agent.agent.supervisor_intake import IntakeResult
from math_agent.config import AgentConfig


def _failed_lean_trace(problem: str) -> ReActTrace:
    trace = ReActTrace(problem=problem, current_goal=problem)
    trace.turns.append(
        ReActTurn(
            thought="try formalizing",
            action=Action(name="formalize", args={"statement": "P"}),
            observation=ToolObservation(
                success=False, output="error: unknown constant 'Nat.addc'"
            ),
            step_num=1,
        )
    )
    trace.turns.append(
        ReActTurn(
            thought="compute instead",
            action=Action(name="compute", args={"code": "print(1)"}),
            observation=ToolObservation(success=False, output="irrelevant"),
            step_num=2,
        )
    )
    return trace


def test_failure_digest_keeps_only_lean_tool_failures():
    trace = _failed_lean_trace("Prove P")
    digest = _lean_failure_digest(trace)
    assert "Previous proof attempt failed" in digest["text"]
    assert "unknown constant 'Nat.addc'" in digest["text"]
    # Non-Lean tool failures are not part of the replan context.
    assert "irrelevant" not in digest["text"]
    assert _lean_failure_digest(ReActTrace(problem="P", current_goal="P")) == {}


@pytest.mark.asyncio
async def test_formal_escalation_replans_with_failure_digest(monkeypatch):
    """A solve requiring formal verification escalates on failure."""
    problem = "Prove P formally"
    calls: list[dict[str, Any]] = []

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
    ):
        calls.append(
            {
                "context_preamble": context_preamble,
                "require_formal_verification": require_formal_verification,
                "planning": planning,
                "max_react_steps": config.max_react_steps if config else None,
            }
        )
        if len(calls) == 1:
            return (
                ReActSolution(
                    problem=react_problem,
                    turns=[],
                    final_answer="unverified",
                    verification_status="best_effort",
                ),
                _failed_lean_trace(react_problem),
            )
        trace = ReActTrace(problem=react_problem, current_goal=react_problem)
        return (
            ReActSolution(
                problem=react_problem,
                turns=[],
                final_answer="proved",
                verification_status="verified",
            ),
            trace,
        )

    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=MagicMock(),
        config=AgentConfig(
            memory_consolidation_enabled=False,
            escalation_replan_rounds=1,
            escalation_max_react_steps=24,
        ),
        lean_runner=MagicMock(),
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

    # The formal requirement is the trust boundary that drives escalation.
    solution = await agent.solve(problem, require_formal_verification=True)

    assert solution.verification_status == "verified"
    assert len(calls) == 2
    # First round: normal budget
    assert calls[0]["require_formal_verification"] is True
    assert calls[0]["max_react_steps"] is None  # default config
    # Escalation round: larger budget + planning forced + diagnostics injected
    assert calls[1]["planning"] is True
    assert calls[1]["max_react_steps"] == 24
    assert "Previous proof attempt failed" in calls[1]["context_preamble"]
    assert "unknown constant 'Nat.addc'" in calls[1]["context_preamble"]


@pytest.mark.asyncio
async def test_escalation_without_lean_skips_replan(monkeypatch):
    """No Lean runner means no escalation rounds."""
    calls = 0

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
    ):
        nonlocal calls
        calls += 1
        trace = _failed_lean_trace(react_problem)
        return (
            ReActSolution(
                problem=react_problem,
                turns=[],
                final_answer="unverified",
                verification_status="best_effort",
            ),
            trace,
        )

    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=MagicMock(),
        config=AgentConfig(memory_consolidation_enabled=False),
        lean_runner=None,
    )
    agent._intake = SimpleNamespace(
        analyze=AsyncMock(return_value=IntakeResult(strategy="react"))
    )
    agent._augmentor = SimpleNamespace(
        augment=AsyncMock(
            return_value=AugmentationResult(prompt="Prove P", memories_used=[])
        )
    )
    monkeypatch.setattr(agent, "_run_react", fake_run_react.__get__(agent))

    solution = await agent.solve("Prove P", require_formal_verification=True)
    assert solution.verification_status == "best_effort"
    assert calls == 1



def _failed_lean_trace_with_kind(problem: str) -> ReActTrace:
    trace = ReActTrace(problem=problem, current_goal=problem)
    trace.turns.append(
        ReActTurn(
            thought="formalize",
            action=Action(name="formalize", args={"statement": "P"}),
            observation=ToolObservation(
                success=False,
                output="Lean verification: FAILED\nFailure kind: syntax\nErrors:\n- unexpected token",
                lean_code="theorem P : True := by\n  exactt trivial",
            ),
            step_num=1,
        )
    )
    return trace


def test_failure_digest_extracts_kinds_and_draft():
    trace = _failed_lean_trace_with_kind("Prove P")
    digest = _lean_failure_digest(trace)
    assert digest["failure_kinds"] == ["syntax"]
    assert "exactt trivial" in digest["draft"]
    assert "repair it" in digest["text"]


@pytest.mark.asyncio
async def test_escalation_routes_repairable_failures_to_draft_repair(monkeypatch):
    """Coding-level Lean failures get a repair hint, not a replan hint."""
    problem = "Prove P formally"
    calls: list[dict[str, Any]] = []

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
    ):
        calls.append({"context_preamble": context_preamble})
        if len(calls) == 1:
            return (
                ReActSolution(
                    problem=react_problem,
                    turns=[],
                    final_answer="unverified",
                    verification_status="best_effort",
                ),
                _failed_lean_trace_with_kind(react_problem),
            )
        trace = ReActTrace(problem=react_problem, current_goal=react_problem)
        return (
            ReActSolution(
                problem=react_problem,
                turns=[],
                final_answer="proved",
                verification_status="verified",
            ),
            trace,
        )

    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=MagicMock(),
        config=AgentConfig(
            memory_consolidation_enabled=False,
            escalation_replan_rounds=1,
        ),
        lean_runner=MagicMock(),
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

    solution = await agent.solve(problem, require_formal_verification=True)

    assert solution.verification_status == "verified"
    assert len(calls) == 2
    preamble = calls[1]["context_preamble"]
    assert "Repair the failed draft" in preamble
    assert "exactt trivial" in preamble


def _twice_failed_formal_trace(problem: str) -> ReActTrace:
    """Two failed one-shot formalize/lean_check rounds without verification."""
    trace = ReActTrace(problem=problem, current_goal=problem)
    for step, tool in enumerate(("formalize", "lean_check"), start=1):
        trace.turns.append(
            ReActTurn(
                thought=f"attempt {step}",
                action=Action(name=tool, args={"statement": "P"}),
                observation=ToolObservation(
                    success=False,
                    output="Lean verification: FAILED\nFailure kind: unsolved_goals",
                    lean_code="theorem P : True := by\n  sorry",
                ),
                step_num=step,
            )
        )
    return trace


@pytest.mark.asyncio
async def test_escalation_forces_deep_search_after_repeated_repair_failure(monkeypatch):
    """≥2 failed formalize/lean_check rounds force the deep-search route with
    enlarged budgets; the critic judge is not consulted for this decision."""
    problem = "Prove P formally"
    calls: list[dict[str, Any]] = []

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
    ):
        calls.append(
            {
                "context_preamble": context_preamble,
                "max_wall_seconds": config.max_wall_seconds if config else None,
                "tactic_search_max_attempts": (
                    config.tactic_search_max_attempts if config else None
                ),
            }
        )
        if len(calls) == 1:
            return (
                ReActSolution(
                    problem=react_problem,
                    turns=[],
                    final_answer="unverified",
                    verification_status="best_effort",
                ),
                _twice_failed_formal_trace(react_problem),
            )
        trace = ReActTrace(problem=react_problem, current_goal=react_problem)
        return (
            ReActSolution(
                problem=react_problem,
                turns=[],
                final_answer="proved",
                verification_status="verified",
            ),
            trace,
        )

    critic = MagicMock()
    critic.complete = AsyncMock(
        side_effect=AssertionError("critic judge must not be consulted")
    )
    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=critic,
        config=AgentConfig(
            memory_consolidation_enabled=False,
            escalation_replan_rounds=1,
            deep_search_wall_seconds=3600.0,
            deep_search_max_attempts=200,
            # Single route keeps the legacy serial deep-search behavior.
            deep_search_parallel_routes=1,
        ),
        lean_runner=MagicMock(),
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

    solution = await agent.solve(problem, require_formal_verification=True)

    assert solution.verification_status == "verified"
    assert len(calls) == 2
    preamble = calls[1]["context_preamble"]
    # The route hint explicitly directs the agent to the search tools.
    assert "tactic_search" in preamble
    assert "prove_by_lemmas" in preamble
    # The escalated round uses the enlarged deep-search budgets.
    assert calls[1]["max_wall_seconds"] == 3600.0
    assert calls[1]["tactic_search_max_attempts"] == 200


@pytest.mark.asyncio
async def test_escalation_forces_deep_search_after_structured_tool_failure(monkeypatch):
    """≥2 failed tactic_search/prove_by_lemmas turns also force the
    deep-search route: hard problems where the actor jumps straight to the
    structured tools must still reach the last-resort budget."""
    problem = "Prove P formally"
    calls: list[dict[str, Any]] = []

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
    ):
        calls.append(
            {
                "context_preamble": context_preamble,
                "max_wall_seconds": config.max_wall_seconds if config else None,
            }
        )
        if len(calls) == 1:
            trace = ReActTrace(problem=react_problem, current_goal=react_problem)
            for step, tool in enumerate(("prove_by_lemmas", "tactic_search"), start=1):
                trace.turns.append(
                    ReActTurn(
                        thought=f"attempt {step}",
                        action=Action(name=tool, args={"statement": "P"}),
                        observation=ToolObservation(
                            success=False,
                            output="Lean verification: FAILED\nFailure kind: unsolved_goals",
                        ),
                        step_num=step,
                    )
                )
            return (
                ReActSolution(
                    problem=react_problem,
                    turns=[],
                    final_answer="unverified",
                    verification_status="best_effort",
                ),
                trace,
            )
        trace = ReActTrace(problem=react_problem, current_goal=react_problem)
        return (
            ReActSolution(
                problem=react_problem,
                turns=[],
                final_answer="proved",
                verification_status="verified",
            ),
            trace,
        )

    critic = MagicMock()
    critic.complete = AsyncMock(
        side_effect=AssertionError("critic judge must not be consulted")
    )
    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=critic,
        config=AgentConfig(
            memory_consolidation_enabled=False,
            escalation_replan_rounds=1,
            deep_search_wall_seconds=3600.0,
            # Single route keeps the legacy serial deep-search behavior.
            deep_search_parallel_routes=1,
        ),
        lean_runner=MagicMock(),
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

    solution = await agent.solve(problem, require_formal_verification=True)

    assert solution.verification_status == "verified"
    assert len(calls) == 2
    assert "tactic_search" in calls[1]["context_preamble"]
    assert calls[1]["max_wall_seconds"] == 3600.0


def _solution(problem: str, answer: str, status: str, issues: list[str] | None = None):
    return ReActSolution(
        problem=problem,
        turns=[],
        final_answer=answer,
        verification_status=status,
        verification_issues=issues or [],
    )


def test_select_deep_search_route_prefers_verified_then_fewest_issues():
    verified_noisy = _solution("P", "v1", "verified", ["wobble"])
    verified_clean = _solution("P", "v2", "verified")
    chosen = _select_deep_search_route(
        [_solution("P", "u", "best_effort"), verified_noisy, verified_clean], "P"
    )
    assert chosen is verified_clean


def test_select_deep_search_route_fewest_issues_when_nothing_verified():
    noisy = _solution("P", "noisy", "best_effort", ["a", "b", "c"])
    clean = _solution("P", "clean", "best_effort", ["a"])
    chosen = _select_deep_search_route([noisy, clean, None], "P")
    assert chosen is clean


def test_select_deep_search_route_all_failed_returns_placeholder():
    chosen = _select_deep_search_route([None, None], "P")
    assert chosen.verification_status == "best_effort"
    assert chosen.turns == []
    assert chosen.verification_issues


def _build_parallel_supervisor(routes: int) -> SupervisorAgent:
    agent = SupervisorAgent(
        llm=MagicMock(),
        critic_llm=MagicMock(),
        config=AgentConfig(
            memory_consolidation_enabled=False,
            escalation_replan_rounds=1,
            deep_search_parallel_routes=routes,
        ),
        lean_runner=MagicMock(),
        project_context=ProjectContext(project_id="project-1"),
    )
    agent._intake = SimpleNamespace(
        analyze=AsyncMock(return_value=IntakeResult(strategy="react"))
    )
    agent._augmentor = SimpleNamespace(
        augment=AsyncMock(
            return_value=AugmentationResult(prompt="Prove P formally", memories_used=[])
        )
    )
    return agent


def _patch_initial_solve_failure(monkeypatch, agent, calls):
    """First (and only serial) _run_react call fails twice -> deep_search route."""

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
    ):
        calls.append({"context_preamble": context_preamble})
        return (
            _solution(react_problem, "unverified", "best_effort"),
            _twice_failed_formal_trace(react_problem),
        )

    monkeypatch.setattr(agent, "_run_react", fake_run_react.__get__(agent))


@pytest.mark.asyncio
async def test_deep_search_parallel_routes_run_and_verified_wins(monkeypatch):
    """routes>1: the deep_search round runs parallel strategy-diversified
    routes via run_subagents_parallel, and a verified route is adopted."""
    calls: list[dict[str, Any]] = []
    captured: dict[str, Any] = {}
    agent = _build_parallel_supervisor(routes=2)
    _patch_initial_solve_failure(monkeypatch, agent, calls)

    async def fake_parallel(
        problem, specs, shared, *, max_parallel=None, solve_kwargs=None,
        per_route_kwargs=None,
    ):
        captured["specs"] = specs
        captured["max_parallel"] = max_parallel
        captured["solve_kwargs"] = solve_kwargs
        captured["per_route_kwargs"] = per_route_kwargs
        return [
            _solution(problem, "unverified", "best_effort", ["stuck"]),
            _solution(problem, "proved", "verified"),
        ]

    monkeypatch.setattr(
        "math_agent.agent.supervisor.run_subagents_parallel", fake_parallel
    )

    solution = await agent.solve("Prove P formally", require_formal_verification=True)

    assert solution.verification_status == "verified"
    assert solution.final_answer == "proved"
    # The escalation round went parallel: _run_react only ran the initial solve.
    assert len(calls) == 1
    specs = captured["specs"]
    assert len(specs) == 2
    assert captured["max_parallel"] == 2
    assert all(spec.planning is True for spec in specs)
    assert captured["solve_kwargs"]["require_formal_verification"] is True
    # Routes are strategy-diversified on top of the shared deep-search hint
    # and the previous round's Lean diagnostics.
    preambles = [
        kwargs["initial_trace"].context_preamble
        for kwargs in captured["per_route_kwargs"]
    ]
    assert preambles[0] != preambles[1]
    assert "leads with tactic_search" in preambles[0]
    assert "leads with prove_by_lemmas" in preambles[1]
    for preamble in preambles:
        assert "Previous proof attempt failed" in preamble
        assert "Do not keep repairing" in preamble


@pytest.mark.asyncio
async def test_deep_search_parallel_fewest_issues_when_nothing_verified(monkeypatch):
    """No verified route: the fewest-issues route carries the round."""
    calls: list[dict[str, Any]] = []
    agent = _build_parallel_supervisor(routes=2)
    _patch_initial_solve_failure(monkeypatch, agent, calls)

    async def fake_parallel(
        problem, specs, shared, *, max_parallel=None, solve_kwargs=None,
        per_route_kwargs=None,
    ):
        return [
            _solution(problem, "far", "best_effort", ["a", "b", "c"]),
            _solution(problem, "close", "best_effort", ["a"]),
        ]

    monkeypatch.setattr(
        "math_agent.agent.supervisor.run_subagents_parallel", fake_parallel
    )

    solution = await agent.solve("Prove P formally", require_formal_verification=True)

    assert solution.verification_status == "best_effort"
    assert solution.final_answer == "close"
