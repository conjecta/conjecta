from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from math_agent.agent.planner import FormalizationPlan
from math_agent.config import LeanConfig
from math_agent.lean.codegen import _is_repairable
from math_agent.lean.context_builder import LeanContextBuilder
from math_agent.lean.mathlib_search import default_search
from math_agent.lean.runner import LeanRunner
from math_agent.llm.base import LLMBackend, Message
from math_agent.text_utils import extract_lean_code

log = logging.getLogger("math_agent.lean.lemma_executor")

# A planner-supplied lemma statement is meant to be the proposition alone, so
# callers can wrap it as `lemma <name> : <statement> := by ...`. Models often
# supply a whole declaration instead ("lemma foo : P", "theorem foo : P := by"),
# and wrapping that a second time produces Lean that fails to parse -- e.g.
# "unexpected token 'lemma'; expected term". Strip any declaration header (and a
# trailing proof-assignment) so the wrap sites receive just the proposition.
_DECL_KEYWORD_RE = re.compile(
    r"^\s*(?:private\s+|protected\s+|nonrec\s+|@\[[^\]]*\]\s*)*"
    r"(?:lemma|theorem|example)\b",
)
_PROOF_ASSIGN_RE = re.compile(r":=\s*(?:by\b.*)?$", re.DOTALL)


def _split_after_binders(text: str) -> str | None:
    """Return what follows the top-level ``:`` of a declaration header.

    Binders may themselves contain colons -- ``lemma foo (n : Nat) : P n`` --
    so scan for a colon at bracket depth zero rather than the first colon.
    """
    depth = 0
    for index, char in enumerate(text):
        if char in "([{⟨":
            depth += 1
        elif char in ")]}⟩":
            depth -= 1
        elif char == ":" and depth == 0:
            # `:=` at depth zero is a proof assignment, not the type ascription.
            if text[index : index + 2] == ":=":
                return None
            return text[index + 1 :]
    return None


def sanitize_lemma_statement(statement: str) -> str:
    """Reduce a planner-supplied lemma statement to the bare proposition.

    Idempotent: a statement that is already bare is returned unchanged apart
    from surrounding whitespace.
    """
    text = (statement or "").strip()
    if not text:
        return text
    if _DECL_KEYWORD_RE.match(text):
        after = _split_after_binders(text)
        # Only accept the strip if it left something behind, so a malformed
        # header cannot gut the statement entirely.
        if after and after.strip():
            text = after.strip()
    return _PROOF_ASSIGN_RE.sub("", text).strip()


def _lemma_levels(lemmas: list[dict]) -> list[list[tuple[int, dict]]]:
    """Group lemmas into dependency levels for bounded-parallel execution.

    Each level contains (1-based index, lemma) pairs whose depends_on entries
    all resolve to lemmas in earlier levels. Unknown dependency names or
    cycles fall back to one lemma per level in plan order — the original
    strictly-sequential behavior.
    """
    names: dict[str, int] = {}
    for position, lemma in enumerate(lemmas):
        name = str(lemma.get("name") or f"lemma_{position + 1}")
        names[name] = position
    depth: dict[int, int] = {}
    for position, lemma in enumerate(lemmas):
        deps = [str(dep) for dep in (lemma.get("depends_on") or [])]
        level = 0
        for dep in deps:
            if dep not in names:
                # Unknown dependency: keep the safe sequential order.
                return [[(idx + 1, item)] for idx, item in enumerate(lemmas)]
            dep_pos = names[dep]
            if dep_pos >= position:
                # Forward/self dependency (or cycle): not schedulable in
                # levels; keep sequential order.
                return [[(idx + 1, item)] for idx, item in enumerate(lemmas)]
            level = max(level, depth[dep_pos] + 1)
        depth[position] = level
    levels: list[list[tuple[int, dict]]] = []
    for position, lemma in enumerate(lemmas):
        while len(levels) <= depth[position]:
            levels.append([])
        levels[depth[position]].append((position + 1, lemma))
    return levels


