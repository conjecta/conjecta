"""Lean 4 tool handlers for the reasoning agent."""
from __future__ import annotations

from math_agent.agent.formal_evidence import extract_formal_declarations
from math_agent.agent.state import ReasoningState, ReasoningStep, StepType
from math_agent.lean.codegen import LeanCodegen
from math_agent.lean.runner import LeanResult, LeanRunner


def format_lean_result(lean_code: str, result: LeanResult) -> str:
    """Format Lean codegen/check output for the agent context window."""
    lines: list[str] = []
    if result.success and not result.uses_sorry:
        lines.append("Lean verification: PASSED")
    elif result.success and result.uses_sorry and result.draft:
        lines.append(
            "Lean verification: DRAFT OK (type-checks with 'sorry' hole(s) "
            "remaining — this is NOT a complete proof; re-run lean_check "
            "without draft once all holes are filled)"
        )
    elif result.success and result.uses_sorry:
        lines.append("Lean verification: INCOMPLETE (proof contains 'sorry')")
    else:
        lines.append("Lean verification: FAILED")

    failure_kind = getattr(result, "failure_kind", None)
    if not result.success and failure_kind:
        # Machine-parseable category so escalation routing (and the agent)
        # can distinguish coding errors from wrong proof strategy.
        lines.append(f"Failure kind: {failure_kind}")

    if result.errors:
        lines.append("Errors:")
        lines.extend(f"- {err}" for err in result.errors[:8])
    if result.warnings:
        lines.append("Warnings:")
        lines.extend(f"- {warn}" for warn in result.warnings[:5])

    if lean_code.strip():
        lines.append("")
        lines.append("Lean code:")
        lines.append(f"```lean\n{lean_code.strip()}\n```")
    return "\n".join(lines)


async def formalize_statement(
    statement: str,
    *,
    lean_codegen: LeanCodegen | None,
    state: ReasoningState | None,
) -> tuple[str, str | None]:
    """Generate and verify Lean 4 code for a mathematical statement."""
    if lean_codegen is None:
        return "Lean formalization unavailable (Lean codegen not configured).", None
    if state is None:
        return "Lean formalization unavailable (no reasoning context).", None

    step = ReasoningStep(content=statement.strip(), step_type=StepType.FORMALIZATION)
    lean_code, result = await lean_codegen.generate_and_verify(step, state)
    if lean_code is None or result is None:
        return "Lean formalization failed to produce code.", None
    output = format_lean_result(lean_code, result)
    if result.success and not result.uses_sorry:
        output = await append_axioms_line(lean_codegen.runner, lean_code, output)
    return output, lean_code


async def append_axioms_line(
    lean_runner: LeanRunner | None, lean_code: str, output: str
) -> str:
    """Append a `#print axioms` summary line after a strict verification pass.

    Best effort: any failure leaves the output unchanged — axiom reporting
    must never turn a verified proof into a failure.
    """
    if lean_runner is None:
        return output
    declarations = extract_formal_declarations(lean_code)
    if not declarations:
        return output
    printer = getattr(lean_runner, "print_axioms", None)
    if printer is None:
        return output
    try:
        axioms = await printer(lean_code, declarations[-1]["name"])
    except Exception:
        return output
    if axioms is None:
        return output
    summary = ", ".join(axioms) if axioms else "none"
    return f"{output}\nAxioms: {summary}"


# Default cap for one lean_check input; the effective value comes from
# LeanConfig.max_check_chars via the runner's config when available.
_MAX_LEAN_CHECK_CHARS = 8000


async def check_lean_code(
    lean_code: str,
    *,
    lean_runner: LeanRunner | None,
    draft: bool = False,
) -> tuple[str, str | None]:
    """Type-check existing Lean 4 source code.

    With ``draft=True``, ``sorry``/``admit`` holes are tolerated so partial
    proof skeletons can be checked early and often; a draft pass is never a
    complete proof.
    """
    if lean_runner is None:
        return "Lean check unavailable (Lean toolchain not configured).", None

    configured_max = getattr(
        getattr(lean_runner, "config", None), "max_check_chars", None
    )
    max_chars = (
        configured_max
        if isinstance(configured_max, int) and configured_max > 0
        else _MAX_LEAN_CHECK_CHARS
    )
    code = lean_code.strip()
    if not code:
        return "lean_check requires non-empty Lean 4 source code.", None
    if len(code) > max_chars:
        return (
            f"lean_check input is too long ({len(code)} chars; max {max_chars}). "
            "Break the proof into smaller lemmas and check them one at a time.",
            None,
        )

    result = await lean_runner.check_proof(code, draft=draft)
    output = format_lean_result(code, result)
    if not draft and result.success and not result.uses_sorry:
        output = await append_axioms_line(lean_runner, code, output)
    return output, code
