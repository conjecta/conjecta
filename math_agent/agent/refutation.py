"""Shared computational refutation pass for research and normal-mode claim check."""
from __future__ import annotations

import json
from typing import Any

from math_agent.agent.react_state import Action, ProjectContext
from math_agent.agent.tools import ToolContext, ToolRegistry
from math_agent.llm.base import LLMBackend
from math_agent.types import EventCallback
from math_agent.web.json_utils import complete_json_object

REFUTE_SYSTEM = """You plan an early counterexample search for one mathematical claim. Use a computational tool only when finite examples, symbolic manipulation, or CAS checks can genuinely falsify the claim. Failure to find a counterexample is never a proof.

Output only JSON:
{"applicable": true, "action": {"name": "compute", "args": {"code": "..."}}, "rationale": "..."}
or
{"applicable": false, "action": null, "rationale": "why computation cannot meaningfully test it"}."""

REFUTE_JUDGE_SYSTEM = """You are a conservative counterexample judge. Decide whether the tool output contains a concrete counterexample to the exact claim. A failed search or a few passing cases is not a proof.

Output only JSON:
{"counterexample_found": false, "summary": "...", "revised_statement": ""}
If a genuine counterexample is present, set counterexample_found=true and optionally give a precise repaired statement."""


async def run_computational_refute(
    *,
    claim: str,
    llm: LLMBackend,
    critic_llm: LLMBackend,
    tool_registry: ToolRegistry | None,
    project_context: ProjectContext | None = None,
    enabled: bool = True,
    max_tool_calls: int = 1,
    on_event: EventCallback | None = None,
) -> dict[str, Any]:
    """Plan and optionally execute one computational falsification attempt."""
    if not enabled:
        return {
            "counterexample_found": False,
            "summary": "Refutation pass disabled.",
        }
    if tool_registry is None:
        return {
            "counterexample_found": False,
            "summary": "No computational refutation tool is available.",
            "status": "unavailable",
        }
    available = [
        name
        for name in tool_registry.available
        if name == "compute" or "sagemath" in name.casefold()
    ]
    if not available:
        return {
            "counterexample_found": False,
            "summary": "No computational refutation tool is available.",
            "status": "unavailable",
        }
    if max_tool_calls <= 0:
        return {
            "counterexample_found": False,
            "summary": "Refutation tool budget exhausted.",
            "status": "budget_exhausted",
        }

    plan = await complete_json_object(
        llm,
        user=(
            f"Claim:\n{claim}\n\n"
            f"Available tools: {', '.join(available)}\n"
            "For compute, args must contain Python code under the key code. "
            "The code must print the tested cases and any counterexample."
        ),
        system=REFUTE_SYSTEM,
        temperature=0.0,
    )
    if not isinstance(plan, dict) or not plan.get("applicable"):
        return {
            "counterexample_found": False,
            "summary": str(
                (plan or {}).get("rationale")
                or "No meaningful finite refutation pass."
            ),
            "status": "not_applicable",
        }
    raw_action = plan.get("action")
    if not isinstance(raw_action, dict):
        return {
            "counterexample_found": False,
            "summary": "Invalid refutation plan.",
        }
    name = str(raw_action.get("name") or "")
    args = raw_action.get("args")
    if name not in available or not isinstance(args, dict):
        return {
            "counterexample_found": False,
            "summary": "Refutation planner requested an unavailable or invalid tool.",
            "status": "invalid_plan",
        }
    if on_event is not None:
        await on_event(
            {
                "type": "stage_status",
                "stage": "claim_check",
                "message": f"正在计算验证（{name}）…",
                "ui": "status_bar",
            }
        )
        await on_event(
            {
                "type": "tool_start",
                "step_num": "claim_check",
                "tool": name,
                "args_preview": json.dumps(args, ensure_ascii=False)[:2000],
            }
        )
    observation = await tool_registry.execute_action(
        Action(name=name, args=args),
        ToolContext(
            project_context=project_context or ProjectContext(),
            llm=llm,
        ),
    )
    if on_event is not None:
        await on_event(
            {
                "type": "tool_done",
                "step_num": "claim_check",
                "tool": name,
                "success": observation.success,
                "output": observation.output[:2000],
                "error": observation.error,
            }
        )
    verdict = await complete_json_object(
        critic_llm,
        user=(
            f"Exact claim:\n{claim}\n\n"
            f"Tool: {name}\nTool success: {observation.success}\n"
            f"Tool output:\n{observation.output[:8000]}"
        ),
        system=REFUTE_JUDGE_SYSTEM,
        temperature=0.0,
    )
    result = dict(verdict or {})
    result.setdefault("counterexample_found", False)
    result.setdefault("summary", observation.output[:1000])
    result["tool"] = name
    result["tool_success"] = observation.success
    result["tool_output"] = observation.output[:20_000]
    return result
