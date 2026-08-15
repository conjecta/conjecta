"""Lean 4 tools: formalize, lean_check, mathlib search, tactic search, lemmas.

Imports from ``math_agent.tools.lean`` are deferred to call time on purpose:
that module pulls in ``math_agent.agent.*``, whose package ``__init__``
imports the agent (and therefore this package) back — a top-level import here
would create a circular import when ``math_agent.tools`` is loaded first.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from math_agent.lean.mathlib_search import default_search
from math_agent.tools.context import ToolContext
from math_agent.tools.results import ToolResult

log = logging.getLogger("math_agent.tools")


async def _formalize_unavailable_tool(_statement: str, _ctx: ToolContext) -> ToolResult:
    return ToolResult(
        name="formalize",
        output="Lean formalization unavailable (Lean codegen not configured).",
        success=False,
    )


async def _lean_check_unavailable_tool(
    _lean_code: str, _ctx: ToolContext
) -> ToolResult:
    return ToolResult(
        name="lean_check",
        output="Lean check unavailable (Lean toolchain not configured).",
        success=False,
    )


async def _tactic_search_unavailable_tool(
    _args_str: str, _ctx: ToolContext
) -> ToolResult:
    return ToolResult(
        name="tactic_search",
        output="tactic_search unavailable (Lean runner or LLM not configured).",
        success=False,
    )


async def _formalize_tool(statement: str, ctx: ToolContext) -> ToolResult:
    from math_agent.tools.lean import formalize_statement

    lean_codegen = ctx.lean_codegen
    if lean_codegen is not None and ctx.premise_retriever is not None:
        from math_agent.lean.codegen import LeanCodegen

        lean_codegen = LeanCodegen(
            llm=lean_codegen.llm,
            runner=lean_codegen.runner,
            config=lean_codegen.config,
            premise_retriever=ctx.premise_retriever,
        )
    output, lean_code = await formalize_statement(
        _statement_with_formalization_plan(statement, ctx.formalization_plan),
        lean_codegen=lean_codegen,
        state=ctx.state,
    )
    success = lean_code is not None and "PASSED" in output
    return ToolResult(
        name="formalize", output=output, success=success, lean_code=lean_code
    )


def _statement_with_formalization_plan(
    statement: str, plan_data: dict[str, Any] | None
) -> str:
    """Append the unified planner's Lean sketch as guidance for the coder."""
    if not plan_data:
        return statement
    from math_agent.agent.planner import FormalizationPlan

    try:
        known = {
            key: value
            for key, value in plan_data.items()
            if key in FormalizationPlan.__dataclass_fields__
        }
        plan = FormalizationPlan(**known)
    except Exception:
        return statement
    if not (
        plan.restatement or plan.goal_type or plan.lemmas or plan.recommended_imports
    ):
        return statement
    return f"{statement}\n\n{plan.to_prompt_block(include_verified_code=False)}"


def _parse_lean_check_args(args_str: str) -> tuple[str, bool]:
    """Parse lean_check args: JSON {"code": ..., "draft": ...} or raw code string."""
    text = str(args_str or "")
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text, False
    if not isinstance(parsed, dict):
        return text, False
    code = str(parsed.get("code") or "")
    draft_value = parsed.get("draft", False)
    draft = draft_value is True or (
        isinstance(draft_value, str)
        and draft_value.strip().lower() in {"true", "1", "yes"}
    )
    return code, draft


