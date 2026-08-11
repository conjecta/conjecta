#!/usr/bin/env python3
"""Real-LLM check for the new harness mechanisms (hooks + update_plan).

Builds a direct ReActAgent against the production config, registers
pre/post tool hooks, and solves a multi-step problem. Prints the hook
event stream, whether the model used update_plan, and the final answer.

Usage:
    .venv/bin/python scripts/real_llm_hook_check.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from math_agent.agent.hooks import (
    clear_hooks,
    register_post_tool_hook,
    register_pre_tool_hook,
)
from math_agent.agent.react_agent import ReActAgent
from math_agent.agent.tools import ToolRegistry
from math_agent.config import load_config
from math_agent.llm import create_backend

PROBLEM = (
    sys.argv[1]
    if len(sys.argv) > 1
    else (
        "Find the smallest prime number greater than 1000, then compute that "
        "prime modulo 97, then multiply the result by 3. Work step by step "
        "and give the final number."
    )
)

events: list[dict] = []


def _pre(name, args):
    events.append({"phase": "pre", "tool": name})


def _post(name, args, observation):
    events.append(
        {"phase": "post", "tool": name, "obs_chars": len(str(observation))}
    )


async def main() -> int:
    clear_hooks()
    register_pre_tool_hook(_pre)
    register_post_tool_hook(_post)

    config = load_config()
    llm = create_backend(config.llm)
    critic = create_backend(config.critic)
    agent_config = replace(
        config.agent,
        max_react_steps=10,
        memory_consolidation_enabled=False,
    )
    registry = ToolRegistry(
        enabled_tools=["compute", "search"],
        llm=llm,
        agent_config=agent_config,
        knowledge_config=config.knowledge,
        critic_llm=critic,
    )
    agent = ReActAgent(
        llm=llm,
        critic_llm=critic,
        config=agent_config,
        tool_registry=registry,
    )

    solution = await agent.solve(PROBLEM)

    update_plan_calls = [e for e in events if e["tool"] == "update_plan"]
    report = {
        "answer": str(solution.final_answer)[:300],
        "verification_status": solution.verification_status,
        "llm_calls": getattr(solution, "llm_call_count", None),
        "turns": len(solution.trace.turns),
        "hook_events": len(events),
        "hook_tools": sorted({e["tool"] for e in events}),
        "update_plan_called": len(update_plan_calls) > 0,
        "plan_items": getattr(solution.trace, "plan_items", None),
        "events": events,
    }
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
