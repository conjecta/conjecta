"""Early claim check for non-easy normal-mode solves."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from math_agent.agent.react_state import ProjectContext, ReActTrace
from math_agent.agent.refutation import run_computational_refute
from math_agent.agent.tools import ToolRegistry
from math_agent.llm.base import LLMBackend
from math_agent.types import EventCallback
from math_agent.web.json_utils import complete_json_object

log = logging.getLogger("math_agent.agent.claim_check")

_AUDIT_STATUSES = frozenset({"ok", "false_as_stated", "needs_clarification"})

_AUDIT_SYSTEM = """You audit a mathematical claim before a proof attempt. Look for:
- statements that are false as written (e.g. requiring strict inequalities on sequences that allow repeats)
- illegal WLOG / perturbation arguments that would not preserve the claim
- ambiguous quantifiers or missing hypotheses

Output only JSON:
{
  "status": "ok" | "false_as_stated" | "needs_clarification",
  "issues": ["short issue", ...],
  "revised_claim": "corrected statement or empty string"
}
Be conservative: only mark false_as_stated when you can point to a concrete counterexample pattern or clear logical gap in the stated hypotheses."""


@dataclass
class ClaimCheckResult:
    status: str = "ok"
    issues: list[str] = field(default_factory=list)
    revised_claim: str = ""
    counterexample_found: bool = False
    refute_summary: str = ""
    refute: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "issues": list(self.issues),
            "revised_claim": self.revised_claim,
            "counterexample_found": self.counterexample_found,
            "refute_summary": self.refute_summary,
            "refute": dict(self.refute),
        }

    @property
    def blocks_original_claim(self) -> bool:
        return self.counterexample_found or self.status == "false_as_stated"


def normalize_audit(data: dict[str, Any] | None) -> ClaimCheckResult:
    raw = data if isinstance(data, dict) else {}
    status = str(raw.get("status") or "ok").strip().lower()
    if status not in _AUDIT_STATUSES:
        status = "ok"
    issues_raw = raw.get("issues") or []
    issues: list[str] = []
    if isinstance(issues_raw, list):
        for item in issues_raw:
            text = str(item or "").strip()
            if text:
                issues.append(text[:500])
    revised = str(raw.get("revised_claim") or "").strip()
    return ClaimCheckResult(status=status, issues=issues, revised_claim=revised)


def format_claim_check_preamble(result: ClaimCheckResult) -> str:
    lines = ["Claim check (pre-solve):"]
    lines.append(f"- status: {result.status}")
    if result.issues:
        lines.append("- issues:")
        for issue in result.issues[:8]:
            lines.append(f"  - {issue}")
    if result.counterexample_found:
        lines.append("- computational counterexample: found")
        if result.refute_summary:
            lines.append(f"  summary: {result.refute_summary[:1500]}")
    elif result.refute_summary:
        lines.append(f"- refute note: {result.refute_summary[:800]}")
    if result.revised_claim:
        lines.append(f"- revised claim:\n{result.revised_claim[:3000]}")
    if result.blocks_original_claim:
        lines.append(
            "- instruction: Do NOT claim the original statement is proved. "
            "Report that it is false as stated (with the counterexample/issues), "
            "and prove or state the revised claim if one is given."
        )
    elif result.status == "needs_clarification":
        lines.append(
            "- instruction: Note the ambiguities; proceed carefully and do not "
            "paper over missing hypotheses with illegal WLOG."
        )
    return "\n".join(lines)


async def run_claim_check(
    *,
    problem: str,
    llm: LLMBackend,
    critic_llm: LLMBackend,
    tool_registry: ToolRegistry | None,
    project_context: ProjectContext | None = None,
    refute_enabled: bool = True,
    max_tool_calls: int = 1,
    on_event: EventCallback | None = None,
) -> ClaimCheckResult:
    """Run hypothesis audit then optional computational refute."""
    audit_data = await complete_json_object(
        critic_llm,
        user=f"Claim to audit:\n{problem}",
        system=_AUDIT_SYSTEM,
        temperature=0.0,
    )
    result = normalize_audit(audit_data)
    claim_for_refute = result.revised_claim or problem
    if on_event is not None and refute_enabled:
        await on_event(
            {
                "type": "stage_status",
                "stage": "claim_check",
                "message": "正在用计算检验命题…",
                "ui": "status_bar",
            }
        )
    try:
        refute = await run_computational_refute(
            claim=claim_for_refute,
            llm=llm,
            critic_llm=critic_llm,
            tool_registry=tool_registry,
            project_context=project_context,
            enabled=refute_enabled,
            max_tool_calls=max_tool_calls,
            on_event=on_event,
        )
    except Exception as exc:
        log.warning("Computational refute failed during claim check: %s", exc)
        refute = {
            "counterexample_found": False,
            "summary": f"Refute failed: {exc}",
            "status": "error",
        }
    result.refute = dict(refute or {})
    result.counterexample_found = bool(result.refute.get("counterexample_found"))
    result.refute_summary = str(result.refute.get("summary") or "").strip()
    revised_from_refute = str(result.refute.get("revised_statement") or "").strip()
    if result.counterexample_found and revised_from_refute and not result.revised_claim:
        result.revised_claim = revised_from_refute
    if result.counterexample_found and result.status == "ok":
        result.status = "false_as_stated"
    return result


def apply_claim_check_to_trace(trace: ReActTrace, result: ClaimCheckResult) -> None:
    """Store result and append preamble block (idempotent if already applied)."""
    trace.claim_check = result.to_dict()
    block = format_claim_check_preamble(result)
    if "Claim check (pre-solve):" in (trace.context_preamble or ""):
        return
    if trace.context_preamble:
        trace.context_preamble = f"{trace.context_preamble.rstrip()}\n\n{block}"
    else:
        trace.context_preamble = block