async def _lean_check_tool(args_str: str, ctx: ToolContext) -> ToolResult:
    from math_agent.tools.lean import check_lean_code

    lean_code, draft = _parse_lean_check_args(args_str)
    if re.search(r"(?m)^\s*import\s+Mathlib\s*$", lean_code or ""):
        suggestion = ""
        if ctx.premise_retriever is not None:
            try:
                entries = ctx.premise_retriever.retrieve(lean_code, top_k=3)
                modules = sorted(
                    {e.module for e in entries if e.module.startswith("Mathlib.")}
                )
                if modules:
                    suggestion = " Try precise imports such as: " + ", ".join(
                        f"import {m}" for m in modules
                    )
            except Exception:
                pass
        return ToolResult(
            name="lean_check",
            output=(
                "Rejected: the umbrella `import Mathlib` is too heavy for this "
                "host (it does not finish within the solve budget). Use precise "
                "imports instead (e.g. `import Mathlib.Tactic.Common` plus the "
                "specific module)."
                + suggestion
            ),
            success=False,
        )
    output, code = await check_lean_code(
        lean_code, lean_runner=ctx.lean_runner, draft=draft
    )
    success = code is not None and "PASSED" in output
    draft_ok = not success and code is not None and "DRAFT OK" in output
    if not success and not draft_ok:
        output = (
            "The provided Lean 4 code did not pass verification. "
            "If this was brand-new code, consider using the `formalize` action to regenerate it "
            "from a clear statement and proof sketch, then verify the result with `lean_check`. "
            "If repeated formalize/lean_check repair rounds on this item keep failing, switch to "
            "`tactic_search` (single goal) or `prove_by_lemmas` (lemma decomposition) instead of "
            "repairing the same draft again.\n\n"
            + output
        )
    return ToolResult(name="lean_check", output=output, success=success, lean_code=code)


def _validate_mathlib_query(query: str) -> str | None:
    """Return an error message if the query is not a valid mathlib search string."""
    q = query.strip()
    if not q:
        return "Empty query. Provide an exact declaration name (e.g. 'Nat.gcd_comm') or a Lean type snippet in ASCII (e.g. 'a + b = b + a')."
    if "`" in q:
        return "Do not use backticks (`) in search_mathlib queries. Use plain identifiers or ASCII type snippets."
    # Only allow printable ASCII plus a small set of Lean-accepted Unicode symbols.
    allowed_unicode = set("→←↔∀∃λΠ∧∨≠≤≥⊔⊓⟨⟩⟦⟧")  # noqa: RUF001 -- intentional Lean math symbols
    for ch in q:
        if ord(ch) < 128:
            continue
        if ch not in allowed_unicode:
            return (
                f"Invalid character '{ch}' in search_mathlib query. "
                "Use exact declaration names or ASCII type snippets; do not paste full mathematical expressions."
            )
    return None


async def _search_mathlib_tool(query: str, _ctx: ToolContext) -> ToolResult:
    validation_error = _validate_mathlib_query(query)
    if validation_error:
        return ToolResult(name="search_mathlib", output=validation_error, success=False)
    try:
        search = default_search()
        entries = await asyncio.to_thread(search.search_by_name, query, max_results=5)
        if not entries:
            entries = await asyncio.to_thread(
                search.search_by_type_snippet, query, max_results=5
            )
    except Exception as exc:
        return ToolResult(
            name="search_mathlib", output=f"Search failed: {exc}", success=False
        )

    if not entries:
        return ToolResult(
            name="search_mathlib",
            output=(
                f"No mathlib declarations found for: {query}\n\n"
                "This often means the exact result is not in mathlib4. "
                "Proceed by constructing the proof from first principles using `formalize` or `lean_check`."
            ),
            success=True,
        )

    lines = [f"Found {len(entries)} declaration(s):"]
    for e in entries:
        name = e.get("name", "")
        module = e.get("module", "")
        signature = e.get("signature", "") or e.get("type", "")
        lines.append(f"- {name} in {module}")
        if signature:
            lines.append(f"  signature: {signature}")
    return ToolResult(name="search_mathlib", output="\n".join(lines), success=True)


