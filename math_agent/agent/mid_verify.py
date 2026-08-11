"""Mid-trace verification checkpoints for non-easy normal-mode solves.

While the ReAct loop runs, a conservative judge inspects the latest turn and,
when the turn asserts a checkable intermediate claim, verifies it once —
computationally (counterexample search) or formally (Lean). The verdict is
annotated onto the turn's observation so the agent sees it on the next step.
Verification failures are local corrections: they never abort the solve, they
only feed back diagnostics and increment a correction counter that can arm an
escalation signal for higher-tier orchestration.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from math_agent.agent.react_state import Action, ProjectContext, ReActTurn
from math_agent.agent.refutation import run_computational_refute
from math_agent.agent.tools import ToolContext, ToolRegistry
from math_agent.llm.base import LLMBackend
from math_agent.types import EventCallback
from math_agent.web.json_utils import complete_json_object

log = logging.getLogger("math_agent.agent.mid_verify")


def clip_text(text: str, limit: int) -> str:
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."

CHECKPOINT_JUDGE_SYSTEM = """You inspect one step of a math problem-solving trace and decide whether it asserts an intermediate claim that can be machine-checked right now.

Checkable means a concrete, self-contained mathematical statement that later steps will rely on, e.g. a computed value, an inequality just derived, a lemma just claimed. Skip pure planning, literature search, restatements of the problem, and vague progress notes.

Output only JSON:
{"checkable": true, "claim": "precise statement", "method": "compute" or "lean", "reason": "short"}
or
{"checkable": false, "claim": "", "method": "none", "reason": "short"}

Choose "compute" when finite examples or symbolic/numeric calculation can test the claim. Choose "lean" only for claims that are essentially logical/algebraic derivations that a proof assistant could verify. Be conservative: when in doubt, set checkable=false."""


@dataclass
class MidVerifyResult:
    checked: bool = False
    method: str = "none"
    claim: str = ""
    passed: bool = True
    summary: str = ""
    revised_claim: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "method": self.method,
            "claim": self.claim,
            "passed": self.passed,
            "summary": self.summary,
            "revised_claim": self.revised_claim,
        }


# Cheap pre-filter for the judge call: a step whose thought/action/observation
# contains no concrete mathematical content at all (no numbers, relations, or
# formulas) cannot assert a machine-checkable claim, so the judge LLM call is
# skipped outright. Anything with a hint of checkable content still goes to
# the judge, which remains the authority on checkability.
_MACHINE_CHECK_HINT_RE = re.compile(
    r"(?:\d|=|≠|≤|≥|≡|∀|∃|∈|∣|\\frac|\\sum|\\sqrt|\\pmod|"
    r"\bdivides\b|\bprime\b|\bmod\b|\binequality\b|\bequation\b)",
    re.IGNORECASE,
)


def clearly_not_checkable(turn: ReActTurn) -> bool:
    """Heuristic: no numeric/relational content means nothing to machine-check."""
    text = f"{turn.thought}\n{turn.action.args}\n{turn.observation.output}"
    return not _MACHINE_CHECK_HINT_RE.search(text)


def format_mid_verify_note(result: MidVerifyResult) -> str:
    """Short annotation appended to the turn's observation (clipped downstream)."""
    if not result.checked:
        return ""
    claim = clip_text(result.claim, 200)
    if result.passed:
        if result.method == "compute":
            return (
                f"\n\n[mid-verify] Counterexample search found no violation of: {claim}"
            )
        return f"\n\n[mid-verify ok] Lean check passed for: {claim}"
    lines = [f"\n\n[mid-verify FAILED] The intermediate claim looks wrong: {claim}"]
    summary = clip_text(result.summary, 300)
    if summary:
        lines.append(f"Evidence: {summary}")
    if result.revised_claim:
        lines.append(f"Suggested correction: {clip_text(result.revised_claim, 200)}")
    lines.append("Do not build on this claim; revise it before proceeding.")
    return "\n".join(lines)


async def run_mid_verify(
    *,
    turn: ReActTurn,
    problem: str,
    llm: LLMBackend,
    critic_llm: LLMBackend,
    tool_registry: ToolRegistry | None,
    project_context: ProjectContext | None = None,
    on_event: EventCallback | None = None,
) -> MidVerifyResult:
    """Judge the latest turn and, when checkable, verify its claim once."""
    if clearly_not_checkable(turn):
        return MidVerifyResult(summary="heuristic: no machine-checkable content")
    verdict = await complete_json_object(
        critic_llm,
        user=(
            f"Problem: {clip_text(problem, 800)}\n\n"
            f"Latest step thought: {clip_text(turn.thought, 800)}\n"
            f"Action: {turn.action.name}({clip_text(str(turn.action.args), 400)})\n"
            f"Observation: {clip_text(turn.observation.output, 800)}\n\n"
            "Does this step assert a checkable intermediate claim?"
        ),
        system=CHECKPOINT_JUDGE_SYSTEM,
        temperature=0.0,
    )
    if not isinstance(verdict, dict) or not verdict.get("checkable"):
        return MidVerifyResult(
            summary=str((verdict or {}).get("reason") or "not checkable")[:300]
        )
    claim = str(verdict.get("claim") or "").strip()
    method = str(verdict.get("method") or "none").strip().lower()
    if not claim or method not in {"compute", "lean"}:
        return MidVerifyResult(summary="judge produced no usable claim")

    if tool_registry is None:
        return MidVerifyResult(summary="no tool registry available")

    if method == "compute":
        refute = await run_computational_refute(
            claim=claim,
            llm=llm,
            critic_llm=critic_llm,
            tool_registry=tool_registry,
            project_context=project_context,
            enabled=True,
            max_tool_calls=1,
            on_event=on_event,
        )
        status = str(refute.get("status") or "")
        if status in {"unavailable", "budget_exhausted", "invalid_plan", "error"}:
            return MidVerifyResult(
                summary=f"compute verification unavailable ({status})"
            )
        counterexample = bool(refute.get("counterexample_found"))
        return MidVerifyResult(
            checked=True,
            method="compute",
            claim=claim,
            passed=not counterexample,
            summary=str(refute.get("summary") or "")[:1000],
            revised_claim=str(refute.get("revised_statement") or "").strip(),
        )

    # method == "lean": formalize the claim and treat a passing Lean check as
    # verification; failure is advisory evidence, not a hard refutation.
    if "formalize" not in tool_registry.available:
        return MidVerifyResult(summary="formalize tool unavailable")
    observation = await tool_registry.execute_action(
        Action(name="formalize", args={"statement": claim}),
        ToolContext(
            project_context=project_context or ProjectContext(),
            llm=llm,
        ),
    )
    return MidVerifyResult(
        checked=True,
        method="lean",
        claim=claim,
        passed=observation.success,
        summary=(observation.error or observation.output)[:1000],
    )
