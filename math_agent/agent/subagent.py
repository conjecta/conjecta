"""Shared construction contract for bounded ReAct subagents."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

from math_agent.agent.react_agent import ReActAgent
from math_agent.agent.react_state import ProjectContext, ReActSolution
from math_agent.agent.tools import ToolRegistry
from math_agent.config import AgentConfig
from math_agent.llm.base import LLMBackend

log = logging.getLogger("math_agent.agent.subagent")


@dataclass(frozen=True)
class SharedSubagentDeps:
    """Dependencies that are safe to share across independent workers.

    Model backends, the Lean runner, and the tool registry are shared services.
    ``build_subagent`` always creates a private config object; solve traces are
    created by each ReActAgent invocation and are never shared here.
    """

    llm: LLMBackend
    critic_llm: LLMBackend
    config: AgentConfig
    lean_runner: Any = None
    lean_codegen: Any = None
    premise_retriever: Any = None
    project_context: ProjectContext = field(default_factory=ProjectContext)
    tool_registry: ToolRegistry | None = None
    consolidator: Any = None
    # Optional shared LLM call counter; parallel routes built from the same
    # deps then draw down one budget instead of one per route.
    llm_call_counter: Any = None


@dataclass(frozen=True)
class SubagentSpec:
    """Per-worker limits and policies.

    HITL and memory integration default off because nested workers cannot own
    durable user interactions or mutate shared memory safely.
    """

    max_steps: int | None = None
    max_tool_calls: int | None = None
    wall_seconds: float | None = None
    tool_allowlist: tuple[str, ...] | None = None
    reviewers_enabled: tuple[str, ...] | None = None
    hitl_enabled: bool = False
    memory_integration_enabled: bool = False
    # Force the reviewer panel to run on every conclusion, overriding the
    # review-skip shortcuts (easy prompt / high confidence) of ordinary solves.
    force_review: bool = False
    # None keeps the subagent default (planning off); True/False overrides it
    # (formal escalation rounds run the unified planner up front).
    planning: bool | None = None
    event_scope: Mapping[str, Any] = field(default_factory=dict)


def build_subagent(
    shared: SharedSubagentDeps,
    spec: SubagentSpec | None = None,
) -> ReActAgent:
    """Build one bounded ReActAgent from shared services and a private config."""

    policy = spec or SubagentSpec()
    private_config = build_subagent_config(shared.config, policy)

    agent = ReActAgent(
        llm=shared.llm,
        critic_llm=shared.critic_llm,
        config=private_config,
        lean_runner=shared.lean_runner,
        lean_codegen=shared.lean_codegen,
        premise_retriever=shared.premise_retriever,
        project_context=shared.project_context,
        tool_registry=shared.tool_registry,
        consolidator=(
            shared.consolidator if policy.memory_integration_enabled else None
        ),
        allowed_tools=policy.tool_allowlist,
        llm_call_counter=shared.llm_call_counter,
    )
    agent.event_scope = dict(policy.event_scope)
    return agent


def build_subagent_config(
    base: AgentConfig,
    spec: SubagentSpec,
) -> AgentConfig:
    """Materialize the private config used by ``build_subagent``."""

    policy = spec
    overrides: dict[str, Any] = {
        "memory_consolidation_enabled": policy.memory_integration_enabled,
        "hitl": replace(base.hitl, enabled=policy.hitl_enabled),
        "planning_enabled": False,
        # Nested workers must not pay for normal-mode claim check / force review.
        "normal_claim_check_enabled": False,
        "normal_force_review": False,
        "force_review": policy.force_review,
    }
    if policy.max_steps is not None:
        overrides["max_react_steps"] = max(1, int(policy.max_steps))
    if policy.max_tool_calls is not None:
        overrides["max_tool_calls"] = max(0, int(policy.max_tool_calls))
    if policy.wall_seconds is not None:
        overrides["max_wall_seconds"] = max(0.0, float(policy.wall_seconds))
    if policy.tool_allowlist is not None:
        overrides["tools"] = list(dict.fromkeys(policy.tool_allowlist))
    if policy.reviewers_enabled is not None:
        overrides["reviewers_enabled"] = list(policy.reviewers_enabled)
    if policy.planning is not None:
        overrides["planning_enabled"] = policy.planning

    return replace(base, **overrides)


async def run_subagents_parallel(
    problem: str,
    specs: Sequence[SubagentSpec],
    shared: SharedSubagentDeps,
    *,
    max_parallel: int | None = None,
    solve_kwargs: Mapping[str, Any] | None = None,
    per_route_kwargs: Sequence[Mapping[str, Any]] | None = None,
) -> list[ReActSolution | None]:
    """Run one bounded subagent per spec concurrently.

    Results keep the spec order. A route that raises is logged and reported
    as ``None`` in its slot instead of failing the whole batch.
    ``solve_kwargs`` apply to every route; ``per_route_kwargs[i]`` is merged
    on top for route ``i`` (e.g. a route-specific initial trace).
    ``max_parallel`` caps concurrency via a semaphore; ``None`` (or a value
    below 1) runs all routes at once.
    """

    if not specs:
        return []
    if per_route_kwargs is not None and len(per_route_kwargs) != len(specs):
        raise ValueError("per_route_kwargs must align with specs one-to-one")
    if max_parallel is None or max_parallel < 1:
        limit = len(specs)
    else:
        limit = min(int(max_parallel), len(specs))
    semaphore = asyncio.Semaphore(limit)
    common_kwargs = dict(solve_kwargs or {})

    async def _run_one(index: int, spec: SubagentSpec) -> ReActSolution | None:
        kwargs = dict(common_kwargs)
        if per_route_kwargs is not None:
            kwargs.update(per_route_kwargs[index])
        async with semaphore:
            try:
                agent = build_subagent(shared, spec)
                return await agent.solve(problem, **kwargs)
            except Exception as exc:
                log.warning(
                    "Parallel subagent route %d failed: %s", index, exc,
                    exc_info=True,
                )
                return None

    return list(
        await asyncio.gather(*(_run_one(i, spec) for i, spec in enumerate(specs)))
    )
