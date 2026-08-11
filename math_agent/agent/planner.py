from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from math_agent.llm.base import LLMBackend, Message
from math_agent.agent.prompts import with_time_context
from math_agent.lean.mathlib_search import MathlibSearch

log = logging.getLogger("math_agent.agent.planner")


@dataclass
class FormalizationPlan:
    """A plan for formalizing a mathematical problem in Lean 4."""

    problem: str = ""
    restatement: str = ""
    goal_type: str = ""
    is_standard_result: bool = False
    recommended_theorem: str | None = None
    recommended_module: str | None = None
    recommended_imports: list[str] = field(default_factory=list)
    open_namespaces: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    instances: list[str] = field(default_factory=list)
    proof_strategy: str = ""
    notes: str = ""
    # Optional multi-lemma decomposition. Each lemma has:
    # name, statement, depends_on, proof_hint, recommended_theorem, recommended_module.
    lemmas: list[dict[str, Any]] = field(default_factory=list)
    # Optional verified code from a past successful run (used by PlanMemory).
    verified_code: str = ""
    # Stable provenance metadata populated when a plan is loaded from memory.
    memory_id: str = ""
    verification_status: str = ""

    def to_prompt_block(self, include_verified_code: bool = True) -> str:
        """Format the plan as a prompt block for the coder/repair LLM."""
        lines = ["--- Formalization plan ---"]
        if self.restatement:
            lines.append(f"Restatement: {self.restatement}")
        if self.goal_type:
            lines.append(f"Goal type: {self.goal_type}")
        if self.is_standard_result:
            lines.append("This is a standard result; prefer using an existing mathlib4 theorem.")
        if self.recommended_theorem:
            lines.append(f"Recommended theorem: {self.recommended_theorem}")
        if self.recommended_module:
            lines.append(f"Recommended module: {self.recommended_module}")
        if self.recommended_imports:
            lines.append(f"Recommended imports: {', '.join(self.recommended_imports)}")
        if self.open_namespaces:
            lines.append(f"Open namespaces: {', '.join(self.open_namespaces)}")
        if self.variables:
            lines.append(f"Variables: {', '.join(self.variables)}")
        if self.assumptions:
            lines.append(f"Assumptions: {', '.join(self.assumptions)}")
        if self.instances:
            lines.append(f"Instances: {', '.join(self.instances)}")
        if self.proof_strategy:
            lines.append(f"Proof strategy: {self.proof_strategy}")

        if self.lemmas:
            lines.append("")
            lines.append("Decompose the proof into the following lemmas (in dependency order). Prove them one at a time, in this exact order:")
            for idx, lemma in enumerate(self.lemmas, start=1):
                name = lemma.get("name") or f"lemma_{idx}"
                stmt = lemma.get("statement", "")
                formal = lemma.get("formal_statement", "")
                lines.append(f"{idx}. Lemma `{name}`: {stmt}")
                if formal:
                    lines.append(f"   Formal statement: {formal}")
                deps = lemma.get("depends_on")
                if deps:
                    lines.append(f"   Depends on: {', '.join(str(d) for d in deps)}")
                rt = lemma.get("recommended_theorem")
                if rt:
                    lines.append(f"   Recommended theorem: {rt}")
                rm = lemma.get("recommended_module")
                if rm:
                    lines.append(f"   Recommended module: {rm}")
                hint = lemma.get("proof_hint")
                if hint:
                    lines.append(f"   Proof hint: {hint}")
                sketch = lemma.get("proof_sketch")
                if sketch:
                    lines.append(f"   Proof sketch: {sketch}")

        if self.notes:
            lines.append("")
            lines.append(f"Notes: {self.notes}")
        if include_verified_code and self.verified_code:
            code = self.verified_code
            if len(code) > 2000:
                code = code[:2000] + "\n... (truncated)"
            lines.append("")
            lines.append("Verified code from a previous successful run:")
            lines.append(f"```lean\n{code}\n```")
        return "\n".join(lines)


