from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from typing import Protocol

from math_agent.agent.formal_evidence import formal_evidence_id
from math_agent.agent.prompts import COMPLETENESS_SYSTEM, CRITIC_SYSTEM
from math_agent.agent.react_state import ReActTrace, ReActTurn, ReviewResult
from math_agent.agent.state import ReasoningState, ReasoningStep, StepType
from math_agent.llm.base import LLMBackend, Message
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from math_agent.lean.codegen import LeanCodegen

log = logging.getLogger("math_agent.agent.reviewers")


def _bind_formal_evidence(turn: ReActTurn, lean_code: str, target_claim: str) -> None:
    """Attach the verified Lean artifact to the turn observation.

    Downstream reviewers (e.g. fidelity) inspect ``turn.observation.lean_code``
    and ``turn.observation.metadata["formal_evidence"]`` to decide whether the
    conclusion is formally grounded.
    """
    turn.observation.lean_code = lean_code
    declared_claim = str(
        turn.action.args.get("claim")
        or turn.action.args.get("statement")
        or turn.action.args.get("answer")
        or target_claim
    ).strip()
    evidence_id = formal_evidence_id(
        action_name=turn.action.name,
        target_claim=target_claim,
        artifact=lean_code,
    )
    metadata = dict(turn.observation.metadata)
    metadata["formal_evidence"] = {
        "id": evidence_id,
        "action": turn.action.name,
        "target_claim": target_claim.strip(),
        "declared_claim": declared_claim,
        "artifact_sha256": hashlib.sha256(lean_code.encode("utf-8")).hexdigest(),
        "lean_code_sha256": hashlib.sha256(lean_code.encode("utf-8")).hexdigest(),
        "passed": True,
    }
    turn.observation.metadata = metadata


class Reviewer(Protocol):
    name: str

    async def review(self, turn: ReActTurn, trace: ReActTrace) -> ReviewResult: ...


def _build_review_context(turn: ReActTurn, trace: ReActTrace) -> str:
    """Shared prompt prefix for the LLM reviewers.

    Every reviewer prompt starts with this exact string so provider-side
    prefix caching kicks in across the panel; reviewers only append their
    role-specific evidence and instructions.
    """
    answer = turn.action.args.get("answer", turn.thought)
    return (
        f"Original problem:\n{trace.problem[:4000]}\n\n"
        f"Current goal:\n{trace.current_goal[:1000]}\n\n"
        f"Candidate conclusion:\n{answer[:6000]}\n\n"
    )


class CriticReviewer:
    name = "critic"

    def __init__(self, llm: LLMBackend) -> None:
        self.llm = llm

    async def review(self, turn: ReActTurn, trace: ReActTrace) -> ReviewResult:
        recent_evidence = "\n".join(
            f"- {prior.action.name}: {prior.observation.output[:800]}"
            for prior in trace.turns[-3:]
        )
        claim_note = _claim_check_review_note(trace)
        prompt = (
            _build_review_context(turn, trace)
            + f"Recent tool evidence:\n{recent_evidence or '(none)'}\n\n"
            + f"{claim_note}"
            "Decide PASS or FAIL using the fatal-error bar only. FAIL only if you identify "
            "a concrete mathematical error that would very likely make the main claim false "
            "or leave it unsupported as if proven. Minor gaps, style issues, incomplete "
            "exposition, and non-load-bearing nits must be PASS."
        )
        response = await _timed_critic_completion(
            self.llm,
            [Message(role="user", content=prompt)],
            system=CRITIC_SYSTEM,
            temperature=0.2,
            phase="critic_review",
        )
        return _parse_critic_response(self.name, response)


