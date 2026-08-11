from __future__ import annotations

import heapq
import logging
import re
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from math_agent.llm.base import LLMBackend
    from math_agent.lean.repl_session import LeanReplPool
    from math_agent.lean.runner import LeanRunner

from math_agent.agent.prompts import TACTIC_GENERATOR_SYSTEM
from math_agent.lean.premise_retriever import PremiseRetriever
from math_agent.llm.base import Message

log = logging.getLogger("math_agent.lean.proof_search")


@dataclass
class ProofState:
    """A node in the proof search tree."""

    theorem_statement: str
    partial_proof: str = ""
    imports: str = ""
    depth: int = 0
    error: str = ""
    parent_tactic: str = ""
    # REPL mode: id of this node's proof state inside the REPL session and
    # the structured goal text reported by it (preferred over regex scraping).
    repl_state_id: int | None = None
    repl_goal: str = ""

    @property
    def goal(self) -> str:
        """Best-effort current goal string."""
        if self.repl_goal:
            return self.repl_goal
        if self.error:
            goal = _extract_unsolved_goal(self.error)
            if goal:
                return goal
        return self.theorem_statement

    @property
    def full_code(self) -> str:
        parts: list[str] = []
        if self.imports:
            parts.append(self.imports)
        parts.append(self.theorem_statement)
        if self.partial_proof:
            parts.append(self.partial_proof)
        return "\n".join(parts).strip()


def _extract_unsolved_goal(lean_output: str) -> str | None:
    """Extract the first `⊢ ...` goal from Lean diagnostics.

    Joins indented continuation lines so multi-line goals are preserved.
    """
    lines = lean_output.splitlines()
    for i, line in enumerate(lines):
        if "⊢" not in line:
            continue
        marker = line.index("⊢")
        base_indent = len(line[:marker])
        parts = [line[marker + 1 :].strip()]
        for continuation in lines[i + 1 :]:
            if not continuation:
                break
            continuation_indent = len(continuation) - len(continuation.lstrip())
            if continuation_indent <= base_indent:
                break
            parts.append(continuation.strip())
        goal = " ".join(parts)
        return " ".join(goal.split()) if goal else None
    return None


def _format_state_for_prompt(state: ProofState) -> str:
    lines = ["Theorem:", f"{state.theorem_statement}"]
    if state.partial_proof:
        lines.extend(["", "Proof so far:", f"```lean\n{state.full_code}\n```"])
    if state.parent_tactic:
        lines.append(f"Previous tactic: {state.parent_tactic}")
    lines.extend(["", f"Current goal: {state.goal}"])
    return "\n".join(lines)


