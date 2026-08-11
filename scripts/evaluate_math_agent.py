#!/usr/bin/env python3
"""Run reproducible core-agent evaluations from a JSONL dataset.

Example:
    uv run python scripts/evaluate_math_agent.py \
      --dataset data/eval_smoke.jsonl \
      --trials 3 \
      --output data/eval-results/smoke.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from math_agent.agent.react_agent import ReActAgent
from math_agent.agent.react_state import HumanInputRequired, ReActTrace
from math_agent.agent.supervisor import SupervisorAgent
from math_agent.agent.tools import ToolRegistry
from math_agent.config import load_config
from math_agent.evaluation import load_cases, run_evaluation
from math_agent.evaluation.runner import write_results
from math_agent.lean.codegen import LeanCodegen
from math_agent.lean.runner import LeanRunner
from math_agent.llm import create_backend
from math_agent.llm.tracking import UsageAccumulator, UsageTrackingBackend
from math_agent.agent.plan_memory import PlanMemory


class _EvalCheckpointStore:
    """In-memory checkpoint sink so unattended runs can resume HITL pauses.

    The agent writes a snapshot (including the pending interaction) through
    ``project_store.write_checkpoint`` right before raising
    ``HumanInputRequired``; keeping the latest snapshot per session lets the
    eval loop resume the run the same way the web decisions endpoint does.
    """

    def __init__(self) -> None:
        self._checkpoints: dict[str, dict] = {}

    def write_checkpoint(self, snapshot: dict) -> None:
        session_id = str(snapshot.get("session_id") or "")
        if session_id:
            self._checkpoints[session_id] = dict(snapshot)

    def get_checkpoint(self, session_id: str) -> dict | None:
        return self._checkpoints.get(session_id)


def _auto_human_decision(pending: dict) -> dict | None:
    """Build the default (approve-first) decision for a pending interaction."""
    allowed = [str(item) for item in (pending.get("allowed_decisions") or [])]
    request_id = str(pending.get("request_id") or "")
    if not allowed or not request_id:
        return None
    return {
        "request_id": request_id,
        "decision": "approve" if "approve" in allowed else allowed[0],
        "feedback": "auto-resolved: unattended eval run",
    }


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="data/eval_smoke.jsonl")
    parser.add_argument("--output", default="data/eval-results/latest.jsonl")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument(
        "--config",
        default=None,
        help="Optional path to a config.toml override (e.g. an eval-specific "
        "config pointing at a different model endpoint).",
    )
    parser.add_argument(
        "--mode",
        choices=("auto", "react"),
        default="auto",
        help=(
            "Supervisor solve mode. Formal escalation is not a mode: solves "
            "whose problem requires formal verification automatically get "
            "bounded replan rounds (agent.escalation_* config)."
        ),
    )
    parser.add_argument(
        "--pass-k",
        type=int,
        default=None,
        help="k for the summary pass@k metric (unbiased estimator, clamped to "
        "the trial count). Default: 8.",
    )
    parser.add_argument(
        "--planning",
        choices=("on", "off"),
        default=None,
        help="Override planning_enabled (A/B ablation).",
    )
    parser.add_argument("--max-react-steps", type=int, default=None)
    parser.add_argument("--max-tool-calls", type=int, default=None)
    parser.add_argument("--react-context-max-chars", type=int, default=None)
    parser.add_argument(
        "--direct-react",
        action="store_true",
        help="Bypass SupervisorAgent and evaluate ReActAgent directly (legacy ablation).",
    )
    parser.add_argument(
        "--with-mcp",
        action="store_true",
        help="Enable configured MCP servers; disabled by default for reproducibility.",
    )
    args = parser.parse_args(argv)
    if args.pass_k is not None and args.pass_k < 1:
        parser.error("--pass-k must be at least 1")
    return args


async def _main(argv=None) -> int:
    args = _parse_args(argv)
    cases = load_cases(args.dataset)
    config = load_config(Path(args.config) if args.config else None)
    llm = create_backend(config.llm)
    critic_llm = create_backend(config.critic)
    from math_agent.llm.factory import create_prover_backend

    prover_llm = create_prover_backend(config.prover)
    lean_runner = LeanRunner(config.lean) if config.lean.enabled else None
    lean_codegen = (
        LeanCodegen(llm=llm, runner=lean_runner, config=config.lean)
        if lean_runner is not None
        else None
    )

    mcp_client = None
    if args.with_mcp:
        from math_agent.agent.mcp_client import McpClient

        mcp_client = McpClient(config.mcp_servers)
        await mcp_client.initialize()

    tool_registry = ToolRegistry(
        enabled_tools=config.agent.tools,
        lean_runner=lean_runner,
        lean_codegen=lean_codegen,
        llm=llm,
        knowledge_config=config.knowledge,
        mcp_client=mcp_client,
        agent_config=config.agent,
        prover_llm=prover_llm,
        critic_llm=critic_llm,
    )

    overrides: dict = {}
    if args.planning is not None:
        overrides["planning_enabled"] = args.planning == "on"
    if args.max_react_steps is not None:
        overrides["max_react_steps"] = args.max_react_steps
    if args.max_tool_calls is not None:
        overrides["max_tool_calls"] = args.max_tool_calls
    if args.react_context_max_chars is not None:
        overrides["react_context_max_chars"] = args.react_context_max_chars
    eval_config = replace(
        config.agent,
        memory_consolidation_enabled=False,
        **overrides,
    )

    # Each case gets its own isolated PlanMemory so trials within a case can
    # benefit from memory but cases cannot pollute each other's metrics.
    case_plan_memory_paths: dict[str, Path] = {}
    case_plan_memories: dict[str, PlanMemory] = {}

    if args.direct_react:

        async def solve(case):
            main_acc, critic_acc = UsageAccumulator(), UsageAccumulator()
            agent = ReActAgent(
                llm=UsageTrackingBackend(llm, main_acc),
                critic_llm=UsageTrackingBackend(critic_llm, critic_acc),
                config=eval_config,
                lean_runner=lean_runner,
                lean_codegen=lean_codegen,
                tool_registry=tool_registry,
            )
            snapshots: list[dict] = []
            initial_trace: ReActTrace | None = None
            human_decision: dict | None = None
            while True:
                try:
                    solution = await agent.solve(
                        case.problem,
                        require_formal_verification=case.require_formal_verification,
                        on_checkpoint=snapshots.append,
                        initial_trace=initial_trace,
                        human_decision=human_decision,
                    )
                    break
                except HumanInputRequired:
                    # Unattended eval: auto-approve the pause and resume.
                    pending = (
                        snapshots[-1].get("pending_interaction") if snapshots else None
                    )
                    if not isinstance(pending, dict):
                        raise
                    human_decision = _auto_human_decision(pending)
                    if human_decision is None:
                        raise
                    initial_trace = ReActTrace.from_checkpoint(snapshots[-1])
            solution.metadata["eval_usage"] = {
                "input_tokens": main_acc.prompt_tokens + critic_acc.prompt_tokens,
                "output_tokens": main_acc.completion_tokens + critic_acc.completion_tokens,
                "total_tokens": main_acc.total_tokens + critic_acc.total_tokens,
                "llm_calls": main_acc.calls + critic_acc.calls,
            }
            return solution
    else:
        knowledge_store = None
        try:
            from math_agent.knowledge.supabase import KnowledgeStore

            knowledge_store = KnowledgeStore(knowledge_config=config.knowledge)
        except Exception:
            pass

        def _plan_memory_for_case(case_id: str) -> PlanMemory:
            plan_memory = case_plan_memories.get(case_id)
            if plan_memory is None:
                tmp = tempfile.NamedTemporaryFile(
                    suffix=".jsonl", prefix=f"eval_plan_memory_{case_id}_", delete=False
                )
                tmp.close()
                path = Path(tmp.name)
                plan_memory = PlanMemory(path=path, seed_path=None)
                case_plan_memory_paths[case_id] = path
                case_plan_memories[case_id] = plan_memory
            return plan_memory

        async def solve(case):
            main_acc, critic_acc = UsageAccumulator(), UsageAccumulator()
            checkpoint_store = _EvalCheckpointStore()
            agent = SupervisorAgent(
                llm=UsageTrackingBackend(llm, main_acc),
                critic_llm=UsageTrackingBackend(critic_llm, critic_acc),
                config=eval_config,
                lean_runner=lean_runner,
                lean_codegen=lean_codegen,
                knowledge_store=knowledge_store,
                plan_memory=_plan_memory_for_case(case.id),
                tool_registry=tool_registry,
                project_store=checkpoint_store,
            )
            session_id = f"eval-{case.id}"
            prior_trace: dict | None = None
            human_decision: dict | None = None
            while True:
                try:
                    solution = await agent.solve(
                        case.problem,
                        mode=args.mode,
                        require_formal_verification=case.require_formal_verification,
                        session_id=session_id,
                        prior_trace=prior_trace,
                        human_decision=human_decision,
                    )
                    break
                except HumanInputRequired:
                    # Unattended eval: auto-approve the pause and resume from
                    # the checkpoint snapshot, mirroring the web resume flow.
                    checkpoint = checkpoint_store.get_checkpoint(session_id)
                    pending = (
                        checkpoint.get("pending_interaction")
                        if isinstance(checkpoint, dict)
                        else None
                    )
                    if not isinstance(pending, dict):
                        raise
                    human_decision = _auto_human_decision(pending)
                    if human_decision is None:
                        raise
                    prior_trace = checkpoint
            solution.metadata["eval_usage"] = {
                "input_tokens": main_acc.prompt_tokens + critic_acc.prompt_tokens,
                "output_tokens": main_acc.completion_tokens + critic_acc.completion_tokens,
                "total_tokens": main_acc.total_tokens + critic_acc.total_tokens,
                "llm_calls": main_acc.calls + critic_acc.calls,
            }
            return solution

    try:
        # pass_k is only forwarded when explicitly set, so the runner default
        # (8) applies otherwise.
        run_kwargs = {"pass_k": args.pass_k} if args.pass_k is not None else {}
        results, summary = await run_evaluation(
            cases, solve, trials=args.trials, **run_kwargs
        )
        write_results(args.output, results, summary)
    finally:
        if mcp_client is not None:
            await mcp_client.close()
        for path in case_plan_memory_paths.values():
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass

    print(json.dumps(summary.to_dict(), ensure_ascii=False, indent=2))
    print(f"Detailed results: {Path(args.output).resolve()}")
    if summary.false_verified_count > 0 or summary.accuracy == 0.0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))