async def _tactic_search_tool(args_str: str, ctx: ToolContext) -> ToolResult:
    from math_agent.lean.proof_search import ProofSearch, TacticGenerator

    if ctx.lean_runner is None or ctx.llm is None:
        return ToolResult(
            name="tactic_search",
            output="tactic_search unavailable (Lean runner or LLM not configured).",
            success=False,
        )

    try:
        args = json.loads(args_str)
    except json.JSONDecodeError as e:
        return ToolResult(
            name="tactic_search",
            output=f"Invalid JSON args: {e}",
            success=False,
        )

    theorem = str(args.get("theorem_statement", "")).strip()
    if not theorem:
        return ToolResult(
            name="tactic_search",
            output="tactic_search requires a non-empty 'theorem_statement'.",
            success=False,
        )
    imports = str(args.get("imports", "")).strip()
    if re.search(r"(?m)^\s*import\s+Mathlib\s*$", imports):
        return ToolResult(
            name="tactic_search",
            output=(
                "tactic_search rejects the umbrella `import Mathlib` (too heavy "
                "for this host); pass precise imports like `import Mathlib.Algebra...`."
            ),
            success=False,
        )

    # Cheap pre-validation: elaborate the statement once (with a draft `sorry`
    # body) before entering REPL/batch search. A statement that does not parse
    # or elaborate would silently burn the whole search budget; return the
    # error immediately so the model can fix the statement.
    probe_statement = theorem
    if not re.search(r":=\s*by\s*$", probe_statement):
        probe_statement = f"{probe_statement} := by"
    probe_code = f"{imports}\n\n{probe_statement}\n  sorry" if imports else (
        f"{probe_statement}\n  sorry"
    )
    probe = await ctx.lean_runner.check_proof(probe_code, draft=True)
    if not probe.success and probe.failure_kind not in {
        "timeout",
        "lean_unavailable",
    }:
        detail = "\n".join(probe.errors) if probe.errors else probe.output
        return ToolResult(
            name="tactic_search",
            output=(
                "tactic_search rejected the statement: it does not parse/elaborate "
                "in Lean 4. Fix the statement (or its imports) and call "
                f"tactic_search again.\nErrors:\n{detail[:2000]}"
            ),
            success=False,
        )

    config_max_attempts = (
        ctx.agent_config.tactic_search_max_attempts
        if ctx.agent_config is not None
        else 32
    )
    try:
        max_attempts = int(args.get("max_attempts", config_max_attempts))
        max_attempts = (
            config_max_attempts
            if max_attempts < 1
            else min(max_attempts, config_max_attempts)
        )
    except (ValueError, TypeError):
        max_attempts = config_max_attempts

    config_max_depth = (
        ctx.agent_config.tactic_search_max_depth if ctx.agent_config is not None else 8
    )
    generator = TacticGenerator(
        ctx.prover_llm or ctx.llm,
        max_candidates=3,
        premise_retriever=ctx.premise_retriever,
        trace_memory=ctx.trace_memory,
        critic_llm=ctx.critic_llm,
    )
    search = ProofSearch(
        generator=generator,
        runner=ctx.lean_runner,
        max_attempts=max_attempts,
        max_depth=config_max_depth,
        premise_retriever=ctx.premise_retriever,
        repl_pool=ctx.repl_pool,
        imports=imports,
        trace_memory=ctx.trace_memory,
        progress_callback=ctx.event_callback,
    )

    try:
        wall_seconds = (
            ctx.agent_config.tactic_search_wall_seconds
            if ctx.agent_config is not None
            else 120.0
        )
        result = await asyncio.wait_for(search.search(theorem), timeout=wall_seconds)
    except asyncio.TimeoutError:
        return ToolResult(
            name="tactic_search",
            output="tactic_search timed out before finding a proof.",
            success=False,
        )
    except Exception as e:
        return ToolResult(
            name="tactic_search",
            output=f"tactic_search failed: {e}",
            success=False,
        )

    if result.success:
        return ToolResult(
            name="tactic_search",
            output=f"Proof found after {result.attempts} attempts:\n```lean\n{result.proof}\n```",
            success=True,
            lean_code=result.proof,
        )

    return ToolResult(
        name="tactic_search",
        output=(
            f"No proof found after {result.attempts} attempts. "
            f"Deepest partial proof:\n```lean\n{result.proof}\n```\n"
            f"Error: {result.error}"
        ),
        success=False,
        lean_code=result.proof,
    )