class LemmaDAGExecutor:
    """Execute a multi-lemma formalization plan lemma-by-lemma.

    This turns a one-shot "write the whole file" task into a sequence of
    smaller "write the next lemma" tasks, each verified independently before
    moving on. It is the first step toward the full lemma-DAG vision.
    """

    def __init__(
        self,
        llm: LLMBackend,
        runner: LeanRunner,
        plan: FormalizationPlan,
        problem: str,
        informal_proof: str = "",
        *,
        max_repair_attempts: int | None = None,
        session_log: logging.Logger | None = None,
        progress_callback: Any = None,
        wall_seconds: float | None = None,
        search_hook: Any = None,
        max_parallel: int = 1,
        rescue_enabled: bool = False,
        route_count: int = 1,
        rescue_max_depth: int | None = None,
        route_temperatures: list[float] | None = None,
        difficulty_threshold: int | None = None,
        max_routes_hard: int | None = None,
    ):
        self.llm = llm
        self.runner = runner
        self.plan = plan
        self.problem = problem
        self.informal_proof = informal_proof
        # Defaults come from LeanConfig so code and config cannot drift.
        config_defaults = LeanConfig()
        self.max_repair_attempts = (
            max_repair_attempts
            if max_repair_attempts is not None
            else config_defaults.max_repair_attempts
        )
        self.session_log = session_log
        # Optional async callback (message: str) for user-facing progress.
        self.progress_callback = progress_callback
        # Overall wall budget for the whole decomposition run; None disables it.
        self.wall_seconds = wall_seconds
        # Optional async hook (name, statement) -> proof body | None. Typically
        # backed by REPL tactic search with a dedicated prover model: cheap
        # mechanical lemmas never spend an LLM codegen round.
        self.search_hook = search_hook
        # Lemmas in the same dependency level are proved concurrently, bounded
        # by this many workers (Lean checks stay capped by the runner's own
        # semaphore). 1 preserves the original strictly-sequential behavior.
        self.max_parallel = max(1, int(max_parallel))
        # Recursive rescue: a lemma that exhausts its repair budget gets one
        # sub-decomposition round (Hilbert-style recursive subgoaling) before
        # the whole run aborts.
        self.rescue_enabled = bool(rescue_enabled)
        # Multi-route: sample this many independent proof bodies per attempt
        # (temperature-diversified), verify them concurrently, and accept the
        # first that type-checks. 1 keeps single-route behavior.
        self.route_count = max(1, int(route_count))
        # Hard cap on rescue recursion. The model decides *whether* a failed
        # sub-lemma gets decomposed again; this decides how deep it can go.
        self.rescue_max_depth = max(
            0,
            int(
                rescue_max_depth
                if rescue_max_depth is not None
                else config_defaults.lemma_rescue_max_depth
            ),
        )
        # Temperature ladder for multi-route sampling; extended by repeating
        # the last entry when more routes than entries are requested.
        self.route_temperatures = [
            float(t)
            for t in (
                route_temperatures
                if route_temperatures is not None
                else config_defaults.lemma_route_temperatures
            )
        ] or [0.0]
        # Difficulty routing: lemmas rated at or above the threshold get up to
        # max_routes_hard temperature-diversified routes.
        self.difficulty_threshold = int(
            difficulty_threshold
            if difficulty_threshold is not None
            else config_defaults.lemma_difficulty_threshold
        )
        self.max_routes_hard = max(
            1,
            int(
                max_routes_hard
                if max_routes_hard is not None
                else config_defaults.lemma_max_routes_hard
            ),
        )

        self.imports: set[str] = set(plan.recommended_imports) | {"Mathlib.Tactic.Common"}
        self.open_namespaces: list[str] = list(plan.open_namespaces)
        self.verified_lemmas: list[str] = []
        # Diagnostic for the most recent abort: which declaration failed, the
        # Lean failure_kind, and a short error summary. Surfaced by callers so
        # the agent can reroute using the already-verified lemmas.
        self.last_failure: dict[str, Any] | None = None

        self.context_builder = LeanContextBuilder()

    def _file_header(self) -> str:
        lines: list[str] = []
        for imp in sorted(self.imports):
            lines.append(f"import {imp}")
        for ns in self.open_namespaces:
            lines.append(f"open {ns}")
        return "\n".join(lines)

    def _build_code(self, extra: str = "", verified: list[str] | None = None) -> str:
        header = self._file_header()
        parts = list(self.verified_lemmas if verified is None else verified)
        if extra.strip():
            parts.append(extra)
        body = "\n\n".join(parts)
        wrapped = self.context_builder.build(self.plan, body)
        if header:
            return header + "\n\n" + wrapped
        return wrapped

    def _strip_header(self, code: str, keyword: str) -> str:
        """If the model repeated the lemma/theorem header, keep only the proof body.

        See also :func:`sanitize_lemma_statement`, which does the mirror-image
        cleanup for the *statement* side.
        """
        code = code.strip()
        # Match header optionally prefixed by `lemma`/`theorem` and whitespace.
        pattern = rf"^\s*(?:{keyword}|theorem)\s+[A-Za-z_][A-Za-z0-9_']*\s*:.*?:=\s*by\s*"
        match = re.match(pattern, code, re.DOTALL)
        if match:
            return code[match.end():].strip()
        return code

    def _clean_proof_body(self, body: str) -> str:
        """Remove common hallucinated words that are not valid tactics."""
        # "uses" is never a valid Lean tactic and frequently leaks from proof hints.
        body = re.sub(r"\buses\b", "", body)
        return body

    def _indent(self, body: str) -> str:
        """Ensure the tactic body is indented relative to `by`."""
        lines = body.splitlines()
        out: list[str] = []
        for ln in lines:
            if ln.strip() == "":
                out.append("")
            elif ln.startswith("  "):
                out.append(ln)
            else:
                out.append("  " + ln)
        return "\n".join(out)

    def _candidates_block(self, diagnostic: str) -> str:
        """Retrieve relevant mathlib4 declarations from Lean error messages."""
        try:
            return default_search().format_candidates_for_prompt(
                diagnostic.splitlines(), max_total=8
            )
        except Exception as exc:
            log.debug("Failed to retrieve mathlib candidates: %s", exc)
            return ""

    def _extract_theorem_body(self, code: str) -> str | None:
        """Extract the proof body of the first top-level theorem in ``code``.

        Returns the tactics/proof term (without the theorem header) if a
        well-formed ```:= by``` block can be found, otherwise None.
        """
        # Drop the header of the first theorem and capture everything up to the
        # next top-level declaration (import / open / lemma / theorem).
        header_match = re.search(
            r"^\s*theorem\s+\S+\s*:.*?:=\s*by\s*\n",
            code,
            re.MULTILINE | re.DOTALL,
        )
        if not header_match:
            return None
        body = code[header_match.end():]
        lines = body.splitlines(keepends=True)
        out: list[str] = []
        for ln in lines:
            if re.match(r"^(import|open|lemma|theorem)\b", ln.strip()):
                break
            out.append(ln)
        result = "".join(out).rstrip()
        if not result:
            return None
        return result

    def _body_from_search_code(self, code: str, keyword: str = "lemma") -> str | None:
        """Extract the tactic body from a full proof-search result.

        ProofSearch returns whole files (imports + header + tactics); the
        executor only needs the body, re-verified in its own context anyway.
        """
        lines = [
            ln
            for ln in code.splitlines()
            if not re.match(r"^\s*(import|open)\b", ln)
        ]
        body = self._strip_header("\n".join(lines).strip(), keyword)
        return body or None

    async def _estimate_route_count(self, name: str, statement: str) -> int:
        """Estimate lemma difficulty and scale proof routes accordingly.

        Hard lemmas (at or above ``self.difficulty_threshold`` on the model's
        1-5 scale) get up to ``self.max_routes_hard`` temperature-diversified
        routes; easier ones keep the configured route_count.
        Any classifier failure falls back to route_count."""
        prompt = (
            f"Lemma:\n  lemma {name} : {statement} := by\n\n"
            "Rate how hard this Lean 4 lemma is to prove on a 1-5 scale "
            "(1 = closes with one tactic, 5 = needs a novel construction). "
            "Reply with a single integer."
        )
        try:
            response = await self.llm.complete(
                [Message(role="user", content=prompt)],
                system=(
                    "You are an expert Lean 4 proof engineer. "
                    "Reply with a single integer only."
                ),
                temperature=0.0,
            )
            difficulty = int((response.text or "").strip()[:1])
        except Exception:
            return self.route_count
        if difficulty >= self.difficulty_threshold:
            return max(self.route_count, self.max_routes_hard)
        return self.route_count

    async def _try_search_hook(self, name: str, statement: str) -> str | None:
        """Ask the prover/search hook for a proof body; None on any failure."""
        if self.search_hook is None:
            return None
        try:
            code = await self.search_hook(name, statement)
        except Exception:
            log.debug("lemma search hook raised", exc_info=True)
            return None
        if not code:
            return None
        body = self._body_from_search_code(str(code), "lemma")
        if body and self.session_log:
            self.session_log.info("Lemma %s solved by search hook", name)
        return body

    async def _sample_bodies(
        self, system: str, prompt: str, count: int
    ) -> list[str]:
        """Generate up to ``count`` diverse proof bodies concurrently."""
        count = max(1, count)
        temperatures = list(self.route_temperatures)
        while len(temperatures) < count:
            temperatures.append(temperatures[-1])
        raw_bodies = await asyncio.gather(
            *(
                self._generate(system, prompt, temperature=temperatures[i])
                for i in range(count)
            )
        )
        bodies: list[str] = []
        for raw in raw_bodies:
            body = self._indent(self._clean_proof_body(self._strip_header(raw, "lemma")))
            if body and body not in bodies:
                bodies.append(body)
        if not bodies:
            bodies.append("  sorry")
        return bodies

    async def _rescue_lemma(
        self,
        idx: int,
        lemma: dict,
        system: str,
        total: int,
        base_verified: list[str],
        depth: int = 1,
    ) -> tuple[str | None, list[str]]:
        """Recursive rescue: decompose a failed lemma into sub-lemmas, prove
        them, then retry the parent with the sub-lemmas in context.

        Does not mutate ``self.verified_lemmas`` so sibling rescues can run
        concurrently; returns ``(parent_code, sub_lemma_codes)`` for the
        caller to merge. Sub-lemmas that verified before a failure are still
        returned so partial progress is kept. A failed sub-lemma may itself
        be rescued one level deeper when the model judges it decomposable;
        the hard recursion cap is ``self.rescue_max_depth``."""
        name = lemma.get("name") or f"lemma_{idx}"
        statement = sanitize_lemma_statement(
            str(lemma.get("formal_statement") or lemma.get("statement") or "")
        )
        if not statement:
            return None, []
        await self._report(f"引理 `{name}` 直接证明失败，尝试递归分解为子引理…")
        sub_lemmas = await self._decompose_lemma(name, statement)
        if not sub_lemmas:
            return None, []
        sub_codes: list[str] = []
        for sub_idx, sub in enumerate(sub_lemmas, start=1):
            code = await self._prove_lemma(
                sub_idx,
                sub,
                system,
                self._build_code(verified=[*base_verified, *sub_codes]),
                [*base_verified, *sub_codes],
                len(sub_lemmas),
            )
            if code is None and depth < self.rescue_max_depth:
                if await self._should_deepen_rescue(sub):
                    await self._report(
                        f"子引理仍未闭合，尝试再分解一层（第 {depth + 1} 层）…"
                    )
                    code, deeper_subs = await self._rescue_lemma(
                        sub_idx,
                        sub,
                        system,
                        len(sub_lemmas),
                        [*base_verified, *sub_codes],
                        depth + 1,
                    )
                    sub_codes.extend(deeper_subs)
            if code is None:
                if self.session_log:
                    self.session_log.warning(
                        "Rescue decomposition of %s failed at sub-lemma %d", name, sub_idx
                    )
                return None, sub_codes
            sub_codes.append(code)
        await self._report(
            f"子引理全部验证通过，重试引理 `{name}`…"
        )
        parent_code = await self._prove_lemma(
            idx,
            lemma,
            system,
            self._build_code(verified=[*base_verified, *sub_codes]),
            [*base_verified, *sub_codes],
            total,
        )
        return parent_code, sub_codes

    async def _should_deepen_rescue(self, lemma: dict) -> bool:
        """Ask the model whether a failed sub-lemma is worth one more
        decomposition round. Defaults to False on any failure; the hard
        recursion cap lives in code (``self.rescue_max_depth``), not in the model."""
        name = str(lemma.get("name") or "sublemma")
        statement = sanitize_lemma_statement(
            str(lemma.get("formal_statement") or lemma.get("statement") or "")
        )
        if not statement:
            return False
        prompt = (
            f"The Lean 4 lemma\n  lemma {name} : {statement} := by\n"
            "resists direct proof and one decomposition round. Can it "
            "plausibly be decomposed into strictly simpler lemmas? "
            "Answer YES or NO."
        )
        try:
            response = await self.llm.complete(
                [Message(role="user", content=prompt)],
                system="You are an expert Lean 4 proof engineer. Answer YES or NO only.",
                temperature=0.0,
            )
        except Exception:
            return False
        return (response.text or "").strip().upper().startswith("YES")

    async def _decompose_lemma(self, name: str, statement: str) -> list[dict]:
        """Ask the LLM for a sub-lemma decomposition of a failed lemma."""
        prompt = (
            f"Problem:\n{self.problem}\n\n"
            f"The Lean 4 lemma\n  lemma {name} : {statement} := by\n"
            "could not be proved directly. Decompose it into at most 4 smaller, "
            "strictly easier Lean 4 lemmas whose conjunction makes the parent "
            "proof a one-liner. Output ONLY a JSON array, each item "
            '{"statement": "<Lean 4 proposition>", "proof_hint": "<one line>"}.'
        )
        try:
            response = await self.llm.complete(
                [Message(role="user", content=prompt)],
                system=(
                    "You are an expert Lean 4 proof engineer. Output only valid "
                    "JSON; propositions must be Lean 4 syntax without tactics."
                ),
                temperature=0.3,
            )
        except Exception:
            log.debug("lemma decomposition call failed", exc_info=True)
            return []
        match = re.search(r"\[.*\]", response.text or "", re.DOTALL)
        if not match:
            return []
        try:
            items = json.loads(match.group(0))
        except json.JSONDecodeError:
            return []
        sub_lemmas: list[dict] = []
        for position, item in enumerate(items if isinstance(items, list) else [], start=1):
            if not isinstance(item, dict):
                continue
            sub_statement = sanitize_lemma_statement(str(item.get("statement") or ""))
            if not sub_statement:
                continue
            sub_lemmas.append(
                {
                    "name": f"{name}_sub{position}",
                    "statement": sub_statement,
                    "proof_hint": str(item.get("proof_hint") or ""),
                }
            )
            if len(sub_lemmas) >= 4:
                break
        return sub_lemmas

    async def _prove_lemma(
        self,
        idx: int,
        lemma: dict,
        system: str,
        context_code: str,
        verified_snapshot: list[str],
        total: int,
    ) -> str | None:
        """Prove one lemma against a fixed verified context; None on failure."""
        name = lemma.get("name") or f"lemma_{idx}"
        # Prefer the planner's formal (Lean) statement; fall back to the
        # informal one. Fail the run when no usable statement exists at all.
        statement = sanitize_lemma_statement(
            str(lemma.get("formal_statement") or lemma.get("statement") or "")
        )
        if not statement:
            log.warning("LemmaDAGExecutor: lemma %s has an empty statement; aborting.", name)
            self.last_failure = {
                "lemma": name,
                "failure_kind": "empty_statement",
                "detail": "planner produced an empty lemma statement",
            }
            return None
        hint = lemma.get("proof_hint", "")
        deps = lemma.get("depends_on") or []

        prompt = (
            f"Problem:\n{self.problem}\n\n"
            f"Informal proof:\n{self.informal_proof}\n\n"
            f"{self.plan.to_prompt_block(include_verified_code=False)}\n\n"
            f"Previously verified code:\n\n```lean\n{context_code}\n```\n\n"
            f"Now write the proof body for the next lemma:\n"
            f"lemma {name} : {statement} := by\n\n"
        )
        if hint:
            prompt += f"Hint: {hint}\n"
        if deps:
            prompt += f"You may use the following earlier lemmas: {', '.join(str(d) for d in deps)}.\n"
        prompt += (
            "Output only the indented tactics/proof term. "
            "Do not repeat the lemma header."
        )

        hook_body, route_count = await asyncio.gather(
            self._try_search_hook(name, statement),
            self._estimate_route_count(name, statement),
        )
        if hook_body is not None:
            await self._report(
                f"引理 {idx}/{total} `{name}` 由证明搜索直接闭合"
            )
            bodies = [self._indent(hook_body)]
        else:
            bodies = await self._sample_bodies(system, prompt, route_count)

        success, result, lemma_code = await self._verify_routes(
            name, statement, bodies, verified_snapshot, f"({idx}/{total})"
        )

        # Repair loop for this lemma.
        for attempt in range(self.max_repair_attempts):
            if success:
                break
            if not _is_repairable(result.failure_kind):
                # Infra/timeout/unsafe failures cannot be fixed by
                # re-prompting; stop instead of burning repair budget.
                if self.session_log:
                    self.session_log.warning(
                        "Lemma %s failed with non-repairable failure_kind=%s; "
                        "skipping LLM repair",
                        name,
                        result.failure_kind,
                    )
                break
            if self.session_log:
                self.session_log.info("Repairing lemma %s attempt %d", name, attempt + 1)
            await self._report(
                f"引理 {idx}/{total} `{name}` 未通过，"
                f"正在修复（第 {attempt + 1}/{self.max_repair_attempts} 次）…"
            )
            diagnostic = "\n".join(result.errors)
            candidates = await asyncio.to_thread(self._candidates_block, diagnostic)
            repair_prompt = (
                f"Problem:\n{self.problem}\n\n"
                f"Informal proof:\n{self.informal_proof}\n\n"
                f"{self.plan.to_prompt_block(include_verified_code=False)}\n\n"
                f"Previously verified code:\n\n```lean\n{context_code}\n```\n\n"
                f"The following lemma failed to type-check:\n\n```lean\n{lemma_code}\n```\n\n"
                f"Diagnostics:\n{diagnostic}\n\n"
            )
            if candidates:
                repair_prompt += candidates + "\n\n"
            repair_prompt += (
                "Fix the proof body. Output only the corrected indented tactics/proof term. "
                "If relevant declarations are listed above, prefer using their real mathlib4 names."
            )
            proof_bodies = await asyncio.gather(
                self._generate(system, repair_prompt),
                self._generate(system, repair_prompt, temperature=0.7),
            )
            repair_bodies: list[str] = []
            for raw in proof_bodies:
                body = self._indent(self._strip_header(raw, "lemma"))
                if body and body not in repair_bodies:
                    repair_bodies.append(body)
            if repair_bodies:
                success, result, lemma_code = await self._verify_routes(
                    name,
                    statement,
                    repair_bodies,
                    verified_snapshot,
                    f"({idx}/{total}) repair {attempt + 1}",
                )

        if not success:
            self.last_failure = {
                "lemma": name,
                "failure_kind": result.failure_kind,
                "detail": (result.errors or ["unknown Lean error"])[0][:400],
            }
            if self.session_log:
                self.session_log.warning("LemmaExecutor failed on lemma %s", name)
            await self._report(
                f"引理 `{name}` 修复 {self.max_repair_attempts} 次后仍未通过，分解证明失败"
            )
            return None

        if self.session_log:
            self.session_log.info("Lemma %s verified", name)
        await self._report(f"引理 {idx}/{total} `{name}` 验证通过")
        return lemma_code

    async def _generate(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float = 0.0,
    ) -> str:
        response = await self.llm.complete(
            [Message(role="user", content=prompt)],
            system=system,
            temperature=temperature,
        )
        return extract_lean_code(response.text)

    async def _verify_routes(
        self,
        name: str,
        statement: str,
        bodies: list[str],
        verified_snapshot: list[str],
        label: str,
    ) -> tuple[bool, Any, str]:
        """Verify candidate bodies concurrently; the first success wins and
        the remaining checks are cancelled. On total failure returns the last
        completed failure so the repair loop still has diagnostics."""
        if not bodies:
            bodies = ["  sorry"]

        async def check(body: str) -> tuple[bool, Any, str]:
            candidate = f"lemma {name} : {statement} := by\n{body}"
            success, result = await self._verify(
                self._build_code(candidate, verified=verified_snapshot),
                f"lemma {name} {label}",
            )
            return success, result, candidate

        tasks = [asyncio.ensure_future(check(body)) for body in bodies]
        last_failure: tuple[Any, str] | None = None
        try:
            pending = set(tasks)
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    success, result, candidate = task.result()
                    if success:
                        return True, result, candidate
                    last_failure = (result, candidate)
            assert last_failure is not None
            return False, last_failure[0], last_failure[1]
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _verify(self, code: str, description: str) -> tuple[bool, Any]:
        if self.session_log:
            self.session_log.info("LemmaExecutor verifying %s", description)
        result = await self.runner.check_proof(code)
        if self.session_log:
            self.session_log.info(
                "LemmaExecutor %s: success=%s errors=%d",
                description,
                result.success,
                len(result.errors),
            )
        return result.success, result

    async def _report(self, message: str) -> None:
        """Surface a user-facing progress line, if a callback is wired."""
        if self.progress_callback is None:
            return
        try:
            await self.progress_callback(message)
        except Exception:
            log.debug("LemmaExecutor progress callback failed", exc_info=True)

    async def execute(self) -> str | None:
        """Generate and verify each lemma, then the main theorem.

        Returns the complete Lean code if all steps verify, otherwise None.
        On failure ``self.verified_lemmas`` keeps the lemmas that did verify
        and ``self.last_failure`` describes the abort.
        """
        if self.wall_seconds is not None and self.wall_seconds > 0:
            try:
                return await asyncio.wait_for(
                    self._execute_impl(), timeout=self.wall_seconds
                )
            except asyncio.TimeoutError:
                self.last_failure = {
                    "lemma": None,
                    "failure_kind": "timeout",
                    "detail": (
                        "lemma-decomposition execution exceeded its wall "
                        f"budget ({self.wall_seconds}s)"
                    ),
                }
                if self.session_log:
                    self.session_log.warning(
                        "LemmaExecutor hit wall budget (%.1fs)", self.wall_seconds
                    )
                await self._report("分解证明超出整体时间预算，已中止")
                return None
        return await self._execute_impl()

    async def _execute_impl(self) -> str | None:
        """Generate and verify each lemma, then the main theorem."""
        if not self.plan.lemmas:
            return None
        if not self.plan.goal_type.strip():
            log.warning("LemmaDAGExecutor: plan has no goal_type; aborting.")
            return None

        system = (
            "You are an expert Lean 4 coder using mathlib4. "
            "You are writing ONE proof body at a time. "
            "Output only the tactics/proof term inside a single ```lean fenced block. "
            "Do NOT include imports, `open`, or other lemmas/theorems. "
            "Indent each tactic line with two spaces. "
            "Do NOT write explicit mathlib4 theorem names you are unsure of. "
            "Do NOT write explanatory words like `uses`, `applys`, or `by` multiple times. "
            "Use only valid Lean tactics such as `exact?`, `apply?`, `rw?`, `simp?`, "
            "`omega`, `nlinarith`, `ring`, `norm_num`, `linarith`, `rcases`, `have`, `calc`."
        )

        # --- Prove the lemmas, one dependency level at a time ---
        # depends_on forms a DAG; independent lemmas in the same level are
        # proved concurrently (bounded by max_parallel), and each level still
        # verifies against the accumulated verified code of previous levels.
        levels = _lemma_levels(self.plan.lemmas)
        total = len(self.plan.lemmas)

        for level in levels:
            context_code = self._build_code()
            verified_snapshot = list(self.verified_lemmas)
            # Scale workers to the level size; small levels don't need the
            # full pool, large levels use up to max_parallel.
            semaphore = asyncio.Semaphore(min(len(level), self.max_parallel))

            async def prove_one(idx: int, lemma: dict) -> tuple[int, str | None]:
                async with semaphore:
                    code = await self._prove_lemma(
                        idx, lemma, system, context_code, verified_snapshot, total
                    )
                    return idx, code

            results = await asyncio.gather(
                *(prove_one(idx, lemma) for idx, lemma in level)
            )
            if self.rescue_enabled:
                failed = [
                    (idx, lemma)
                    for (idx, lemma), (_, code) in zip(level, results)
                    if code is None
                ]
                if failed:
                    # Rescues run concurrently against the same base snapshot
                    # and merge deterministically in index order afterwards.
                    base_verified = list(self.verified_lemmas)
                    outcomes = await asyncio.gather(
                        *(
                            self._rescue_lemma(
                                idx, lemma, system, total, base_verified
                            )
                            for idx, lemma in failed
                        )
                    )
                    parent_codes: dict[int, str | None] = {}
                    for (idx, _), (parent_code, sub_codes) in zip(failed, outcomes):
                        self.verified_lemmas.extend(sub_codes)
                        parent_codes[idx] = parent_code
                    results = [
                        (idx, code if code is not None else parent_codes.get(idx))
                        for (idx, _), (_, code) in zip(level, results)
                    ]
            # Keep the level's successes even when a sibling fails: partial
            # progress is reported to the agent for the next attempt.
            for _, lemma_code in sorted(results, key=lambda item: item[0]):
                if lemma_code is not None:
                    self.verified_lemmas.append(lemma_code)
            failed = [idx for idx, code in results if code is None]
            if failed:
                if self.session_log:
                    self.session_log.warning(
                        "LemmaExecutor aborting: level failed at %s", failed
                    )
                return None

        # --- Prove the main theorem ---
        context_code = self._build_code()
        await self._report(
            f"{len(self.plan.lemmas)} 个引理全部验证通过，正在组装主定理…"
        )
        # Match the codegen convention (prompts.py) so evidence binding and
        # fidelity review treat this as the primary target declaration.
        main_name = "conjecta_target"
        main_system = (
            "You are an expert Lean 4 coder using mathlib4. "
            "You are writing the final theorem proof body. "
            "Output only the tactics/proof term inside a single ```lean fenced block. "
            "Do NOT include imports, `open`, or lemmas. "
            "Indent each tactic line with two spaces. "
            "Use the previously verified lemmas."
        )
        main_prompt = (
            f"Problem:\n{self.problem}\n\n"
            f"Informal proof:\n{self.informal_proof}\n\n"
            f"{self.plan.to_prompt_block()}\n\n"
            f"Verified lemmas:\n\n```lean\n{context_code}\n```\n\n"
            f"Now write the proof body for the final theorem:\n"
            f"theorem {main_name} : {self.plan.goal_type} := by\n\n"
            "If the formalization plan above includes 'Verified code from a previous successful run', "
            "follow its structure closely. Output only the indented tactics/proof term. "
            "Do not repeat the theorem header."
        )
        # If a previous successful run produced verified code for a similar problem,
        # try reusing its main-theorem proof body before asking the LLM.
        reused_body: str | None = None
        if self.plan.verified_code:
            reused_body = self._extract_theorem_body(self.plan.verified_code)
            if reused_body and self.session_log:
                self.session_log.info("Reusing main theorem body from verified memory code")

        if reused_body:
            proof_body = self._indent(reused_body)
        else:
            proof_body = await self._generate(main_system, main_prompt)
            proof_body = self._strip_header(proof_body, "theorem")
            proof_body = self._clean_proof_body(proof_body)
            proof_body = self._indent(proof_body)
        theorem_code = f"theorem {main_name} : {self.plan.goal_type} := by\n{proof_body}"

        full_code = self._build_code(theorem_code)
        success, result = await self._verify(full_code, "main theorem")
        for attempt in range(self.max_repair_attempts):
            if success:
                break
            if not _is_repairable(result.failure_kind):
                # Same short-circuit as the lemma loop: infra/timeout/unsafe
                # failures are not repairable by re-prompting.
                if self.session_log:
                    self.session_log.warning(
                        "Main theorem failed with non-repairable failure_kind=%s; "
                        "skipping LLM repair",
                        result.failure_kind,
                    )
                break
            if self.session_log:
                self.session_log.info("Repairing main theorem attempt %d", attempt + 1)
            await self._report(
                f"主定理未通过，正在修复（第 {attempt + 1}/{self.max_repair_attempts} 次）…"
            )
            diagnostic = "\n".join(result.errors)
            candidates = await asyncio.to_thread(self._candidates_block, diagnostic)
            repair_prompt = (
                f"Problem:\n{self.problem}\n\n"
                f"Informal proof:\n{self.informal_proof}\n\n"
                f"{self.plan.to_prompt_block()}\n\n"
                f"Verified lemmas:\n\n```lean\n{context_code}\n```\n\n"
                f"The following final theorem failed to type-check:\n\n```lean\n{theorem_code}\n```\n\n"
                f"Diagnostics:\n{diagnostic}\n\n"
            )
            if candidates:
                repair_prompt += candidates + "\n\n"
            repair_prompt += (
                "Fix the proof body. Output only the corrected indented tactics/proof term. "
                "If relevant declarations are listed above, prefer using their real mathlib4 names."
            )
            proof_body = await self._generate(main_system, repair_prompt)
            proof_body = self._strip_header(proof_body, "theorem")
            proof_body = self._indent(proof_body)
            theorem_code = f"theorem {main_name} : {self.plan.goal_type} := by\n{proof_body}"
            full_code = self._build_code(theorem_code)
            success, result = await self._verify(full_code, f"main theorem repair {attempt + 1}")

        if not success:
            self.last_failure = {
                "lemma": main_name,
                "failure_kind": result.failure_kind,
                "detail": (result.errors or ["unknown Lean error"])[0][:400],
            }
            await self._report("主定理多次修复仍未通过，分解证明失败")
            return None

        await self._report(f"主定理 `{main_name}` 验证通过")
        return full_code