@dataclass
class TacticGenerator:
    """LLM-backed generator of candidate tactics for a proof state."""

    llm: LLMBackend
    temperature: float = 0.7
    max_candidates: int = 5
    premise_retriever: PremiseRetriever | None = None
    trace_memory: object | None = None  # math_agent.lean.proof_trace_memory.ProofTraceMemory
    # Optional critic backend: when present, generated candidates are re-ranked
    # by the critic's per-tactic promise scores (a cheap learned-value stand-in
    # for the best-first frontier).
    critic_llm: LLMBackend | None = None

    async def generate(self, state: ProofState) -> list[str]:
        prompt = _format_state_for_prompt(state)
        premises = ""
        if self.premise_retriever is not None:
            retrieved = self.premise_retriever.retrieve(state.goal, top_k=5)
            if retrieved:
                premises = (
                    "Relevant mathlib4 declarations you may use:\n"
                    + "\n".join(e.to_prompt_text() for e in retrieved)
                    + "\n\n"
                )
        exemplars = ""
        if self.trace_memory is not None:
            try:
                similar = self.trace_memory.similar(state.theorem_statement, top_k=2)
            except Exception:
                similar = []
            if similar:
                exemplars = (
                    "Verified proofs of similar statements (adapt their tactics):\n"
                    + "\n\n".join(
                        f"```lean\n{trace.proof}\n```" for trace in similar
                    )
                    + "\n\n"
                )
        full_prompt = f"{exemplars}{premises}{prompt}"
        messages = [Message(role="user", content=full_prompt)]
        response = await self.llm.complete(
            messages,
            system=TACTIC_GENERATOR_SYSTEM,
            temperature=self.temperature,
        )
        candidates = self._parse(response.text)
        if self.critic_llm is not None and len(candidates) > 1:
            scores = await self._score_candidates(state, candidates)
            if scores is not None:
                candidates = [
                    candidate
                    for _, candidate in sorted(
                        zip(scores, candidates), key=lambda item: -item[0]
                    )
                ]
        return candidates

    async def _score_candidates(
        self, state: ProofState, candidates: list[str]
    ) -> list[float] | None:
        """Critic score per candidate (higher = more promising); None on failure."""
        listing = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(candidates))
        prompt = (
            f"Current Lean 4 goal:\n{state.goal}\n\n"
            f"Candidate tactics:\n{listing}\n\n"
            "Rate each candidate's promise of making proof progress, one score "
            "per line as `index score` with score in [0, 10]. Output only the lines."
        )
        try:
            response = await self.critic_llm.complete(
                [Message(role="user", content=prompt)],
                system=(
                    "You are a strict Lean 4 proof critic. Score tactic "
                    "candidates by expected progress; be terse."
                ),
                temperature=0.0,
            )
        except Exception:
            log.debug("critic scoring failed", exc_info=True)
            return None
        scores: dict[int, float] = {}
        for line in response.text.splitlines():
            match = re.match(r"\s*(\d+)\D+(-?\d+(?:\.\d+)?)", line.strip())
            if match:
                scores[int(match.group(1)) - 1] = float(match.group(2))
        if len(scores) < len(candidates):
            return None
        return [scores[i] for i in range(len(candidates))]

    def _parse(self, response: str) -> list[str]:
        lines = [line.strip() for line in response.splitlines() if line.strip()]
        candidates: list[str] = []
        for line in lines:
            # Remove leading numbering like "1." or "-".
            cleaned = re.sub(r"^(\d+\.\s*|[-*]\s*)", "", line).strip()
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)
            if len(candidates) >= self.max_candidates:
                break
        return candidates


@dataclass
class ProofSearchResult:
    success: bool
    proof: str = ""
    attempts: int = 0
    final_state: ProofState | None = None
    error: str = ""