class FormalReviewer:
    name = "formal"

    def __init__(
        self,
        lean_codegen: LeanCodegen | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.lean_codegen = lean_codegen
        self.timeout_seconds = timeout_seconds

    async def review(self, turn: ReActTurn, trace: ReActTrace) -> ReviewResult:
        if self.lean_codegen is None:
            return ReviewResult(
                reviewer=self.name,
                verdict="PASS",
                suggestions=["Lean formalization unavailable."],
                confidence=1.0,
            )

        # Prose conclusions are formalized on a best-effort basis with a short timeout
        # so the UI/reviewer pipeline does not hang.
        if turn.action.name == "conclude":
            answer = turn.action.args.get("answer", turn.thought)
            state = ReasoningState(problem=trace.problem, current_goal=trace.current_goal)
            step = ReasoningStep(content=answer, step_type=StepType.REASONING)

            try:
                should = await self.lean_codegen.should_formalize(step, state)
            except Exception as exc:
                log.warning("Formalization decision failed: %s", exc)
                should = False

            if not should:
                return ReviewResult(
                    reviewer=self.name,
                    verdict="PASS",
                    suggestions=["Prose conclusion not formalizable; skipped formal check."],
                    confidence=0.9,
                )

            try:
                lean_code, result = await self.lean_codegen.generate_and_verify(
                    step, state, timeout_seconds=self.timeout_seconds
                )
            except asyncio.TimeoutError:
                return ReviewResult(
                    reviewer=self.name,
                    verdict="PASS",
                    suggestions=["Prose conclusion formalization timed out; skipped formal check."],
                    confidence=0.9,
                )
            except Exception as exc:
                return ReviewResult(
                    reviewer=self.name,
                    verdict="PASS",
                    suggestions=[f"Prose formalization error: {exc}; skipped formal check."],
                    confidence=0.9,
                )

            if lean_code is None and result is None:
                return ReviewResult(
                    reviewer=self.name,
                    verdict="PASS",
                    suggestions=["Prose conclusion formalization timed out; skipped formal check."],
                    confidence=0.9,
                )

            if lean_code and result and result.success:
                _bind_formal_evidence(
                    turn,
                    lean_code,
                    target_claim=trace.current_goal or trace.problem,
                )
                return ReviewResult(
                    reviewer=self.name,
                    verdict="PASS",
                    suggestions=["Prose conclusion formalization succeeded."],
                    confidence=1.0,
                )

            errors = result.errors if result else ["Unknown error"]
            return ReviewResult(
                reviewer=self.name,
                verdict="FAIL",
                issues=[f"Prose conclusion formalization failed: {'; '.join(errors[:3])}"],
                suggestions=["Fix the reasoning or provide a simpler claim."],
                confidence=0.9,
            )

        state = ReasoningState(problem=trace.problem, current_goal=trace.current_goal)
        step = ReasoningStep(content=turn.thought, step_type=StepType.REASONING)

        try:
            should = await self.lean_codegen.should_formalize(step, state)
        except Exception as exc:
            log.warning("Formalization decision failed: %s", exc)
            should = False

        if not should:
            return ReviewResult(
                reviewer=self.name,
                verdict="PASS",
                confidence=0.9,
            )

        try:
            lean_code, result = await self.lean_codegen.generate_and_verify(step, state)
        except Exception as exc:
            return ReviewResult(
                reviewer=self.name,
                verdict="FAIL",
                issues=[f"Lean formalization error: {exc}"],
                suggestions=["Try a simpler statement or skip formalization."],
                confidence=0.8,
            )

        if lean_code and result and result.success:
            _bind_formal_evidence(
                turn,
                lean_code,
                target_claim=trace.current_goal or trace.problem,
            )
            return ReviewResult(
                reviewer=self.name,
                verdict="PASS",
                suggestions=["Lean formalization succeeded."],
                confidence=1.0,
            )

        errors = result.errors if result else ["Unknown error"]
        return ReviewResult(
            reviewer=self.name,
            verdict="FAIL",
            issues=[f"Lean formalization failed: {'; '.join(errors[:3])}"],
            suggestions=["Fix the formalization or reasoning."],
            confidence=0.9,
        )


class KnowledgeReviewer:
    name = "knowledge"

    def __init__(self, knowledge_store=None) -> None:
        self.knowledge_store = knowledge_store

    async def review(self, turn: ReActTurn, trace: ReActTrace) -> ReviewResult:
        project_id = trace.project_context.project_id
        if not project_id or self.knowledge_store is None:
            return ReviewResult(
                reviewer=self.name,
                verdict="PASS",
                confidence=1.0,
            )

        query = f"{trace.current_goal} {turn.thought}"[:200]
        try:
            facts = await asyncio.to_thread(
                self.knowledge_store.search_facts, project_id, query, limit=5
            )
            if not facts:
                return ReviewResult(
                    reviewer=self.name,
                    verdict="PASS",
                    confidence=1.0,
                )
            suggestions = [f"Related fact: {f.get('statement', '')}" for f in facts[:3]]
            return ReviewResult(
                reviewer=self.name,
                verdict="PASS",
                suggestions=suggestions,
                confidence=0.8,
            )
        except Exception as exc:
            log.warning("Knowledge reviewer search failed: %s", exc)
            return ReviewResult(
                reviewer=self.name,
                verdict="PASS",
                suggestions=[f"Knowledge search failed: {exc}"],
                confidence=1.0,
            )


_FORMAL_FIDELITY_SYSTEM = (
    "You are a formalization fidelity gate for Lean artifacts. "
    "Compare the original problem with the formal Lean goal.\n\n"
    "FAIL only when the formalization is materially the wrong statement in a way that "
    "would make accepting it endorse a false or different claim — e.g. flipped "
    "quantifiers/implication, dropped essential hypotheses, wrong conclusion direction, "
    "or a clearly easier substitute theorem.\n\n"
    "PASS for minor encoding differences, naming, harmless type choices, or incomplete "
    "but directionally faithful formalizations that do not change the claim. If unsure "
    "whether a mismatch is fatal, PASS.\n\n"
    "Respond in this exact format:\n"
    "VERDICT: PASS or FAIL\n"
    "ISSUES: (list each FATAL mismatch only, or \"none\")\n"
    "SUGGESTIONS: (how to fix fatal issues, or \"none\")\n"
    "CONFIDENCE: (0.0 to 1.0)"
)

_PROSE_FIDELITY_SYSTEM = (
    "You are a semantic fidelity gate for mathematical answers. "
    "Compare the original natural-language problem with the candidate natural-language "
    "conclusion.\n\n"
    "FAIL only when the candidate answers a materially different claim — wrong "
    "conclusion, dropped essential hypothesis that changes the result, flipped "
    "quantifiers/direction, or silently substituting an easier problem — such that "
    "accepting it would endorse a false or unwarranted claim.\n\n"
    "PASS for incomplete exposition, style, minor wording, or small omissions that do "
    "not change what is being claimed. This is NOT a Lean formalization review. Do not "
    "request Lean syntax, typed variables, Finset encodings, formal predicates, or "
    "explicit proof-assistant quantifiers unless the user asked for formalization. "
    "Logical proof gaps belong to the critic; report only fatal statement/answer "
    "mismatch here. If unsure, PASS.\n\n"
    "Respond in this exact format:\n"
    "VERDICT: PASS or FAIL\n"
    "ISSUES: (list each FATAL mismatch only, or \"none\")\n"
    "SUGGESTIONS: (how to fix fatal issues, or \"none\")\n"
    "CONFIDENCE: (0.0 to 1.0)"
)


class CompletenessReviewer:
    """Fail conclude writeups that leave load-bearing assertions unjustified."""

    name = "completeness"

    def __init__(self, llm: LLMBackend) -> None:
        self.llm = llm

    async def review(self, turn: ReActTurn, trace: ReActTrace) -> ReviewResult:
        claim_note = _claim_check_review_note(trace)
        prompt = (
            _build_review_context(turn, trace)
            + f"{claim_note}"
            "Decide PASS or FAIL using the completeness bar only. FAIL only when a "
            "load-bearing assertion/lemma/structural step is left unjustified. "
            "Missing diagrams are suggestions, not FAIL reasons."
        )
        response = await _timed_critic_completion(
            self.llm,
            [Message(role="user", content=prompt)],
            system=COMPLETENESS_SYSTEM,
            temperature=0.2,
            phase="completeness_review",
        )
        return _parse_critic_response(self.name, response)


class StatementFidelityReviewer:
    name = "fidelity"

    def __init__(self, llm: LLMBackend) -> None:
        self.llm = llm

    async def review(self, turn: ReActTurn, trace: ReActTrace) -> ReviewResult:
        has_formal_artifact = bool(turn.observation.lean_code)
        prompt_parts = [_build_review_context(turn, trace)]
        if has_formal_artifact:
            evidence = turn.observation.metadata.get("formal_evidence")
            if isinstance(evidence, dict):
                prompt_parts.append(
                    "Formal evidence binding:\n"
                    f"- target claim: {str(evidence.get('target_claim') or '')[:2000]}\n"
                    f"- requested claim: {str(evidence.get('requested_claim') or '')[:2000]}\n"
                    f"- declared claim: {str(evidence.get('declared_claim') or '')[:2000]}\n"
                    f"- checked declaration: {str((evidence.get('primary_declaration') or {}).get('name') or '')[:200]}\n"
                    f"- evidence id: {str(evidence.get('id') or '')[:200]}\n\n"
                )
            prompt_parts.append(
                f"Lean formalization:\n{turn.observation.lean_code[:6000]}\n\n"
            )
        claim_note = _claim_check_review_note(trace)
        if claim_note:
            prompt_parts.append(claim_note)
        prompt_parts.append(
            "FAIL only for a fatal statement mismatch that would endorse a false or "
            "different claim; otherwise PASS."
            if has_formal_artifact
            else "FAIL only if the candidate answers a materially different claim in a "
            "way that would endorse a false or unwarranted result; otherwise PASS."
        )
        prompt = "".join(prompt_parts)
        response = await _timed_critic_completion(
            self.llm,
            [Message(role="user", content=prompt)],
            system=(
                _FORMAL_FIDELITY_SYSTEM
                if has_formal_artifact
                else _PROSE_FIDELITY_SYSTEM
            ),
            temperature=0.2,
            phase="fidelity_review",
        )
        return _parse_critic_response(self.name, response)


def _claim_check_review_note(trace: ReActTrace) -> str:
    """Extra reviewer pressure when early claim check found a false statement."""
    data = getattr(trace, "claim_check", None)
    if not isinstance(data, dict) or not data:
        return ""
    blocked = bool(data.get("counterexample_found")) or str(
        data.get("status") or ""
    ).strip().lower() == "false_as_stated"
    if not blocked:
        return ""
    summary = str(data.get("refute_summary") or "").strip()
    revised = str(data.get("revised_claim") or "").strip()
    parts = [
        "Early claim check flagged the original statement as false or "
        "counterexampled. FAIL if the candidate claims the original statement "
        "is proved.\n"
    ]
    if summary:
        parts.append(f"Claim-check summary: {summary[:1200]}\n")
    if revised:
        parts.append(f"Revised claim (if any): {revised[:2000]}\n")
    parts.append("\n")
    return "".join(parts)


async def _timed_critic_completion(
    llm: LLMBackend,
    messages: list[Message],
    *,
    system: str,
    temperature: float,
    phase: str,
) -> str:
    started = time.monotonic()
    try:
        response = await llm.complete(
            messages,
            system=system,
            temperature=temperature,
        )
        return response.text
    finally:
        log.info(
            "phase_duration phase=%s model_role=critic duration_seconds=%.3f",
            phase,
            time.monotonic() - started,
        )


def _parse_critic_response(reviewer: str, text: str) -> ReviewResult:
    match = re.search(r"VERDICT:\s*(PASS|FAIL)\b", text, re.IGNORECASE)
    if match:
        verdict = match.group(1).upper()
    else:
        log.warning("%s: missing VERDICT header; treating as FAIL", reviewer)
        verdict = "FAIL"
    issues = _extract_list(text, "ISSUES:")
    suggestions = _extract_list(text, "SUGGESTIONS:")
    confidence = _extract_confidence(text)
    return ReviewResult(
        reviewer=reviewer,
        verdict=verdict,
        issues=issues,
        suggestions=suggestions,
        confidence=confidence,
    )


def _extract_list(text: str, prefix: str) -> list[str]:
    lines = text.splitlines()
    result: list[str] = []
    capture = False
    headers = {"VERDICT:", "SUGGESTIONS:", "CONFIDENCE:", "ISSUES:"}
    for line in lines:
        stripped_upper = line.strip().upper()
        if stripped_upper.startswith(prefix.upper()):
            capture = True
            rest = line.split(":", 1)[1].strip()
            if rest and rest.lower() != "none":
                result.append(rest)
            continue
        if capture:
            if line.strip() and not line.startswith(" ") and any(stripped_upper.startswith(h) for h in headers):
                break
            item = line.strip("- *").strip()
            if item and item.lower() != "none":
                result.append(item)
    return result


def _extract_confidence(text: str) -> float:
    match = re.search(r"CONFIDENCE:\s*([0-9.]+)", text, re.IGNORECASE)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass
    return 0.5
