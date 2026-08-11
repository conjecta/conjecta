import asyncio

import pytest

from math_agent.agent.react_state import ProjectContext, ReActSolution
from math_agent.agent.subagent import (
    SharedSubagentDeps,
    SubagentSpec,
    build_subagent,
    run_subagents_parallel,
)
from math_agent.agent.tools import ToolRegistry
from math_agent.config import AgentConfig


def test_build_subagent_maps_private_policy_and_shares_services():
    llm = object()
    critic = object()
    runner = object()
    registry = ToolRegistry(enabled_tools=["compute"])
    context = ProjectContext(project_id="project-1")
    base = AgentConfig(max_react_steps=20, max_tool_calls=12)
    spec = SubagentSpec(
        max_steps=5,
        max_tool_calls=2,
        wall_seconds=30.0,
        tool_allowlist=("compute",),
        reviewers_enabled=(),
        event_scope={"goal_id": "lemma-1", "attempt": 2},
    )

    agent = build_subagent(
        SharedSubagentDeps(
            llm=llm,
            critic_llm=critic,
            config=base,
            lean_runner=runner,
            project_context=context,
            tool_registry=registry,
        ),
        spec,
    )

    # The agent wraps backends in a call-counting shim (llm call budget);
    # the wrapped object must still be the shared one.
    assert agent.llm._backend is llm
    assert agent.critic_llm._backend is critic
    assert agent.tools is registry
    assert agent.project_context is context
    assert agent.config is not base
    assert base.max_react_steps == 20
    assert agent.config.max_react_steps == 5
    assert agent.config.max_tool_calls == 2
    assert agent.config.max_wall_seconds == 30.0
    assert agent.config.tools == ["compute"]
    assert agent.config.reviewers_enabled == []
    assert agent.config.hitl.enabled is False
    assert agent.config.memory_consolidation_enabled is False
    assert agent.allowed_tools == frozenset({"compute"})
    assert agent.event_scope == {"goal_id": "lemma-1", "attempt": 2}


def test_each_subagent_gets_a_distinct_config():
    shared = SharedSubagentDeps(
        llm=object(),
        critic_llm=object(),
        config=AgentConfig(),
    )

    left = build_subagent(shared)
    right = build_subagent(shared)

    assert left.config is not right.config
    assert left.config is not shared.config
    assert right.config is not shared.config


def test_subagents_share_injected_llm_call_counter():
    from math_agent.llm.tracking import LLMCallCounter

    counter = LLMCallCounter()
    shared = SharedSubagentDeps(
        llm=object(),
        critic_llm=object(),
        config=AgentConfig(),
        llm_call_counter=counter,
    )

    left = build_subagent(shared)
    right = build_subagent(shared)

    # Parallel routes draw down one budget instead of one per route.
    assert left._llm_call_counter is counter
    assert right._llm_call_counter is counter


def test_subagent_without_injected_counter_gets_fresh_budget():
    shared = SharedSubagentDeps(
        llm=object(),
        critic_llm=object(),
        config=AgentConfig(),
    )

    left = build_subagent(shared)
    right = build_subagent(shared)

    assert left._llm_call_counter is not right._llm_call_counter


def test_force_review_spec_threads_into_private_config():
    shared = SharedSubagentDeps(
        llm=object(),
        critic_llm=object(),
        config=AgentConfig(),
    )

    forced = build_subagent(shared, SubagentSpec(force_review=True))
    plain = build_subagent(shared)

    assert forced.config.force_review is True
    assert plain.config.force_review is False
    assert shared.config.force_review is False


class _FakeAgent:
    """Stand-in for build_subagent output in parallel-runner tests."""

    def __init__(self, behavior):
        self.behavior = behavior

    async def solve(self, problem, **kwargs):
        return await self.behavior(problem, **kwargs)


def _shared_deps() -> SharedSubagentDeps:
    return SharedSubagentDeps(llm=object(), critic_llm=object(), config=AgentConfig())


def _patch_build(monkeypatch, behaviors):
    agents = [_FakeAgent(behavior) for behavior in behaviors]
    builds = []
    monkeypatch.setattr(
        "math_agent.agent.subagent.build_subagent",
        lambda shared, spec: (builds.append(spec), agents[len(builds) - 1])[1],
    )
    return builds


@pytest.mark.asyncio
async def test_run_subagents_parallel_preserves_order_and_isolates_failures(
    monkeypatch,
):
    async def ok(answer):
        async def behavior(problem, **kwargs):
            return ReActSolution(
                problem=problem, turns=[], final_answer=answer
            )

        return behavior

    async def boom(problem, **kwargs):
        raise RuntimeError("route exploded")

    behaviors = [await ok("a"), boom, await ok("c")]
    _patch_build(monkeypatch, behaviors)

    results = await run_subagents_parallel(
        "P", [SubagentSpec(), SubagentSpec(), SubagentSpec()], _shared_deps()
    )

    assert [r.final_answer if r is not None else None for r in results] == [
        "a",
        None,
        "c",
    ]


@pytest.mark.asyncio
async def test_run_subagents_parallel_runs_routes_concurrently(monkeypatch):
    active = 0
    max_active = 0

    async def behavior(problem, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return ReActSolution(problem=problem, turns=[], final_answer="done")

    _patch_build(monkeypatch, [behavior, behavior, behavior])

    results = await run_subagents_parallel(
        "P", [SubagentSpec()] * 3, _shared_deps()
    )

    assert all(r is not None for r in results)
    assert max_active == 3


@pytest.mark.asyncio
async def test_run_subagents_parallel_semaphore_caps_concurrency(monkeypatch):
    active = 0
    max_active = 0

    async def behavior(problem, **kwargs):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.01)
        active -= 1
        return ReActSolution(problem=problem, turns=[], final_answer="done")

    _patch_build(monkeypatch, [behavior, behavior, behavior])

    results = await run_subagents_parallel(
        "P", [SubagentSpec()] * 3, _shared_deps(), max_parallel=1
    )

    assert all(r is not None for r in results)
    assert max_active == 1


@pytest.mark.asyncio
async def test_run_subagents_parallel_merges_per_route_kwargs(monkeypatch):
    seen: list[dict] = []

    async def behavior(problem, **kwargs):
        seen.append(kwargs)
        return ReActSolution(problem=problem, turns=[], final_answer="done")

    _patch_build(monkeypatch, [behavior, behavior])

    await run_subagents_parallel(
        "P",
        [SubagentSpec(), SubagentSpec()],
        _shared_deps(),
        solve_kwargs={"shared": True, "tag": "common"},
        per_route_kwargs=[{"tag": "route-0"}, {"tag": "route-1"}],
    )

    assert seen[0] == {"shared": True, "tag": "route-0"}
    assert seen[1] == {"shared": True, "tag": "route-1"}
