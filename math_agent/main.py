"""CLI entry point."""
from __future__ import annotations

import argparse
import asyncio
import sys

from math_agent.config import load_config
from math_agent.llm import create_backend
from math_agent.log_config import setup_logging, new_session_logger
from math_agent.agent.plan_memory import PlanMemory


async def run(problem: str, *, mode: str = "auto") -> None:
    config = load_config()
    if config.logging.enabled:
        setup_logging(level=config.logging.level, log_dir=config.logging.dir)
    _, session_log = new_session_logger(problem)
    llm = create_backend(config.llm)
    critic_llm = create_backend(config.critic)
    from math_agent.llm.factory import create_prover_backend

    prover_llm = create_prover_backend(config.prover)

    from math_agent.lean.runner import LeanRunner
    from math_agent.lean.codegen import LeanCodegen
    from math_agent.lean.premise_retriever import PremiseRetriever

    lean_runner = LeanRunner(config.lean) if config.lean.enabled else None
    if lean_runner is not None and config.lean.mathlib_dep:
        dep_result = await lean_runner.ensure_dependencies()
        if dep_result is not None:
            session_log.error("Lean dependency setup failed: %s", "; ".join(dep_result.errors))
            print("Lean dependency setup failed. Run: math-agent-lean-setup")
            for err in dep_result.errors:
                print(f"  - {err}")
            sys.exit(1)

    premise_retriever = None
    if config.lean.premise_index_enabled and lean_runner is not None:
        try:
            premise_retriever = PremiseRetriever()
            await asyncio.to_thread(premise_retriever.build_index)
        except Exception as exc:
            session_log.warning("Premise retriever unavailable: %s", exc)
            premise_retriever = None

    lean_codegen = (
        LeanCodegen(
            llm=llm,
            runner=lean_runner,
            config=config.lean,
            premise_retriever=premise_retriever,
        )
        if lean_runner is not None
        else None
    )

    knowledge_store = None
    plan_memory = None
    if config.agent.memory_consolidation_enabled:
        try:
            from math_agent.knowledge.supabase import KnowledgeStore
            knowledge_store = KnowledgeStore(knowledge_config=config.knowledge)
        except Exception as exc:
            session_log.warning("KnowledgeStore unavailable: %s", exc)
        plan_memory = PlanMemory()

    from math_agent.agent.mcp_client import McpClient
    mcp_client = McpClient(config.mcp_servers)
    try:
        await mcp_client.initialize()

        from math_agent.agent.tools import ToolRegistry
        tool_registry = ToolRegistry(
            enabled_tools=config.agent.tools,
            lean_runner=lean_runner,
            lean_codegen=lean_codegen,
            premise_retriever=premise_retriever,
            llm=llm,
            knowledge_config=config.knowledge,
            mcp_client=mcp_client,
            agent_config=config.agent,
            search_config=config.search,
            prover_llm=prover_llm,
            critic_llm=critic_llm,
        )

        from math_agent.agent.supervisor import SupervisorAgent
        from math_agent.agent.react_state import HumanInputRequired
        agent = SupervisorAgent(
            llm=llm,
            critic_llm=critic_llm,
            config=config.agent,
            lean_runner=lean_runner,
            lean_codegen=lean_codegen,
            knowledge_store=knowledge_store,
            plan_memory=plan_memory,
            tool_registry=tool_registry,
            verifier_config=config.verifier,
        )
        try:
            solution = await agent.solve(problem, session_log=session_log, mode=mode)
        except HumanInputRequired as pause:
            session_log.warning(
                "Solve paused for human input request=%s; CLI cannot resume pauses",
                pause.interaction.get("request_id"),
            )
            print("\n=== Paused: human input required ===")
            print(pause.interaction.get("question") or "Human input required.")
            print(f"request_id: {pause.interaction.get('request_id')}")
            print(
                "The CLI cannot resume interactive pauses. Use the web workbench, "
                'or run unattended with [agent.hitl] mode = "auto".'
            )
            return

        session_log.info("=== Solution ===\n%s", solution.summary())
        print("\n=== Solution ===")
        print(solution.summary())
    finally:
        await mcp_client.close()


def cli() -> None:
    parser = argparse.ArgumentParser(
        prog="math-agent",
        description="Conjecta math research agent",
    )
    parser.add_argument(
        "problem",
        nargs="+",
        help="Problem statement (words are joined with spaces)",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "react"],
        default="auto",
        help="Solve mode: auto (default) or react",
    )
    args = parser.parse_args()
    problem = " ".join(args.problem).strip()
    if not problem:
        parser.error("problem is required")
    asyncio.run(run(problem, mode=args.mode))


if __name__ == "__main__":
    cli()
