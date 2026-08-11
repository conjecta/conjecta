# scripts/benchmark_memory.py
"""A/B benchmark for memory consolidation effectiveness.

Run with:
    export OPENAI_API_KEY=...
    export OPENAI_BASE_URL=https://your-provider.example/v1
    uv run python scripts/benchmark_memory.py
"""
from __future__ import annotations

import asyncio
import statistics
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from math_agent.agent.context_augmentor import ContextAugmentor
from math_agent.agent.memory_consolidation import MemoryConsolidator
from math_agent.agent.plan_memory import PlanMemory
from math_agent.agent.react_agent import ReActAgent
from math_agent.config import AgentConfig, Config, load_config
from math_agent.evaluation import EvalCase
from math_agent.evaluation.judges import judge_solution
from math_agent.lean.codegen import LeanCodegen
from math_agent.lean.runner import LeanRunner
from math_agent.llm import create_backend


BENCHMARK_CASES = [
    EvalCase(
        id="sqrt2_irrational",
        problem="Prove that sqrt(2) is irrational.",
        judge="formal",
    ),
    EvalCase(
        id="n3_n_divisible_by_6",
        problem="Prove that for every integer n, n^3 - n is divisible by 6.",
        judge="formal",
    ),
    EvalCase(
        id="n7_mod_42",
        problem="Prove that for every integer n, n^7 is congruent to n modulo 42.",
        judge="formal",
    ),
    EvalCase(
        id="sqrt2_plus_sqrt3_irrational",
        problem="Prove that sqrt(2) + sqrt(3) is irrational.",
        judge="formal",
    ),
]

TRIALS_PER_PROBLEM = 2


def build_agent(config: Config, enable_memory: bool, plan_memory: PlanMemory | None = None):
    llm = create_backend(config.llm)
    critic_llm = create_backend(config.critic)
    lean_runner = LeanRunner(config.lean) if config.lean.enabled else None
    lean_codegen = (
        LeanCodegen(llm=llm, runner=lean_runner, config=config.lean)
        if lean_runner is not None
        else None
    )

    agent_config = AgentConfig(
        max_react_steps=config.agent.max_react_steps,
        tools=config.agent.tools,
        reviewers_enabled=config.agent.reviewers_enabled,
        memory_consolidation_enabled=enable_memory,
        memory_consolidation_model=config.agent.memory_consolidation_model,
    )

    consolidator = None
    if enable_memory:
        consolidation_llm = critic_llm
        if config.agent.memory_consolidation_model:
            from math_agent.llm.factory import create_backend_from_model_string

            consolidation_llm = create_backend_from_model_string(
                config.agent.memory_consolidation_model,
                temperature=config.critic.temperature,
            )

        knowledge_store = None
        try:
            from math_agent.knowledge.supabase import KnowledgeStore

            knowledge_store = KnowledgeStore(knowledge_config=config.knowledge)
        except Exception:
            pass

        consolidator = MemoryConsolidator(
            llm=consolidation_llm,
            knowledge_store=knowledge_store,
            plan_memory=plan_memory or PlanMemory(),
        )

    return ReActAgent(
        llm=llm,
        critic_llm=critic_llm,
        config=agent_config,
        lean_runner=lean_runner,
        lean_codegen=lean_codegen,
        consolidator=consolidator,
    )


async def run_trial(agent, case: EvalCase, augmentor: ContextAugmentor | None = None):
    events = []

    async def on_event(event):
        events.append(event)

    problem = case.problem
    if augmentor is not None:
        try:
            augmentation = await augmentor.augment(problem, project_id=None)
            problem = augmentation.prompt
        except Exception as exc:
            print(f"  Augmentation failed: {exc}")

    try:
        solution = await agent.solve(problem, on_event=on_event)
        success = judge_solution(case, solution)
    except Exception as exc:
        print(f"  ERROR: {exc}")
        return {"success": False, "steps": 0, "tool_calls": 0, "knowledge_hits": 0}

    steps = sum(1 for e in events if e.get("type") == "step")
    tool_calls = sum(1 for e in events if e.get("type") == "step_start")
    knowledge_hits = sum(
        1
        for e in events
        if e.get("type") == "step" and e.get("action") == "search_knowledge"
    )
    return {
        "success": success,
        "steps": steps,
        "tool_calls": tool_calls,
        "knowledge_hits": knowledge_hits,
    }


async def run_mode(
    config: Config,
    enable_memory: bool,
    plan_memory: PlanMemory | None = None,
):
    label = "with-memory" if enable_memory else "baseline"
    print(f"\n=== Mode: {label} ===")
    augmentor = None
    if enable_memory and plan_memory is not None:
        augmentor = ContextAugmentor(plan_memory=plan_memory)
    results = []
    for case in BENCHMARK_CASES:
        print(f"Problem: {case.problem}")
        agent = build_agent(config, enable_memory, plan_memory=plan_memory)
        trial_results = []
        for trial in range(TRIALS_PER_PROBLEM):
            print(f"  Trial {trial + 1}/{TRIALS_PER_PROBLEM}")
            trial_results.append(await run_trial(agent, case, augmentor=augmentor))
        results.extend(trial_results)
    return results


def summarize(results: list[dict]):
    successes = [r["success"] for r in results]
    steps = [r["steps"] for r in results]
    tool_calls = [r["tool_calls"] for r in results]
    knowledge_hits = [r["knowledge_hits"] for r in results]
    return {
        "success_rate": sum(successes) / len(successes),
        "avg_steps": statistics.mean(steps),
        "avg_tool_calls": statistics.mean(tool_calls),
        "avg_knowledge_hits": statistics.mean(knowledge_hits),
    }


async def main():
    config = load_config()
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as tmp:
        plan_memory = PlanMemory(path=Path(tmp.name), seed_path=None)

    try:
        baseline = await run_mode(config, enable_memory=False)
        with_memory = await run_mode(config, enable_memory=True, plan_memory=plan_memory)
    finally:
        Path(plan_memory.path).unlink(missing_ok=True)

    print("\n=== Summary ===")
    print(f"{'Metric':<25} {'Baseline':<15} {'With Memory':<15}")
    for key in ["success_rate", "avg_steps", "avg_tool_calls", "avg_knowledge_hits"]:
        b = summarize(baseline)[key]
        m = summarize(with_memory)[key]
        print(f"{key:<25} {b:<15.3f} {m:<15.3f}")

    total_cases = len(BENCHMARK_CASES) * TRIALS_PER_PROBLEM
    print(
        f"\nWARNING: sample size is small (N={total_cases}); "
        "treat the comparison as indicative, not conclusive."
    )


if __name__ == "__main__":
    asyncio.run(main())