@dataclass
class ProofSearch:
    """Breadth-first search over tactic sequences.

    With a ``repl_pool`` attached (and the REPL binary available), search runs
    against a long-running Lean REPL: structured goal states, no per-step
    recompilation. Any REPL failure falls back to the batch-compile path,
    which stays the final verification authority either way.
    """

    generator: TacticGenerator
    runner: LeanRunner
    max_attempts: int = 32
    max_depth: int = 8
    max_branching: int = 3
    premise_retriever: PremiseRetriever | None = None
    repl_pool: LeanReplPool | None = None
    # Verified-trace flywheel: successful proofs are recorded here and similar
    # ones are shown to the generator as exemplars on later searches.
    trace_memory: object | None = None
    # Optional async callback (message: str) for user-facing search progress.
    progress_callback: object | None = None
    # Precise `import ...` lines for the statement (REPL mode loads them once
    # up front; batch mode prepends them to every candidate check). Never use
    # the umbrella `import Mathlib` here — it does not fit small-RAM hosts.
    imports: str = ""

    async def search(
        self, theorem_statement: str, max_attempts: int | None = None
    ) -> ProofSearchResult:
        max_attempts = self.max_attempts if max_attempts is None else max_attempts
        result: ProofSearchResult | None = None
        if self.repl_pool is not None:
            from math_agent.lean.repl_session import LeanReplPool, ReplProtocolError

            if LeanReplPool.available(self.repl_pool.config):
                try:
                    result = await self._search_repl(theorem_statement, max_attempts)
                except ReplProtocolError:
                    log.warning(
                        "REPL proof search failed; falling back to batch compile",
                        exc_info=True,
                    )
        if result is None:
            result = await self._search_batch(theorem_statement, max_attempts)
        if result.success and self.trace_memory is not None:
            try:
                self.trace_memory.record(
                    theorem_statement, result.proof, attempts=result.attempts
                )
            except Exception:
                log.debug("proof trace record failed", exc_info=True)
        return result

    async def _report(self, message: str) -> None:
        """Best-effort user-facing progress line (never fails the search)."""
        if self.progress_callback is None:
            return
        try:
            await self.progress_callback(message)
        except Exception:
            log.debug("proof-search progress callback failed", exc_info=True)

    async def _search_repl(
        self, theorem_statement: str, max_attempts: int
    ) -> ProofSearchResult:
        """REPL-backed search: one compile for setup, then cheap tactic steps."""
        statement = theorem_statement.strip()
        imports = self.imports.strip()
        async with self.repl_pool.session() as session:
            header = f"{imports}\n\n{statement}" if imports else statement
            blocked = session.static_gate(header, label="<repl-search>")
            if blocked:
                return ProofSearchResult(
                    success=False,
                    attempts=0,
                    error=(
                        "static Lean source gate blocked the statement: "
                        + ", ".join(blocked)
                    ),
                )
            # Tactic mode entry: a trailing sorry yields a proof state id and
            # the structured root goal. The sorry lives only inside the REPL;
            # candidate proofs returned by search never contain it.
            command = await session.run_command(f"{header} sorry")
            if command.errors or not command.sorries:
                log.warning(
                    "REPL could not open tactic mode (%s); using batch path",
                    "; ".join(command.errors) or "no sorry reported",
                )
                return await self._search_batch(statement, max_attempts)
            root_sorry = command.sorries[0]
            if root_sorry.proof_state is None:
                return await self._search_batch(statement, max_attempts)
            await self._report("REPL 会话已建立，开始 best-first 证明搜索…")

            initial = ProofState(
                theorem_statement=statement,
                imports=imports,
                repl_state_id=root_sorry.proof_state,
                repl_goal=root_sorry.goal,
            )
            # Best-first search (HTPS-lite): expand the most promising frontier
            # state first. Priority = open goal count, then goal size, then
            # depth — cheap structural proxies for a learned value function.
            frontier: list[tuple[tuple[int, int, int, int], ProofState]] = []
            push_order = 0

            def _push(node: ProofState) -> None:
                nonlocal push_order
                goal_count = node.repl_goal.count("\n\n") + 1 if node.repl_goal else 1
                priority = (goal_count, len(node.repl_goal), node.depth, push_order)
                heapq.heappush(frontier, (priority, node))
                push_order += 1

            _push(initial)
            seen: set[str] = {initial.full_code}
            attempts = 0
            deepest = initial

            while frontier and attempts < max_attempts:
                _, state = heapq.heappop(frontier)
                if state.repl_state_id is None:
                    continue
                if state.depth > deepest.depth:
                    deepest = state

                candidates = await self.generator.generate(state)
                candidates = candidates[: self.max_branching]

                for tactic in candidates:
                    if attempts >= max_attempts:
                        break
                    if session.static_gate(tactic, label="<repl-tactic>"):
                        continue
                    attempts += 1
                    if attempts % 8 == 0:
                        await self._report(
                            f"证明搜索进行中：已尝试 {attempts} 个 tactic，"
                            f"前沿 {len(frontier)} 个状态，最深 {deepest.depth} 层"
                        )
                    step = await session.run_tactic(tactic, state.repl_state_id)
                    if step.failed or step.proof_state is None:
                        continue
                    child = self._child_state(state, tactic)
                    child.repl_state_id = step.proof_state
                    child.repl_goal = "\n\n".join(step.goals)
                    signature = f"{child.full_code}|{child.repl_goal}"
                    if signature in seen:
                        continue
                    seen.add(signature)

                    if step.completed:
                        # The REPL is a search accelerator, not the verifier of
                        # record: the assembled proof must still pass the batch
                        # checker before we report success.
                        result = await self.runner.check_proof(child.full_code)
                        if result.success:
                            await self._report(
                                f"证明搜索闭合（{attempts} 次尝试），正在批验证…"
                            )
                            return ProofSearchResult(
                                success=True,
                                proof=child.full_code,
                                attempts=attempts,
                                final_state=child,
                            )
                        continue
                    if child.depth < self.max_depth:
                        _push(child)

            return ProofSearchResult(
                success=False,
                proof=deepest.full_code,
                attempts=attempts,
                final_state=deepest,
                error=f"Search exhausted after {attempts} attempts (max {max_attempts}).",
            )

    async def _search_batch(
        self, theorem_statement: str, max_attempts: int
    ) -> ProofSearchResult:
        initial = ProofState(
            theorem_statement=theorem_statement.strip(),
            imports=self.imports.strip(),
        )
        queue: deque[ProofState] = deque([initial])
        seen: set[str] = {initial.full_code}
        attempts = 0
        deepest = initial

        while queue and attempts < max_attempts:
            state = queue.popleft()
            if state.depth > deepest.depth:
                deepest = state

            candidates = await self.generator.generate(state)
            # Limit branching to stay within budget.
            candidates = candidates[: self.max_branching]

            for tactic in candidates:
                if attempts >= max_attempts:
                    break
                attempts += 1
                if attempts % 8 == 0:
                    await self._report(
                        f"证明搜索进行中（批编译模式）：已尝试 {attempts} 个 tactic，"
                        f"最深 {deepest.depth} 层"
                    )
                child = self._child_state(state, tactic)
                if child.full_code in seen:
                    continue
                seen.add(child.full_code)

                result = await self.runner.check_proof(child.full_code)
                if result.success:
                    return ProofSearchResult(
                        success=True,
                        proof=child.full_code,
                        attempts=attempts,
                        final_state=child,
                    )

                # If Lean produced an error with a remaining goal, enqueue.
                goal = _extract_unsolved_goal("\n".join(result.errors) + "\n" + result.output)
                if goal and child.depth < self.max_depth:
                    child.error = "\n".join(result.errors) + "\n" + result.output
                    if self.premise_retriever is not None and result.errors:
                        import_block = self.premise_retriever.repair_imports_for_errors(
                            child.full_code, result.errors
                        )
                        if import_block and import_block not in (child.imports or ""):
                            child.imports = f"{child.imports or ''}\n{import_block}".strip()
                            seen.add(child.full_code)
                            repaired = await self.runner.check_proof(child.full_code)
                            if repaired.success:
                                return ProofSearchResult(
                                    success=True,
                                    proof=child.full_code,
                                    attempts=attempts,
                                    final_state=child,
                                )
                            new_error_text = (
                                "\n".join(repaired.errors) + "\n" + repaired.output
                            )
                            new_goal = _extract_unsolved_goal(new_error_text)
                            if new_goal and child.depth < self.max_depth:
                                child.error = new_error_text
                                queue.append(child)
                            continue
                    queue.append(child)

        return ProofSearchResult(
            success=False,
            proof=deepest.full_code,
            attempts=attempts,
            final_state=deepest,
            error=f"Search exhausted after {attempts} attempts (max {max_attempts}).",
        )

    def _child_state(self, state: ProofState, tactic: str) -> ProofState:
        separator = "\n" if state.partial_proof else ""
        new_partial = f"{state.partial_proof}{separator}  {tactic}"
        return ProofState(
            theorem_statement=state.theorem_statement,
            partial_proof=new_partial,
            imports=state.imports,
            depth=state.depth + 1,
            parent_tactic=tactic,
        )