class FormalizationPlanner:
    """Plan how to formalize a problem before asking the coder to write Lean."""

    SYSTEM = (
        "You are an expert Lean 4 formalization planner. "
        "Given a mathematical problem, output a concise JSON plan. "
        "The plan should help a downstream coder write minimal, correct Lean 4 code.\n\n"
        "Top-level fields:\n"
        "- restatement: a precise informal restatement of the goal\n"
        "- goal_type: the Lean 4 type for the final theorem statement (e.g. `Irrational (Real.sqrt 2)`)\n"
        "- is_standard_result: true if mathlib4 almost certainly has a theorem for this exact goal\n"
        "- recommended_theorem: name of the mathlib4 theorem to use, if known (e.g. `irrational_sqrt_two`)\n"
        "- recommended_module: module that contains the theorem (e.g. `Mathlib.NumberTheory.Real.Irrational`)\n"
        "- recommended_imports: list of imports needed (do NOT include `Mathlib`)\n"
        "- open_namespaces: namespaces to `open`, if any (e.g. `[\"Real\"]`)\n"
        "- variables: list of `variable` declarations for the Lean section (e.g. `[\"{H : Type*}\", \"[InnerProductSpace ℝ H]\"]`)\n"
        "- assumptions: list of named hypotheses visible inside the section (e.g. `[\"hT : IsSelfAdjoint T\"]`)\n"
        "- instances: optional list of explicit `instance` declarations (usually empty)\n"
        "- proof_strategy: one-sentence strategy\n"
        "- lemmas: optional list of helper lemmas. Each lemma has:\n"
        "    - name: a fresh Lean name\n"
        "    - statement: the Lean 4 type of the lemma, in Lean notation, and nothing\n"
        "      else -- exactly what would follow the colon in `lemma name : ...`.\n"
        "      Write `∀ B : ℕ, B < 10 → P B`, never English like `For B under 10, ...`.\n"
        "      Do not include the `lemma`/`theorem` keyword, the name, or `:= by`.\n"
        "    - depends_on: names of earlier lemmas this one uses (empty if none)\n"
        "    - proof_hint: one-sentence hint\n"
        "    - recommended_theorem: existing mathlib theorem name, if any\n"
        "    - recommended_module: module for that theorem\n"
        "- notes: anything else the coder should know\n\n"
        "Examples:\n"
        "Problem: Prove that the square root of 2 is irrational.\n"
        "Plan: {\"restatement\": \"Show that sqrt(2) is irrational\", "
        "\"goal_type\": \"Irrational (Real.sqrt 2)\", "
        "\"is_standard_result\": true, "
        "\"recommended_theorem\": \"irrational_sqrt_two\", "
        "\"recommended_module\": \"Mathlib.NumberTheory.Real.Irrational\", "
        "\"recommended_imports\": [\"Mathlib.NumberTheory.Real.Irrational\"], "
        "\"open_namespaces\": [], \"variables\": [], \"assumptions\": [], \"instances\": [], "
        "\"proof_strategy\": \"Use the existing mathlib theorem irrational_sqrt_two via exact.\", "
        "\"lemmas\": [], "
        "\"notes\": \"Name the theorem something fresh, e.g. sqrt_two_irrational, to avoid shadowing the mathlib theorem.\"}\n\n"
        "Problem: Prove that for every integer n, n^3 - n is divisible by 6.\n"
        "Plan: {\"restatement\": \"Show 6 divides n^3 - n for all integers n\", "
        "\"goal_type\": \"∀ (n : ℤ), 6 ∣ n^3 - n\", "
        "\"is_standard_result\": false, "
        "\"recommended_theorem\": \"IsCoprime.mul_dvd\", "
        "\"recommended_module\": \"Mathlib.RingTheory.Coprime.Basic\", "
        "\"recommended_imports\": [\"Mathlib.Data.Int.Basic\", \"Mathlib.Data.Int.ModEq\", \"Mathlib.RingTheory.Coprime.Basic\", \"Mathlib.Tactic.Common\"], "
        "\"open_namespaces\": [\"Int\"], \"variables\": [], \"assumptions\": [], \"instances\": [], "
        "\"proof_strategy\": \"Factor n^3-n as n(n-1)(n+1), prove 2 and 3 each divide it, then use IsCoprime.mul_dvd.\", "
        "\"lemmas\": ["
        "{\"name\": \"div_by_two\", \"statement\": \"∀ (n : ℤ), 2 ∣ n * (n - 1) * (n + 1)\", \"depends_on\": [], \"proof_hint\": \"Apply Int.emod_two_eq_zero_or_one and rcases, then exact Int.dvd_iff_emod_eq_zero.mpr and dvd_mul_of_dvd_left/right.\", \"recommended_theorem\": \"\", \"recommended_module\": \"\"}, "
        "{\"name\": \"div_by_three\", \"statement\": \"∀ (n : ℤ), 3 ∣ n * (n - 1) * (n + 1)\", \"depends_on\": [], \"proof_hint\": \"Show n % 3 = 0 ∨ 1 ∨ 2 with omega, rcases, then exact Int.dvd_iff_emod_eq_zero.mpr and dvd_mul_of_dvd_left/right.\", \"recommended_theorem\": \"\", \"recommended_module\": \"\"}, "
        "{\"name\": \"coprime_two_three\", \"statement\": \"IsCoprime (2 : ℤ) (3 : ℤ)\", \"depends_on\": [], \"proof_hint\": \"Apply the definition with witnesses 2 and -1, then ring.\", \"recommended_theorem\": \"\", \"recommended_module\": \"\"}"
        "], "
        "\"notes\": \"For the final theorem, factor n^3-n, apply div_by_two and div_by_three, then exact coprime_two_three.mul_dvd h2 h3.\"}\n\n"
        "Problem: Prove that sqrt(2) + sqrt(3) is irrational.\n"
        "Plan: {\"restatement\": \"Show that sqrt(2) + sqrt(3) is irrational\", "
        "\"goal_type\": \"Irrational (Real.sqrt 2 + Real.sqrt 3)\", "
        "\"is_standard_result\": false, "
        "\"recommended_theorem\": \"\", "
        "\"recommended_module\": \"\", "
        "\"recommended_imports\": [\"Mathlib.Tactic.NormNum.Irrational\", \"Mathlib.Data.Real.Sqrt\", \"Mathlib.Tactic.Common\"], "
        "\"open_namespaces\": [\"Real\"], \"variables\": [], \"assumptions\": [], \"instances\": [], "
        "\"proof_strategy\": \"Assume the sum is rational, square it to show sqrt(6) is rational, contradicting irrationality of sqrt(6).\", "
        "\"lemmas\": ["
        "{\"name\": \"sq_identity\", \"statement\": \"(Real.sqrt 2 + Real.sqrt 3 : ℝ) ^ 2 = 5 + 2 * Real.sqrt 6\", \"depends_on\": [], \"proof_hint\": \"Expand with ring and use Real.sq_sqrt and Real.sqrt_mul.\", \"recommended_theorem\": \"\", \"recommended_module\": \"\"}, "
        "{\"name\": \"irrational_sqrt_six\", \"statement\": \"Irrational (Real.sqrt 6)\", \"depends_on\": [], \"proof_hint\": \"Use norm_num after importing Mathlib.Tactic.NormNum.Irrational.\", \"recommended_theorem\": \"\", \"recommended_module\": \"\"}"
        "], "
        "\"notes\": \"In the main proof, intro h then rcases h with ⟨r, hr⟩ (h is membership in Rat.cast range). Derive sqrt 6 = (r^2 - 5)/2 using sq_identity, then apply Rat.not_irrational and contradict irrational_sqrt_six with exact h6 irrational_sqrt_six. Do not use the nonexistent Rational type; use ℚ or Rat.\"}\n\n"
        "Problem: Prove that for every integer n, n^7 is congruent to n modulo 42.\n"
        "Plan: {\"restatement\": \"Show n^7 ≡ n (mod 42) for all integers n\", "
        "\"goal_type\": \"∀ (n : ℤ), n ^ 7 ≡ n [ZMOD 42]\", "
        "\"is_standard_result\": false, "
        "\"recommended_theorem\": \"\", "
        "\"recommended_module\": \"\", "
        "\"recommended_imports\": [\"Mathlib.Data.Int.Basic\", \"Mathlib.Data.Int.ModEq\", \"Mathlib.Data.ZMod.Basic\", \"Mathlib.Tactic.Common\", \"Mathlib.Tactic.Ring\"], "
        "\"open_namespaces\": [\"Int\", \"ZMod\"], \"variables\": [], \"assumptions\": [], \"instances\": [], "
        "\"proof_strategy\": \"Show the identity holds for every element of the finite ring ZMod 42 by decide, then lift it to integers via Int.cast_sub and ZMod.intCast_zmod_eq_zero_iff_dvd.\", "
        "\"lemmas\": ["
        "{\"name\": \"zmod_42_pow_id\", \"statement\": \"∀ (x : ZMod 42), x ^ 7 = x\", \"depends_on\": [], \"proof_hint\": \"decide (ZMod 42 is finite).\", \"recommended_theorem\": \"\", \"recommended_module\": \"\"}"
        "], "
        "\"notes\": \"In the main proof: intro n, apply zmod_42_pow_id to (n : ZMod 42) and simpa to get ((n^7 : ℤ) : ZMod 42) = (n : ZMod 42). Then show ((n^7 - n : ℤ) : ZMod 42) = 0 by rewriting with Int.cast_sub and the previous equality, then simp. Use (ZMod.intCast_zmod_eq_zero_iff_dvd (n^7 - n) 42).mp to get 42 | n^7 - n. Finally apply Int.modEq_iff_dvd.mpr after rewriting n - n^7 = -(n^7 - n) and using dvd_neg.\"}\n\n"
        "Problem: Prove that π is irrational.\n"
        "Plan: {\"restatement\": \"Show that the real number pi is irrational\", "
        "\"goal_type\": \"Irrational Real.pi\", "
        "\"is_standard_result\": true, "
        "\"recommended_theorem\": \"irrational_pi\", "
        "\"recommended_module\": \"Mathlib.Analysis.Real.Pi.Irrational\", "
        "\"recommended_imports\": [\"Mathlib.Analysis.Real.Pi.Irrational\"], "
        "\"open_namespaces\": [], \"variables\": [], \"assumptions\": [], \"instances\": [], "
        "\"proof_strategy\": \"Use the existing mathlib theorem irrational_pi via exact.\", "
        "\"lemmas\": [], "
        "\"notes\": \"Use Real.pi for pi; do not invent Rational.\"}\n\n"
        "Output ONLY the JSON plan, no markdown, no commentary. Use as many lemmas as needed to make each step formalizable; prefer 5-10 small lemmas for hard proofs over 1-2 giant ones. "
        "When 'Verified code from a previous successful run' is present in a memory example, "
        "preserve its structure, theorem names, imports, and cast handling as closely as possible."
    )

    def __init__(
        self,
        llm: LLMBackend,
        mathlib_search: MathlibSearch | None = None,
    ):
        self.llm = llm
        self.search = mathlib_search

    @staticmethod
    def _format_memory_examples(examples: list[FormalizationPlan]) -> str:
        blocks: list[str] = []
        for idx, plan in enumerate(examples, start=1):
            blocks.append(f"Example {idx}:\n{plan.to_prompt_block()}")
        return "\n\n".join(blocks)

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any] | None:
        """Parse JSON while tolerating markdown fences and nested braces."""
        text = raw.strip()
        fence_match = re.search(
            r"```(?:json)?\s*\n(.*?)\n?```",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if fence_match:
            text = fence_match.group(1).strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except Exception:
            pass
        # Extract the outermost balanced object.
        start = text.find("{")
        if start == -1:
            return None
        depth = 0
        in_string = False
        escape = False
        for i, ch in enumerate(text[start:], start=start):
            if in_string:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : i + 1])
                    except Exception:
                        return None
        return None

    async def plan(
        self,
        problem: str,
        informal_proof: str = "",
        *,
        memory_examples: list[FormalizationPlan] | None = None,
        session_log: logging.Logger | None = None,
    ) -> FormalizationPlan:
        prompt_parts = [f"Problem: {problem}\n"]
        if informal_proof:
            prompt_parts.append(f"\nInformal proof:\n{informal_proof}\n")
        if memory_examples:
            prompt_parts.append("\nRelevant past successful plans:\n")
            prompt_parts.append(self._format_memory_examples(memory_examples))
        prompt_parts.append("\nPlan:")
        prompt = "".join(prompt_parts)

        response = await self.llm.complete(
            [Message(role="user", content=prompt)],
            system=self.SYSTEM,
            temperature=0.0,
        )
        raw = response.text.strip()
        data = self._extract_json(raw)
        if data is None:
            # One repair attempt: ask the model to emit only valid JSON.
            repair_prompt = (
                "The previous response was not valid JSON. "
                "Output ONLY a valid JSON object matching the requested schema, "
                "with no markdown fences and no commentary.\n\n"
                f"Previous response:\n{raw}\n\n"
                "Now output valid JSON:"
            )
            try:
                repaired = await self.llm.complete(
                    [Message(role="user", content=repair_prompt)],
                    system="You output only valid JSON.",
                    temperature=0.0,
                )
                data = self._extract_json(repaired.text)
            except Exception as repair_exc:
                if session_log:
                    session_log.debug(
                        "FormalizationPlanner JSON repair failed: %s", repair_exc
                    )
        if data is None:
            log.warning("FormalizationPlanner JSON parse failed; raw response: %s", raw[:2000])
            if session_log:
                session_log.warning("FormalizationPlanner JSON parse failed: %s", raw[:2000])
            return FormalizationPlan(notes="Planner failed to produce valid JSON.")

        plan = self._formalization_from_data(data)

        if self.search is not None:
            plan = self._validate(plan)

        return plan

    @staticmethod
    def _formalization_from_data(data: dict[str, Any]) -> FormalizationPlan:
        """Build a FormalizationPlan from a parsed JSON object."""
        return FormalizationPlan(
            restatement=data.get("restatement", ""),
            goal_type=data.get("goal_type", ""),
            is_standard_result=bool(data.get("is_standard_result", False)),
            recommended_theorem=data.get("recommended_theorem") or None,
            recommended_module=data.get("recommended_module") or None,
            recommended_imports=list(data.get("recommended_imports", [])),
            open_namespaces=list(data.get("open_namespaces", [])),
            variables=list(data.get("variables", [])),
            assumptions=list(data.get("assumptions", [])),
            instances=list(data.get("instances", [])),
            proof_strategy=data.get("proof_strategy", ""),
            notes=data.get("notes", ""),
            lemmas=list(data.get("lemmas", [])),
        )

    def _validate(self, plan: FormalizationPlan) -> FormalizationPlan:
        """Cross-check recommended theorems and imports against the local mathlib."""
        plan = self._validate_imports(plan)

        # Validate top-level theorem.
        plan = self._validate_theorem(plan, plan.recommended_theorem, plan.recommended_module)

        # Validate lemma-level theorems.
        for lemma in plan.lemmas:
            rt = lemma.get("recommended_theorem")
            rm = lemma.get("recommended_module")
            if rt:
                validated = self._validate_theorem(
                    FormalizationPlan(recommended_theorem=rt, recommended_module=rm),
                    rt,
                    rm,
                )
                if validated.recommended_module and validated.recommended_module != rm:
                    lemma["recommended_module"] = validated.recommended_module
                if validated.recommended_theorem is None:
                    lemma["recommended_theorem"] = None

        return plan

    def _validate_imports(self, plan: FormalizationPlan) -> FormalizationPlan:
        """Remove recommended imports whose source files do not exist locally."""
        if self.search is None:
            return plan

        kept: list[str] = []
        dropped: list[str] = []
        for imp in plan.recommended_imports:
            rel = Path(*imp.split(".")).with_suffix(".lean")
            if imp.startswith("Mathlib.") and not (self.search.root / rel).exists():
                dropped.append(imp)
            else:
                kept.append(imp)

        if dropped:
            plan.recommended_imports = kept
            note = f"Removed non-existent import(s): {', '.join(dropped)}. Use search tactics if needed."
            if plan.notes:
                plan.notes += " " + note
            else:
                plan.notes = note

        # Also validate lemma-level imports.
        for lemma in plan.lemmas:
            rm = lemma.get("recommended_module")
            if rm:
                rel = Path(*rm.split(".")).with_suffix(".lean")
                if rm.startswith("Mathlib.") and not (self.search.root / rel).exists():
                    lemma["recommended_module"] = None
                    lemma["recommended_theorem"] = None

        plan = self._add_missing_default_imports(plan)
        return plan

    def _add_missing_default_imports(self, plan: FormalizationPlan) -> FormalizationPlan:
        """Add common imports that the downstream coder is likely to need."""
        text = " ".join(
            [plan.goal_type]
            + [lemma.get("statement", "") for lemma in plan.lemmas]
        )

        def has_import(plan: FormalizationPlan, suffix: str) -> bool:
            return any(imp.endswith(suffix) for imp in plan.recommended_imports)

        if "Irrational" in text and not has_import(plan, "Irrational"):
            plan.recommended_imports.append("Mathlib.Data.Real.Irrational")

        if "Real.sqrt" in text:
            if not has_import(plan, "Sqrt"):
                plan.recommended_imports.append("Mathlib.Data.Real.Sqrt")
            # norm_num can prove Irrational (Real.sqrt n) when n is not a square.
            if "Irrational (Real.sqrt" in text and not has_import(
                plan, "NormNum.Irrational"
            ):
                plan.recommended_imports.append("Mathlib.Tactic.NormNum.Irrational")

        if "ZMod" in text and not has_import(plan, "ZMod"):
            plan.recommended_imports.append("Mathlib.Data.ZMod.Basic")

        if "[ZMOD" in text and not has_import(plan, "ModEq"):
            plan.recommended_imports.append("Mathlib.Data.Int.ModEq")

        return plan

    def _validate_theorem(
        self,
        plan: FormalizationPlan,
        theorem: str | None,
        module: str | None,
    ) -> FormalizationPlan:
        if not theorem:
            return plan
        try:
            entries = self.search.search_by_name(theorem, max_results=3)
        except Exception:
            return plan

        matching = [e for e in entries if e.get("name") == theorem]
        if matching:
            entry = matching[0]
            found_module = entry.get("module")
            if found_module and found_module.startswith("Mathlib."):
                plan.recommended_module = found_module
        else:
            # The theorem name guessed by the LLM does not exist exactly.
            plan.recommended_theorem = None
            if plan.notes:
                plan.notes += " "
            plan.notes += (
                f"The suggested theorem `{theorem}` was not found in the local mathlib4 checkout; "
                "do not guess it. Use search tactics instead."
            )

        return plan


