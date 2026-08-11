from __future__ import annotations

import asyncio

from math_agent.agent.prompts import FORMALIZATION_DECISION_SYSTEM, LEAN_CODEGEN_SYSTEM
from math_agent.agent.state import ReasoningStep, ReasoningState
from math_agent.config import LeanConfig
from math_agent.lean.failure_policy import lean_failure_policy
from math_agent.lean.runner import LeanRunner
from math_agent.lean.result import LeanResult
from math_agent.lean.critic import default_critic
from math_agent.lean.premise_retriever import PremiseRetriever
from math_agent.llm.base import LLMBackend, Message
from math_agent.text_utils import extract_lean_code


def _is_repairable(failure_kind: str | None) -> bool:
    """Whether a Lean failure kind is fixable by re-prompting the model.

    Derived from the canonical mapping in ``math_agent.lean.failure_policy``
    (``None``/unclassified counts as repairable).
    """
    return lean_failure_policy(failure_kind) == "repair"


class LeanCodegen:
    """Generates Lean 4 code from mathematical claims and iterates on failures."""

    def __init__(
        self,
        llm: LLMBackend,
        runner: LeanRunner,
        config: LeanConfig,
        premise_retriever: PremiseRetriever | None = None,
    ) -> None:
        self.llm = llm
        self.runner = runner
        self.config = config
        self.premise_retriever = premise_retriever

    async def should_formalize(self, step: ReasoningStep, state: ReasoningState) -> bool:
        """Ask the LLM whether this step warrants formalization."""
        prompt = (
            f"Proof context:\n{state.context_window(max_steps=5)}\n\n"
            f"Step to evaluate:\n{step.content}\n\n"
            f"Should this step be formally verified in Lean 4?"
        )
        messages = [Message(role="user", content=prompt)]
        response = await self.llm.complete(
            messages, system=FORMALIZATION_DECISION_SYSTEM, temperature=0.0
        )
        return response.text.strip().upper().startswith("YES")

    async def generate_and_verify(
        self,
        step: ReasoningStep,
        state: ReasoningState,
        timeout_seconds: float | None = None,
    ) -> tuple[str | None, LeanResult | None]:
        """Generate Lean code for a step and iterate until it type-checks."""
        coro = self._generate_and_verify_impl(step, state)
        if timeout_seconds is not None and timeout_seconds > 0:
            try:
                return await asyncio.wait_for(coro, timeout=timeout_seconds)
            except asyncio.TimeoutError:
                return None, None
        return await coro

    async def _generate_and_verify_impl(
        self, step: ReasoningStep, state: ReasoningState
    ) -> tuple[str | None, LeanResult | None]:
        """Generate Lean code for a step and iterate until it type-checks."""
        lean_code = await self._generate(step, state)
        lean_code = await self._critic_repair(lean_code, step, state)

        max_attempts = max(1, int(self.config.max_repair_attempts))
        last_result: LeanResult | None = None
        for attempt in range(max_attempts):
            result = await self.runner.check_proof(lean_code)
            last_result = result
            if result.success:
                return lean_code, result
            if not _is_repairable(result.failure_kind):
                return lean_code, result
            if attempt >= max_attempts - 1:
                break
            repaired = await self._repair(lean_code, result.errors, step, state)
            if repaired.strip() == lean_code.strip():
                return lean_code, result
            lean_code = repaired

        return lean_code, last_result

    async def _critic_repair(
        self, lean_code: str, step: ReasoningStep, state: ReasoningState
    ) -> str:
        """Run the pre-verification critic and ask the LLM to fix obvious mistakes."""
        try:
            critic = default_critic()
        except Exception:
            return lean_code
        result = await asyncio.to_thread(critic.critique, lean_code)
        if not result.has_issues:
            return lean_code

        prompt = (
            f"Mathematical context:\n{state.context_window(max_steps=5)}\n\n"
            f"Statement to formalize:\n{step.content}\n\n"
            f"The following Lean 4 code has likely problems:\n\n```lean\n{lean_code}\n```\n\n"
            f"{result.to_prompt_block()}\n\n"
            "Fix all issues. Prefer `exact?`/`apply?`/`rw?` over explicit mathlib theorem names. "
            "Output ONLY the corrected complete Lean 4 code."
        )
        messages = [Message(role="user", content=prompt)]
        response = await self.llm.complete(messages, system=LEAN_CODEGEN_SYSTEM)
        return extract_lean_code(response.text)

    async def _generate(self, step: ReasoningStep, state: ReasoningState) -> str:
        premises = ""
        if self.premise_retriever is not None:
            goal = step.content.strip()
            retrieved = await asyncio.to_thread(
                self.premise_retriever.retrieve, goal, top_k=5
            )
            if retrieved:
                premises = (
                    "Relevant mathlib4 declarations you may use:\n"
                    + "\n".join(e.to_prompt_text() for e in retrieved)
                    + "\n\n"
                )
        prompt = (
            f"{premises}Mathematical context:\n{state.context_window(max_steps=5)}\n\n"
            f"Statement to formalize:\n{step.content}\n\n"
            f"Generate a complete Lean 4 file that formalizes and proves this."
        )
        messages = [Message(role="user", content=prompt)]
        response = await self.llm.complete(messages, system=LEAN_CODEGEN_SYSTEM)
        return extract_lean_code(response.text)

    async def _repair(
        self, lean_code: str, errors: list[str], step: ReasoningStep, state: ReasoningState
    ) -> str:
        if self.premise_retriever is not None and errors:
            import_block = await asyncio.to_thread(
                self.premise_retriever.repair_imports_for_errors, lean_code, errors
            )
            if import_block and import_block not in lean_code:
                lean_code = f"{import_block}\n\n{lean_code}"
        prompt = (
            f"The following Lean 4 code failed to type-check:\n\n```lean\n{lean_code}\n```\n\n"
            f"Errors:\n" + "\n".join(f"- {e}" for e in errors) + "\n\n"
            f"Original statement: {step.content}\n\n"
            f"Fix the code so it type-checks. Output ONLY the corrected Lean 4 code."
        )
        messages = [Message(role="user", content=prompt)]
        response = await self.llm.complete(messages, system=LEAN_CODEGEN_SYSTEM)
        return extract_lean_code(response.text)