async def _prove_by_lemmas_unavailable_tool(
    _args_str: str, _ctx: ToolContext
) -> ToolResult:
    return ToolResult(
        name="prove_by_lemmas",
        output="prove_by_lemmas unavailable (Lean runner or LLM not configured).",
        success=False,
    )


def _parse_prove_by_lemmas_args(
    args_str: str,
) -> tuple[str, list[dict[str, Any]] | None]:
    """Parse prove_by_lemmas args: JSON {"statement": ..., "lemmas": ...} or a raw statement."""
    text = str(args_str or "")
    statement = ""
    lemmas_raw: Any = None
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        statement = str(parsed.get("statement") or "").strip()
        lemmas_raw = parsed.get("lemmas")
    elif isinstance(parsed, str):
        statement = parsed.strip()
    else:
        # Tolerate a raw (non-JSON) statement string.
        statement = text.strip()

    if isinstance(lemmas_raw, str):
        try:
            lemmas_raw = json.loads(lemmas_raw)
        except (json.JSONDecodeError, TypeError):
            lemmas_raw = None
    lemmas: list[dict[str, Any]] | None = None
    if isinstance(lemmas_raw, list):
        lemmas = [item for item in lemmas_raw if isinstance(item, dict)]
        if not lemmas:
            lemmas = None
    return statement, lemmas


def _plan_from_ctx_dict(plan_data: dict[str, Any] | None) -> Any:
    """Parse the unified planner's Lean sketch dict into a FormalizationPlan (or None)."""
    if not plan_data:
        return None
    from math_agent.agent.planner import FormalizationPlan

    try:
        known = {
            key: value
            for key, value in plan_data.items()
            if key in FormalizationPlan.__dataclass_fields__
        }
        return FormalizationPlan(**known)
    except Exception:
        return None


async def _plan_lemma_decomposition(statement: str, ctx: ToolContext) -> Any:
    """Run the FormalizationPlanner to obtain goal_type/imports and a lemma decomposition."""
    from math_agent.agent.planner import FormalizationPlanner

    planner = FormalizationPlanner(ctx.llm)  # type: ignore[arg-type]
    try:
        return await planner.plan(statement)
    except Exception as exc:
        log.warning("prove_by_lemmas planning failed: %s", exc)
        return None