@dataclass
class UnifiedPlan:
    """One planning artifact serving both the informal solve loop and Lean.

    ``plan_text`` is the rendered informal plan (STRATEGY/STEPS/WATCH-OUTS/
    DONE-WHEN) injected into the reasoning context; ``formalization`` is the
    Lean 4 sketch consumed by the formalization path.
    """

    plan_text: str = ""
    formalization: FormalizationPlan = field(default_factory=FormalizationPlan)


def _render_informal_plan(data: Any) -> str:
    """Render the informal section as the classic 4-block plain-text plan."""
    if isinstance(data, str):
        return data.strip()
    if not isinstance(data, dict):
        return ""

    def _lines(value: Any) -> list[str]:
        if isinstance(value, str):
            text = value.strip()
            return [text] if text else []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        return []

    parts: list[str] = []
    strategy = str(data.get("strategy") or "").strip()
    if strategy:
        parts.append(f"STRATEGY: {strategy}")
    steps = _lines(data.get("steps"))
    if steps:
        numbered = "\n".join(f"{idx}. {step}" for idx, step in enumerate(steps, 1))
        parts.append(f"STEPS:\n{numbered}")
    watch_outs = _lines(data.get("watch_outs"))
    if watch_outs:
        parts.append("WATCH-OUTS:\n" + "\n".join(f"- {item}" for item in watch_outs))
    done_when = str(data.get("done_when") or "").strip()
    if done_when:
        parts.append(f"DONE-WHEN: {done_when}")
    return "\n".join(parts)


class UnifiedPlanner:
    """Single-call planner producing both the informal plan and the Lean sketch.

    Replaces the former separate informal plain-text plan call and the
    standalone ``FormalizationPlanner`` call with one JSON response. Failures
    degrade to ``None`` so callers can silently continue without a plan.
    """

    SYSTEM = (
        "You are the planning assistant of a mathematical reasoning agent. "
        "Given a problem, write a concise solution plan. Do NOT solve the problem.\n\n"
        "Output ONE JSON object with exactly two top-level keys:\n"
        '- "informal": an object with keys "strategy" (one sentence overall approach), '
        '"steps" (3-7 ordered key steps: lemmas to establish, computations or tool uses '
        'worth doing), "watch_outs" (pitfalls, hidden assumptions, edge cases that must '
        'be justified), "done_when" (the exact form the final answer must take).\n'
        '- "formalization": a Lean 4 formalization plan object following the schema and '
        "examples below. Draft it even when formal verification was not requested; it is "
        "only a sketch for a downstream coder.\n\n"
        "Match the problem's language (Chinese problem -> Chinese informal plan). "
        "Keep the informal part under 200 words.\n\n"
        "Formalization schema and examples:\n"
        + FormalizationPlanner.SYSTEM
        + "\n\nRemember: wrap everything in the two-key object described above "
        '("informal" + "formalization"). Output ONLY the JSON object.'
    )

    def __init__(
        self,
        llm: LLMBackend,
        mathlib_search: MathlibSearch | None = None,
    ):
        self.llm = llm
        # Reuse the formalization machinery (JSON extraction, mathlib
        # validation) so repair/validation semantics stay identical.
        self._formal = FormalizationPlanner(llm, mathlib_search)

    async def plan(
        self,
        problem: str,
        *,
        session_log: logging.Logger | None = None,
    ) -> UnifiedPlan | None:
        prompt = f"Problem: {problem}\n\nPlan:"
        response = await self.llm.complete(
            [Message(role="user", content=prompt)],
            system=with_time_context(self.SYSTEM),
            temperature=0.0,
        )
        raw = response.text.strip()
        data = FormalizationPlanner._extract_json(raw)
        if data is None:
            # One repair attempt: ask the model to emit only valid JSON.
            repair_prompt = (
                "The previous response was not valid JSON. "
                "Output ONLY a valid JSON object matching the requested schema, "
                "with no markdown fences and no commentary.\n\n"
                f"Previous response:\n{raw}\n\n"
                "Now output valid JSON:"
            )
            try:
                repaired = await self.llm.complete(
                    [Message(role="user", content=repair_prompt)],
                    system="You output only valid JSON.",
                    temperature=0.0,
                )
                data = FormalizationPlanner._extract_json(repaired.text)
            except Exception as repair_exc:
                if session_log:
                    session_log.debug(
                        "UnifiedPlanner JSON repair failed: %s", repair_exc
                    )
        if not isinstance(data, dict):
            log.warning("UnifiedPlanner JSON parse failed; raw response: %s", raw[:2000])
            if session_log:
                session_log.warning("UnifiedPlanner JSON parse failed: %s", raw[:2000])
            return None

        plan_text = _render_informal_plan(data.get("informal"))
        formal_data = data.get("formalization")
        formalization = FormalizationPlanner._formalization_from_data(
            formal_data if isinstance(formal_data, dict) else {}
        )
        if self._formal.search is not None:
            formalization = self._formal._validate(formalization)

        if not plan_text and not formalization.restatement and not formalization.goal_type:
            log.warning("UnifiedPlanner produced an empty plan")
            return None
        return UnifiedPlan(plan_text=plan_text, formalization=formalization)