async def _prove_by_lemmas_tool(args_str: str, ctx: ToolContext) -> ToolResult:
    from math_agent.lean.lemma_executor import (
        LemmaDAGExecutor,
        sanitize_lemma_statement,
    )
    from math_agent.tools.lean import append_axioms_line, format_lean_result

    if ctx.lean_runner is None or ctx.llm is None:
        return ToolResult(
            name="prove_by_lemmas",
            output="prove_by_lemmas unavailable (Lean runner or LLM not configured).",
            success=False,
        )

    statement, lemmas = _parse_prove_by_lemmas_args(args_str)
    if not statement:
        return ToolResult(
            name="prove_by_lemmas",
            output="prove_by_lemmas requires a non-empty 'statement'.",
            success=False,
        )

    ctx_plan = _plan_from_ctx_dict(ctx.formalization_plan)
    plan = None
    if lemmas is not None:
        # The model supplied its own decomposition; reuse the existing plan's
        # goal_type/imports when available, otherwise plan them from scratch.
        base = ctx_plan if (ctx_plan is not None and ctx_plan.goal_type.strip()) else None
        if base is None:
            base = await _plan_lemma_decomposition(statement, ctx)
        if base is None or not base.goal_type.strip():
            return ToolResult(
                name="prove_by_lemmas",
                output=(
                    "Could not determine a Lean 4 goal type for this statement, so the "
                    "supplied lemmas cannot be assembled into a theorem. Try `formalize` "
                    "for a one-shot proof instead."
                ),
                success=False,
            )
        base.lemmas = lemmas
        base.problem = statement
        plan = base
    elif ctx_plan is not None and ctx_plan.lemmas and ctx_plan.goal_type.strip():
        # Reuse the unified planner's decomposition drafted up front.
        plan = ctx_plan
        if not plan.problem:
            plan.problem = statement
    else:
        plan = await _plan_lemma_decomposition(statement, ctx)
        if plan is None or not plan.lemmas or not plan.goal_type.strip():
            return ToolResult(
                name="prove_by_lemmas",
                output=(
                    "Could not produce a lemma decomposition for this statement (the planner "
                    "returned no lemmas or no goal type). Try `formalize` for a one-shot proof, "
                    "or call prove_by_lemmas again with an explicit 'lemmas' decomposition."
                ),
                success=False,
            )

    max_repair_attempts: int | None = None
    wall_seconds: float | None = None
    lean_codegen = ctx.lean_codegen
    if lean_codegen is not None:
        codegen_config = getattr(lean_codegen, "config", None)
        configured = getattr(codegen_config, "max_repair_attempts", None)
        if isinstance(configured, int) and configured > 0:
            max_repair_attempts = configured
        configured_wall = getattr(codegen_config, "lemma_executor_wall_seconds", None)
        if isinstance(configured_wall, (int, float)) and configured_wall > 0:
            wall_seconds = float(configured_wall)
    if wall_seconds is None:
        # Fall back to the runner's LeanConfig when no codegen is wired.
        runner_config = getattr(ctx.lean_runner, "config", None)
        configured_wall = getattr(runner_config, "lemma_executor_wall_seconds", None)
        if isinstance(configured_wall, (int, float)) and configured_wall > 0:
            wall_seconds = float(configured_wall)
    if max_repair_attempts is None:
        # Same fallback for the repair budget so lean.max_repair_attempts is
        # the single source (the executor defaults to LeanConfig otherwise).
        runner_config = getattr(ctx.lean_runner, "config", None)
        configured = getattr(runner_config, "max_repair_attempts", None)
        if isinstance(configured, int) and configured > 0:
            max_repair_attempts = configured
    max_parallel = 1
    rescue_enabled = False
    route_count = 1
    rescue_max_depth: int | None = None
    route_temperatures: list[float] | None = None
    difficulty_threshold: int | None = None
    max_routes_hard: int | None = None
    hook_max_attempts: int | None = None
    hook_max_depth: int | None = None
    for source in (
        getattr(ctx.lean_codegen, "config", None),
        getattr(ctx.lean_runner, "config", None),
    ):
        if source is None:
            continue
        configured_parallel = getattr(source, "lemma_max_parallel", None)
        if isinstance(configured_parallel, int) and configured_parallel > 0:
            max_parallel = configured_parallel
        if getattr(source, "lemma_rescue_enabled", False) is True:
            rescue_enabled = True
        configured_routes = getattr(source, "lemma_route_count", None)
        if isinstance(configured_routes, int) and configured_routes > 0:
            route_count = configured_routes
        configured_depth = getattr(source, "lemma_rescue_max_depth", None)
        if isinstance(configured_depth, int) and configured_depth > 0:
            rescue_max_depth = configured_depth
        configured_temperatures = getattr(source, "lemma_route_temperatures", None)
        if isinstance(configured_temperatures, (list, tuple)) and configured_temperatures:
            route_temperatures = [float(t) for t in configured_temperatures]
        configured_threshold = getattr(source, "lemma_difficulty_threshold", None)
        if isinstance(configured_threshold, int) and configured_threshold > 0:
            difficulty_threshold = configured_threshold
        configured_hard = getattr(source, "lemma_max_routes_hard", None)
        if isinstance(configured_hard, int) and configured_hard > 0:
            max_routes_hard = configured_hard
        configured_hook_attempts = getattr(source, "lemma_hook_max_attempts", None)
        if isinstance(configured_hook_attempts, int) and configured_hook_attempts > 0:
            hook_max_attempts = configured_hook_attempts
        configured_hook_depth = getattr(source, "lemma_hook_max_depth", None)
        if isinstance(configured_hook_depth, int) and configured_hook_depth > 0:
            hook_max_depth = configured_hook_depth
        break

    # Prover-first hook: try REPL tactic search (with the dedicated prover
    # model when configured) before spending an LLM codegen round per lemma.
    search_hook = None
    if ctx.repl_pool is not None:
        from math_agent.lean.proof_search import ProofSearch, TacticGenerator
        from math_agent.lean.repl_session import LeanReplPool

        pool_config = getattr(ctx.repl_pool, "config", None)
        if pool_config is not None and LeanReplPool.available(pool_config):
            hook_imports = "\n".join(
                f"import {module}"
                for module in sorted(
                    set(plan.recommended_imports) | {"Mathlib.Tactic.Common"}
                )
            )
            hook_generator = TacticGenerator(
                ctx.prover_llm or ctx.llm,
                max_candidates=3,
                premise_retriever=ctx.premise_retriever,
                trace_memory=ctx.trace_memory,
                critic_llm=ctx.critic_llm,
            )
            hook_search = ProofSearch(
                generator=hook_generator,
                runner=ctx.lean_runner,
                max_attempts=hook_max_attempts if hook_max_attempts is not None else 12,
                max_depth=hook_max_depth if hook_max_depth is not None else 6,
                premise_retriever=ctx.premise_retriever,
                repl_pool=ctx.repl_pool,
                imports=hook_imports,
                trace_memory=ctx.trace_memory,
            )

            async def search_hook(name: str, statement: str) -> str | None:
                # Guard the wrap: a statement that still carries its own
                # declaration header would produce unparseable Lean here.
                bare = sanitize_lemma_statement(statement)
                if not bare:
                    return None
                result = await hook_search.search(f"lemma {name} : {bare} := by")
                return result.proof if result.success else None

    executor = LemmaDAGExecutor(
        llm=ctx.llm,  # type: ignore[arg-type]
        runner=ctx.lean_runner,
        plan=plan,
        problem=statement,
        max_repair_attempts=max_repair_attempts,
        session_log=ctx.session_log,
        progress_callback=ctx.event_callback,
        wall_seconds=wall_seconds,
        search_hook=search_hook,
        max_parallel=max_parallel,
        rescue_enabled=rescue_enabled,
        route_count=route_count,
        rescue_max_depth=rescue_max_depth,
        route_temperatures=route_temperatures,
        difficulty_threshold=difficulty_threshold,
        max_routes_hard=max_routes_hard,
    )
    try:
        code = await executor.execute()
    except Exception as exc:
        log.exception("prove_by_lemmas executor failed")
        return ToolResult(
            name="prove_by_lemmas",
            output=f"Lemma-decomposition proof failed with an internal error: {exc}",
            success=False,
        )

    if not code:
        output = (
            "Lemma-decomposition proof failed: one of the lemmas or the final theorem "
            "could not be verified in Lean. Try `formalize` or `tactic_search`, or call "
            "prove_by_lemmas again with a different 'lemmas' decomposition."
        )
        # Hand partial progress back so the agent can reroute around the
        # failed step instead of starting from scratch.
        verified = list(getattr(executor, "verified_lemmas", []) or [])
        if verified:
            output += "\n\nVerified lemmas before the failure (you may reuse these):\n"
            output += "\n".join(f"\n```lean\n{lemma_code}\n```" for lemma_code in verified)
        failure = getattr(executor, "last_failure", None) or {}
        if failure:
            detail = str(failure.get("detail") or "")[:400]
            output += (
                f"\n\nFailure diagnostic: lemma={failure.get('lemma')}"
                f" failure_kind={failure.get('failure_kind')} detail={detail}"
            )
        return ToolResult(
            name="prove_by_lemmas",
            output=output,
            success=False,
        )

    try:
        result = await ctx.lean_runner.check_proof(code)
    except Exception as exc:
        return ToolResult(
            name="prove_by_lemmas",
            output=f"Final Lean verification failed: {exc}",
            success=False,
        )
    output = format_lean_result(code, result)
    success = bool(result.success and not result.uses_sorry)
    if success:
        output = await append_axioms_line(ctx.lean_runner, code, output)
    return ToolResult(
        name="prove_by_lemmas",
        output=output,
        success=success,
        lean_code=code,
    )
