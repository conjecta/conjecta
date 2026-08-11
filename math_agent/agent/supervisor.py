from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any

from math_agent.agent.prompts import (
    ATTACHMENT_EXTRACTION_SYSTEM,
    PRIOR_TRACE_CONTEXT_SYSTEM,
)
from math_agent.agent.react_state import (
    CONTEXT_PREAMBLE_MAX_CHARS,
    ProjectContext,
    ReActSolution,
    ReActTrace,
)
from math_agent.agent.supervisor_intake import (
    IntakeResult,
    SupervisorIntake,
    resolve_formal_verification,
)
from math_agent.agent.subagent import (
    SharedSubagentDeps,
    SubagentSpec,
    build_subagent,
    build_subagent_config,
    run_subagents_parallel,
)
from math_agent.config import AgentConfig, VerifierConfig
from math_agent.lean.failure_policy import lean_failure_policy
from math_agent.llm.base import LLMBackend, Message
from math_agent.llm.tracking import LLMCallCounter
from math_agent.types import EventCallback
from math_agent.web.attachments import ATTACHMENT_ONLY_PROBLEM

log = logging.getLogger("math_agent.agent.supervisor")

_PRIOR_ACTION_SUMMARY_MAX_CHARS = 800
_PRIOR_TRACE_SUMMARY_MAX_CHARS = 2_000
_CONVERSATION_CONTEXT_MAX_CHARS = 8_000
_CONVERSATION_CONTEXT_MAX_TURNS = 10
_INTENT_ROUTE_MESSAGES = {
    "clarify": "识别为追问，走轻量路径。",
    "extend": "继续当前话题。",
    "new_problem": "识别为新问题。",
}
# Pipeline bookkeeping shown only in the sticky status bar (Kimi-style: timeline
# stays reserved for thinking / tools / steps, not internal stage chatter).
_STATUS_BAR_ONLY = {"ui": "status_bar"}

# Escalation: a solve that requires formal verification but did not close the
# proof gets bounded replan rounds under larger budgets, with the previous
# round's Lean diagnostics injected as context. There is no user-facing mode
# for this — the requirement itself decides. Historical research runs remain
# readable via web/research_routes.py.
_LEAN_TOOLS = frozenset(
    {"formalize", "lean_check", "prove_by_lemmas", "tactic_search"}
)
_DIGEST_MAX_ENTRIES = 3
_DIGEST_ENTRY_CHARS = 600
_DIGEST_DRAFT_CHARS = 2000
_FAILURE_KIND_RE = re.compile(r"(?:failure_kind=|Failure kind:\s*)([a-z_]+)")
# Lean failure-kind routing (retry / repair / replan / abort) derives from the
# canonical mapping in math_agent/lean/failure_policy.py: ``lean_failure_policy``.
# Repeated one-shot formalize/lean_check repair failures beyond this count force
# the escalation round onto the deep-search route (tactic_search/prove_by_lemmas).
_DEEP_SEARCH_MIN_REPAIR_FAILURES = 2


def _lean_failure_digest(trace: ReActTrace) -> dict[str, Any]:
    """Structured summary of the failed round's Lean diagnostics.

    Returns ``{"text": str, "failure_kinds": list[str], "draft": str}`` or an
    empty dict when the round produced no Lean failures. ``text`` is the
    prompt-ready digest; ``failure_kinds`` drives escalation routing; ``draft``
    is the last failed Lean code, so the next round can repair it instead of
    regenerating from scratch.
    """
    failures: list[str] = []
    failure_kinds: list[str] = []
    draft = ""
    for turn in getattr(trace, "turns", []) or []:
        action = getattr(turn, "action", None)
        observation = getattr(turn, "observation", None)
        if observation is None or getattr(observation, "success", True):
            continue
        tool_name = getattr(action, "name", "") if action is not None else ""
        if tool_name not in _LEAN_TOOLS:
            continue
        output = str(getattr(observation, "output", "") or "").strip()
        if output:
            failures.append(f"[{tool_name}] {output[:_DIGEST_ENTRY_CHARS]}")
            failure_kinds.extend(_FAILURE_KIND_RE.findall(output))
        lean_code = str(getattr(observation, "lean_code", "") or "").strip()
        if lean_code:
            draft = lean_code
    if not failures:
        return {}
    body = "\n\n".join(failures[-_DIGEST_MAX_ENTRIES:])
    text = (
        "Previous proof attempt failed. Lean diagnostics from that round "
        "(avoid repeating these mistakes; reuse any lemmas it verified):\n"
        f"{body}"
    )
    if draft:
        text += (
            "\n\nLast failed Lean draft (repair it where possible instead of "
            f"rewriting from scratch):\n```lean\n{draft[:_DIGEST_DRAFT_CHARS]}\n```"
        )
    return {"text": text, "failure_kinds": failure_kinds, "draft": draft}


_ESCALATION_ROUTE_SYSTEM = (
    "You route failed Lean proof attempts to the cheapest effective recovery. "
    "Given the failure diagnostics, answer with exactly one word:\n"
    "- retry: infrastructure failure (timeout, Lean unavailable, import "
    "error); the same approach may simply work again.\n"
    "- repair: coding-level failure (syntax, unknown constant, type "
    "mismatch); the proof strategy is sound, only the draft needs fixing.\n"
    "- replan: the proof strategy itself failed (unsolved goals, wrong "
    "lemma decomposition); a new plan is needed."
)

_ESCALATION_ROUTE_HINTS = {
    "retry": (
        "Routing: the previous failure was infrastructure-related "
        "(timeout/Lean unavailable/import error), not a proof "
        "error. Retry the same proof approach."
    ),
    "repair": (
        "Routing: the previous failure was a Lean coding error "
        "(syntax/type/unknown constant), not necessarily a wrong "
        "strategy. Repair the failed draft instead of replanning "
        "from scratch."
    ),
    "replan": (
        "Routing: the previous attempt failed to close the proof "
        "goals. Re-plan the proof strategy (a different lemma "
        "decomposition or tactic route): use prove_by_lemmas with a "
        "new lemma decomposition, or tactic_search on the hardest "
        "remaining goal."
    ),
    "deep_search": (
        "Routing: repeated one-shot formalize/lean_check repair rounds "
        "did not close the proof. Switch to deep proof search: call "
        "tactic_search with the full theorem statement for a single-goal "
        "proof, or prove_by_lemmas with an explicit lemma decomposition "
        "for a multi-step proof. Do not keep repairing the same one-shot "
        "formalize draft."
    ),
}

# Strategy diversification for parallel deep-search routes: every route gets
# the shared deep-search hint above plus a route-specific lead-tool bias.
_DEEP_SEARCH_ROUTE_VARIANTS = (
    (
        "tactic_search",
        "This route leads with tactic_search on the full theorem statement; "
        "fall back to prove_by_lemmas only if the search stalls.",
    ),
    (
        "prove_by_lemmas",
        "This route leads with prove_by_lemmas using an explicit lemma "
        "decomposition; use tactic_search only on goals the decomposition "
        "cannot close.",
    ),
)


def _select_deep_search_route(
    results: Sequence[ReActSolution | None], problem: str
) -> ReActSolution:
    """Pick one outcome across parallel deep-search routes.

    A verified route wins (fewest verification_issues, then route order —
    ``min`` is stable). When nothing verified, the route with the fewest
    issues carries the round so the escalation loop keeps its serial
    "not verified -> next round or exit" semantics. Every route raising
    yields a best-effort placeholder whose empty trace ends the loop.
    """

    solved = [solution for solution in results if solution is not None]
    if not solved:
        return ReActSolution(
            problem=problem,
            turns=[],
            final_answer="",
            verification_status="best_effort",
            verification_issues=["all parallel deep-search routes failed"],
        )

    def _issue_count(solution: ReActSolution) -> int:
        return len(getattr(solution, "verification_issues", None) or [])

    verified = [s for s in solved if s.verification_status == "verified"]
    return min(verified or solved, key=_issue_count)


async def _judge_escalation_route(
    critic_llm: Any, digest: dict[str, Any], run_log: logging.Logger
) -> str | None:
    """Model-based escalation routing. Returns retry/repair/replan, or None
    on any judge failure so the caller falls back to the deterministic
    failure_kind rules."""
    if critic_llm is None:
        return None
    prompt = (
        "Failure kinds observed: "
        f"{', '.join(digest['failure_kinds']) or 'unknown'}\n\n"
        f"{digest['text'][:3000]}\n\n"
        "Answer with exactly one word: retry, repair, or replan."
    )
    try:
        response = await critic_llm.complete(
            [Message(role="user", content=prompt)],
            system=_ESCALATION_ROUTE_SYSTEM,
            temperature=0.0,
        )
    except Exception as exc:
        run_log.debug("Escalation route judge failed: %s", exc)
        return None
    verdict = (getattr(response, "text", "") or "").strip().lower().strip(".")
    # The judge may only pick retry/repair/replan; the deep_search route is
    # forced deterministically by the caller, never by the model.
    return verdict if verdict in {"retry", "repair", "replan"} else None


def build_first_user_content(augmented: str, attachments: list[dict] | None):
    """Text-only prompt stays a string; with attachments it becomes OpenAI content parts."""
    if not attachments:
        return augmented
    # Make it unambiguous that the attached images are the problem to solve.
    prompt = augmented
    if prompt == ATTACHMENT_ONLY_PROBLEM:
        prompt = prompt + "\n\n请依据下方附件中的内容进行解答。"
    return [{"type": "text", "text": prompt}, *attachments]


def _clip_context(text: str) -> str:
    value = (text or "").strip()
    if len(value) <= CONTEXT_PREAMBLE_MAX_CHARS:
        return value
    marker = "\n\n[... context truncated ...]"
    return value[: CONTEXT_PREAMBLE_MAX_CHARS - len(marker)] + marker


def _context_without_problem(problem: str, augmented: str) -> str:
    """Extract only the augmentation while keeping the user problem canonical."""
    value = augmented or ""
    if value == problem:
        return ""
    if problem and value.endswith(problem):
        return value[: -len(problem)].rstrip()
    return value.strip()


def _build_context_preamble(problem: str, augmented: str, intake: Any) -> str:
    label = intake.source_label or "referenced source"
    parts: list[str] = []
    if intake.source_digest:
        parts.append(f"Reference briefing ({label}):\n{intake.source_digest}")
    if intake.source_text:
        parts.append(f"Source excerpt ({label}):\n{_clip_context(intake.source_text)}")
    if intake.search_results and intake.search_query:
        parts.append(
            f"Web search results ({intake.search_query}):\n{intake.search_results}"
        )
    knowledge_context = _context_without_problem(problem, augmented)
    if knowledge_context:
        parts.append(knowledge_context)
    return _clip_context("\n\n".join(parts))


def _memory_log_item(memory: Any) -> dict[str, Any]:
    text = str(getattr(memory, "text", "") or "")
    return {
        "kind": getattr(memory, "kind", ""),
        "status": getattr(memory, "status", ""),
        "confidence": getattr(memory, "confidence", None),
        "score": getattr(memory, "score", None),
        "text": text[:120],
    }


def _conversation_context(turns: list[dict[str, str]] | None) -> str:
    """Render bounded prior chat as context, never as the mathematical target."""
    if not turns:
        return ""
    rendered: list[str] = []
    for turn in turns[-_CONVERSATION_CONTEXT_MAX_TURNS:]:
        role = "Assistant" if turn.get("role") == "assistant" else "User"
        text = str(turn.get("text") or "").strip()
        if text:
            rendered.append(f"{role}: {text}")
    if not rendered:
        return ""
    body = _bounded_text("\n\n".join(rendered), _CONVERSATION_CONTEXT_MAX_CHARS)
    return (
        "Prior conversation (context only; do not treat it as the current theorem):\n"
        f"{body}"
    )


def _intake_request(problem: str, conversation_context: str) -> str:
    if not conversation_context:
        return problem
    return _clip_context(
        f"{conversation_context}\n\nCurrent user request (authoritative target):\n{problem}"
    )


def _bounded_repr(value: Any, max_chars: int) -> str:
    try:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        rendered = str(value)
    return _bounded_text(rendered, max_chars)


def _bounded_text(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    marker = "... [truncated]"
    return value[: max_chars - len(marker)] + marker


def _bounded_prior_preamble_input(
    *,
    prior_problem: str,
    prior_summary: str,
    turns: list[dict[str, Any]],
    problem: str,
) -> str:
    payload: dict[str, Any] = {
        "prior_problem": prior_problem[:500],
        "prior_summary": prior_summary,
        "prior_turns": [],
        "new_problem": problem[:500],
    }
    for turn in turns[:15]:
        summary = {
            "step": turn.get("step_num"),
            "thought": str(turn.get("thought") or "")[:300],
            "action": _bounded_repr(
                turn.get("action", {}),
                _PRIOR_ACTION_SUMMARY_MAX_CHARS,
            ),
            "observation": _bounded_repr(turn.get("observation") or "", 300),
        }
        candidate = dict(payload)
        candidate["prior_turns"] = [*payload["prior_turns"], summary]
        encoded = json.dumps(candidate, ensure_ascii=False)
        if len(encoded) > CONTEXT_PREAMBLE_MAX_CHARS:
            break
        payload = candidate
    return json.dumps(payload, ensure_ascii=False)


class SupervisorAgent:
    """Top-level orchestrator.

    Step 1 deterministically handles ordinary new prompts and delegates only
    source/history enrichment to intake. ReAct handles every solve; research
    mode has been removed and raises ``ValueError`` at the entry points.
    """

    def __init__(
        self,
        llm: LLMBackend,
        critic_llm: LLMBackend,
        config,
        lean_runner=None,
        lean_codegen=None,
        knowledge_store=None,
        plan_memory=None,
        tool_registry=None,
        project_context: ProjectContext | None = None,
        project_store=None,
        user_memory_store=None,
        verifier_config: VerifierConfig | None = None,
    ) -> None:
        self.llm = llm
        self.critic_llm = critic_llm
        self.config = config
        self.lean_runner = lean_runner
        self.lean_codegen = lean_codegen
        self.knowledge_store = knowledge_store
        self.plan_memory = plan_memory
        self.tool_registry = tool_registry
        self.project_context = project_context or ProjectContext()
        self.project_store = project_store  # JSONL fallback for the evaluator
        self.user_memory_store = user_memory_store
        self.verifier_config = verifier_config or VerifierConfig()
        # The web factory creates one Supervisor per request, so this one-shot
        # deferred slot is never shared by concurrent web solves.
        self._post_solve_factory: Callable[[], Awaitable[None]] | None = None

        self._intake = SupervisorIntake(llm)

        from math_agent.agent.context_augmentor import ContextAugmentor

        self._augmentor = ContextAugmentor(
            knowledge_store=knowledge_store,
            plan_memory=plan_memory,
            user_memory_store=user_memory_store,
        )

    async def _extract_problem_from_attachments(
        self,
        problem: str,
        attachments: list[dict] | None,
        run_log: logging.Logger,
    ) -> str:
        """Extract a text problem from image attachments so ReAct works with text only."""
        if not attachments:
            return problem
        # Avoid extraction if the user already typed a concrete problem.
        text = (problem or "").strip()
        if text and text != ATTACHMENT_ONLY_PROBLEM:
            return problem

        try:
            messages = [
                Message(
                    role="user",
                    content=[
                        {
                            "type": "text",
                            "text": "Extract the mathematical problem from the attached image(s).",
                        },
                        *attachments,
                    ],
                )
            ]
            response = await self.llm.complete(
                messages,
                system=ATTACHMENT_EXTRACTION_SYSTEM,
                temperature=0.0,
            )
            extracted = (response.text or "").strip()
            if extracted and extracted.upper() != "UNREADABLE":
                run_log.info(
                    "Extracted problem from attachments: %d chars",
                    len(extracted),
                )
                return extracted
        except Exception as exc:
            run_log.warning("Attachment problem extraction failed: %s", exc)
        return problem

    async def solve(
        self,
        problem: str,
        on_event: EventCallback | None = None,
        session_log: logging.Logger | None = None,
        prior_trace: dict | None = None,
        session_id: str | None = None,
        mode: str = "auto",
        api_key: str | None = None,
        model: str | None = None,
        attachments: list[dict] | None = None,
        lean_executable: str | None = None,
        has_conversation_history: bool = False,
        conversation_history: list[dict[str, str]] | None = None,
        defer_post_solve: bool = False,
        human_decision: dict[str, Any] | None = None,
        require_formal_verification: bool | None = None,
    ) -> ReActSolution:
        run_log = session_log or log
        self._post_solve_factory = None
        self._last_conversation_history = conversation_history or []

        async def emit(event: dict[str, Any]) -> None:
            run_log.debug("supervisor event: %s", event.get("type"))
            if on_event:
                await on_event(event)

        if mode not in {"auto", "react"}:
            await emit({"type": "error", "message": f"Invalid solve mode: {mode}"})
            return ReActSolution(
                problem=problem, turns=[], final_answer=f"Invalid solve mode: {mode}"
            )

        # 1. Hydrate an exact resume before any source/history intake work.
        await emit(
            {
                "type": "stage_status",
                "stage": "analyzing",
                "message": "正在理解问题…",
                **_STATUS_BAR_ONLY,
            }
        )
        initial_trace: ReActTrace | None = None
        usable_prior_trace = prior_trace
        if prior_trace:
            try:
                hydrated = ReActTrace.from_checkpoint(
                    prior_trace,
                    project_context=self.project_context,
                )
            except ValueError as exc:
                usable_prior_trace = None
                run_log.warning("Checkpoint could not be hydrated: %s", exc)
            else:
                if hydrated.problem == problem:
                    initial_trace = hydrated

        if initial_trace is not None:
            intake = IntakeResult(
                strategy="react",
                intent="extend",
                require_formal_verification=resolve_formal_verification(
                    problem, self.verifier_config
                ),
            )
        else:
            # Convert image-only problems to text once, then solve with text only.
            extracted_problem = await self._extract_problem_from_attachments(
                problem, attachments, run_log
            )
            if extracted_problem != problem:
                problem = extracted_problem
                attachments = None
                await emit(
                    {
                        "type": "problem_extracted",
                        "problem": problem,
                    }
                )
            prior_conversation = _conversation_context(conversation_history)
            intake = await self._intake.analyze(
                _intake_request(problem, prior_conversation),
                has_history=has_conversation_history,
                proactive_search=False,
            )
        if require_formal_verification is not None:
            intake.require_formal_verification = require_formal_verification
        else:
            # The intake LLM may see prior conversation and source context. The
            # hard trust boundary is derived from this turn's authoritative
            # request and the configured policy unless the caller supplied an
            # explicit evaluation/runtime override above.
            intake.require_formal_verification = resolve_formal_verification(
                problem, self.verifier_config
            )
        intent = getattr(intake, "intent", None) or (
            "extend" if has_conversation_history else "new_problem"
        )
        light_path = intent == "clarify"
        strategy = "react"
        run_log.info(
            "Supervisor intake: mode=%s intent=%s light_path=%s source_label=%s "
            "digest_chars=%d source_text_chars=%d fetch_failed=%s",
            mode,
            intent,
            light_path,
            (intake.source_label or "")[:120],
            len(intake.source_digest),
            len(intake.source_text),
            intake.fetch_failed,
        )
        await emit(
            {
                "type": "stage_status",
                "stage": "analyzing",
                "message": _INTENT_ROUTE_MESSAGES.get(
                    intent, _INTENT_ROUTE_MESSAGES["extend"]
                ),
                **_STATUS_BAR_ONLY,
            }
        )
        if intake.fetch_failed:
            await emit(
                {
                    "type": "stage_status",
                    "stage": "warning",
                    "message": "未能抓取引用的来源，将主要依赖已有知识，结果可能不完整。",
                }
            )
        if intake.source_digest or intake.source_text:
            await emit(
                {
                    "type": "stage_status",
                    "stage": "learning",
                    "message": f"已准备来源摘要：{intake.source_label or '引用文献'}",
                    **_STATUS_BAR_ONLY,
                }
            )
        if intake.needs_search and not light_path:
            if intake.search_results:
                await emit(
                    {
                        "type": "stage_status",
                        "stage": "learning",
                        "message": f"已检索网络资料：{intake.search_query[:80]}",
                        **_STATUS_BAR_ONLY,
                    }
                )
            elif intake.search_query:
                await emit(
                    {
                        "type": "stage_status",
                        "stage": "warning",
                        "message": "需要联网核实，但当前搜索未返回结果；回答可能不够新。",
                    }
                )

        # 2. Attach project knowledge unless this is a clarifying follow-up.
        context_preamble = ""
        if initial_trace is not None:
            context_preamble = initial_trace.context_preamble
            run_log.info(
                "Resuming matching checkpoint at step %d with %d prior turns",
                initial_trace.next_step_num,
                len(initial_trace.turns),
            )
        elif light_path:
            run_log.debug("Clarify path: skipping knowledge augment")
            context_preamble = _conversation_context(conversation_history)
        else:
            await emit(
                {
                    "type": "stage_status",
                    "stage": "preparing_knowledge",
                    "message": "正在检索相关先验知识…",
                    **_STATUS_BAR_ONLY,
                }
            )
            project_id = self.project_context.project_id
            augmentation = await self._augmentor.augment(
                problem,
                project_id,
                session_id=session_id,
                on_event=emit,
                user_id=self.project_context.user_id,
            )
            augmented = augmentation.prompt
            memories_used = augmentation.memories_used
            if memories_used:
                run_log.info(
                    "ContextAugmentor selected %d memories: %s",
                    len(memories_used),
                    [_memory_log_item(memory) for memory in memories_used],
                )
            context_preamble = _build_context_preamble(problem, augmented, intake)
            prior_conversation = _conversation_context(conversation_history)
            if prior_conversation:
                context_preamble = _clip_context(
                    f"{prior_conversation}\n\n{context_preamble}".rstrip()
                )
            run_log.debug(
                "Context augmentation: %d bounded chars",
                len(context_preamble),
            )

        if usable_prior_trace and initial_trace is None:
            context_preamble = await self._maybe_inject_prior_trace(
                problem, context_preamble, usable_prior_trace, emit, run_log
            )

        react_config: AgentConfig | None = None
        if light_path:
            react_config = build_subagent_config(
                self.config,
                SubagentSpec(
                    max_steps=int(self.config.clarify_max_steps),
                    reviewers_enabled=(),
                    hitl_enabled=False,
                    memory_integration_enabled=False,
                    event_scope={"intent": "clarify"},
                ),
            )

        # 3. Execute the solve.
        solution, trace = await self._run_react(
            problem,
            emit,
            run_log,
            session_id=session_id,
            attachments=attachments,
            config=react_config,
            context_preamble=context_preamble,
            initial_trace=initial_trace,
            require_formal_verification=intake.require_formal_verification,
            **({"human_decision": human_decision} if human_decision else {}),
        )

        # 3b. Escalation rounds. A solve that must produce a Lean-verified
        # proof but did not close it gets bounded retries under larger budgets,
        # with the previous round's Lean diagnostics injected as context
        # (Hilbert-style reasoner<->prover feedback). No mode switch is
        # involved: the formal requirement alone decides.
        if (
            intake.require_formal_verification
            and not light_path
            and self.lean_runner is not None
        ):
            rounds_left = max(0, int(self.config.escalation_replan_rounds))
            escalated_config = replace(
                react_config or self.config,
                max_react_steps=self.config.escalation_max_react_steps,
                max_tool_calls=self.config.escalation_max_tool_calls,
                planning_enabled=True,
                force_review=True,
            )
            while rounds_left > 0 and solution.verification_status != "verified":
                digest = _lean_failure_digest(trace)
                if not digest:
                    break
                rounds_left -= 1
                # Deterministic deep-search routing: once Lean proof attempts
                # (one-shot formalize/lean_check or structured
                # tactic_search/prove_by_lemmas) have failed repeatedly on
                # this problem, stop trusting the critic judge and force the
                # escalated round onto tactic_search / prove_by_lemmas with
                # enlarged deep-search budgets. Counting only the one-shot
                # tools made the route unreachable whenever the actor jumped
                # straight to the structured tools on hard problems.
                repair_failures = sum(
                    1
                    for turn in getattr(trace, "turns", []) or []
                    if getattr(getattr(turn, "action", None), "name", "")
                    in {"formalize", "lean_check", "tactic_search", "prove_by_lemmas"}
                    and not getattr(getattr(turn, "observation", None), "success", True)
                )
                force_deep_search = repair_failures >= _DEEP_SEARCH_MIN_REPAIR_FAILURES
                route: str | None = None
                if force_deep_search:
                    route = "deep_search"
                else:
                    route = await _judge_escalation_route(
                        self.critic_llm, digest, run_log
                    )
                if route is None:
                    kinds = set(digest["failure_kinds"])
                    policies = {lean_failure_policy(kind) for kind in kinds}
                    if kinds and policies <= {"retry"}:
                        route = "retry"
                    elif "abort" in policies and "repair" not in policies:
                        # Unrecoverable failure (e.g. termination): escalation
                        # rounds cannot fix it; stop instead of burning budget.
                        run_log.info(
                            "Formal escalation aborted: failure_kinds=%s are terminal",
                            sorted(kinds),
                        )
                        break
                    elif "repair" in policies:
                        route = "repair"
                    else:
                        route = "replan"
                round_config = escalated_config
                if route == "deep_search":
                    round_config = replace(
                        escalated_config,
                        max_wall_seconds=float(self.config.deep_search_wall_seconds),
                        tactic_search_max_attempts=int(
                            self.config.deep_search_max_attempts
                        ),
                        tactic_search_wall_seconds=float(
                            self.config.deep_search_wall_seconds
                        ),
                    )
                hint = _ESCALATION_ROUTE_HINTS[route]
                run_log.info(
                    "Formal escalation round (%s left after this), route=%s failure_kinds=%s",
                    rounds_left,
                    route,
                    sorted(set(digest["failure_kinds"])),
                )
                await emit(
                    {
                        "type": "stage_status",
                        "stage": "solving",
                        "message": "形式化未闭合，携带诊断信息重新规划证明路线…",
                        **_STATUS_BAR_ONLY,
                    }
                )
                context_preamble = _clip_context(
                    f"{context_preamble}\n\n{digest['text']}\n\n{hint}"
                )
                if (
                    route == "deep_search"
                    and int(self.config.deep_search_parallel_routes) > 1
                ):
                    solution, trace = await self._run_deep_search_routes(
                        problem,
                        emit,
                        run_log,
                        session_id=session_id,
                        config=round_config,
                        context_preamble=context_preamble,
                    )
                else:
                    solution, trace = await self._run_react(
                        problem,
                        emit,
                        run_log,
                        session_id=session_id,
                        config=round_config,
                        context_preamble=context_preamble,
                        require_formal_verification=True,
                        planning=True,
                    )

        if not light_path and self.config.memory_consolidation_enabled:
            post_solve_emit = None if defer_post_solve else emit

            def post_solve_factory() -> Awaitable[None]:
                return self._run_post_solve(
                    strategy=strategy,
                    solution=solution,
                    trace=trace,
                    problem=problem,
                    session_id=session_id or "",
                    emit=post_solve_emit,
                    run_log=run_log,
                )

            if defer_post_solve:
                self._post_solve_factory = post_solve_factory
            else:
                await post_solve_factory()

        return solution

    def take_post_solve(self) -> Awaitable[None] | None:
        """Return deferred post-solve work once, if this solve produced any."""
        factory = self._post_solve_factory
        self._post_solve_factory = None
        return factory() if factory is not None else None

    async def _run_post_solve(
        self,
        *,
        strategy: str,
        solution: ReActSolution,
        trace: ReActTrace,
        problem: str,
        session_id: str = "",
        emit: EventCallback | None,
        run_log: logging.Logger,
    ) -> None:
        if emit is not None:
            await emit(
                {
                    "type": "stage_status",
                    "stage": "learning",
                    "message": "正在提炼可复用知识…",
                    **_STATUS_BAR_ONLY,
                }
            )
        try:
            from math_agent.agent.memory_consolidation import MemoryConsolidator

            consolidator = MemoryConsolidator(
                llm=self.critic_llm,
                knowledge_store=self.knowledge_store,
                plan_memory=self.plan_memory,
            )
            await consolidator.consolidate(trace, solution)
        except Exception as exc:
            run_log.warning("Memory consolidation failed: %s", exc, exc_info=True)
            self._record_consolidation_issue(
                solution, f"Memory consolidation failed: {exc}"
            )

        if (
            self.user_memory_store is not None
            and self.config.memory_consolidation_enabled
        ):
            try:
                from math_agent.agent.user_memory import UserMemoryConsolidator

                user_consolidator = UserMemoryConsolidator(
                    llm=self.critic_llm,
                    store=self.user_memory_store,
                )
                await user_consolidator.consolidate(
                    trace,
                    solution,
                    conversation_history=getattr(self, "_last_conversation_history", [])
                    or [],
                    source_session_id=session_id,
                )
            except Exception as exc:
                run_log.warning(
                    "User memory consolidation failed: %s", exc, exc_info=True
                )
                self._record_consolidation_issue(
                    solution, f"User memory consolidation failed: {exc}"
                )

        if self.knowledge_store is not None or self.project_store is not None:
            await self._maybe_evaluate(
                strategy=strategy,
                solution=solution,
                problem=problem,
                emit=emit,
                run_log=run_log,
            )

    @staticmethod
    def _record_consolidation_issue(solution: ReActSolution, message: str) -> None:
        """Surface a consolidation failure on the solution without failing the solve."""
        issues = getattr(solution, "verification_issues", None)
        if isinstance(issues, list):
            issues.append(message)

    async def _run_react(
        self,
        problem: str,
        emit: EventCallback,
        run_log: logging.Logger,
        session_id: str | None = None,
        attachments: list[dict] | None = None,
        config: AgentConfig | None = None,
        context_preamble: str = "",
        initial_trace: ReActTrace | None = None,
        require_formal_verification: bool = False,
        human_decision: dict[str, Any] | None = None,
        planning: bool | None = None,
    ) -> tuple[ReActSolution, ReActTrace]:
        await emit(
            {
                "type": "stage_status",
                "stage": "thinking",
                "message": "开始推理…",
                **_STATUS_BAR_ONLY,
            }
        )
        from math_agent.agent.react_agent import ReActAgent

        if config is not None:
            agent = build_subagent(
                SharedSubagentDeps(
                    llm=self.llm,
                    critic_llm=self.critic_llm,
                    config=config,
                    lean_runner=self.lean_runner,
                    lean_codegen=self.lean_codegen,
                    project_context=self.project_context,
                    tool_registry=self.tool_registry,
                ),
                SubagentSpec(
                    hitl_enabled=config.hitl.enabled,
                    memory_integration_enabled=config.memory_consolidation_enabled,
                    planning=planning,
                ),
            )
        else:
            agent = ReActAgent(
                llm=self.llm,
                critic_llm=self.critic_llm,
                config=self.config,
                lean_runner=self.lean_runner,
                lean_codegen=self.lean_codegen,
                project_context=self.project_context,
                tool_registry=self.tool_registry,
                consolidator=None,
            )

        def _on_checkpoint(snapshot: dict) -> None:
            if self.project_store and session_id:
                snapshot["session_id"] = session_id
                snapshot["project_id"] = self.project_context.project_id
                try:
                    self.project_store.write_checkpoint(snapshot)
                    import asyncio

                    asyncio.create_task(
                        emit(
                            {
                                "type": "checkpoint",
                                "checkpoint_id": session_id,
                                "resumable": True,
                                "reason": "ReAct step completed.",
                            }
                        )
                    )
                except Exception as exc:
                    run_log.debug("Checkpoint write failed: %s", exc)

        trace = initial_trace or ReActTrace(
            problem=problem,
            current_goal=problem,
            project_context=self.project_context,
            context_preamble=context_preamble,
        )
        solution = await agent.solve(
            problem,
            on_event=emit,
            session_log=run_log,
            on_checkpoint=_on_checkpoint,
            attachments=attachments,
            require_formal_verification=require_formal_verification,
            initial_trace=trace,
            human_decision=human_decision,
            session_id=session_id,
        )
        trace = solution.trace
        if trace is None:
            trace = ReActTrace(
                problem=problem,
                current_goal=problem,
                project_context=self.project_context,
                context_preamble=context_preamble,
            )
            trace.turns = solution.turns
        return solution, trace

    async def _run_deep_search_routes(
        self,
        problem: str,
        emit: EventCallback,
        run_log: logging.Logger,
        *,
        session_id: str | None = None,
        config: AgentConfig,
        context_preamble: str,
    ) -> tuple[ReActSolution, ReActTrace]:
        """Run strategy-diversified deep-search routes concurrently.

        Routes share the per-problem LLM call counter and each route keeps
        the same deep-search wall deadline, so the round's total wall time is
        not multiplied by the route count. Resumable checkpoints are skipped
        here: concurrent workers would race on the same checkpoint file.
        """
        route_count = max(2, int(self.config.deep_search_parallel_routes))
        shared = SharedSubagentDeps(
            llm=self.llm,
            critic_llm=self.critic_llm,
            config=config,
            lean_runner=self.lean_runner,
            lean_codegen=self.lean_codegen,
            project_context=self.project_context,
            tool_registry=self.tool_registry,
            llm_call_counter=LLMCallCounter(),
        )
        specs: list[SubagentSpec] = []
        route_kwargs: list[dict[str, Any]] = []
        for index in range(route_count):
            name, bias = _DEEP_SEARCH_ROUTE_VARIANTS[
                index % len(_DEEP_SEARCH_ROUTE_VARIANTS)
            ]
            preamble = _clip_context(f"{context_preamble}\n\nRoute strategy: {bias}")
            specs.append(
                SubagentSpec(
                    hitl_enabled=config.hitl.enabled,
                    memory_integration_enabled=config.memory_consolidation_enabled,
                    planning=True,
                    event_scope={"intent": "deep_search", "route": name},
                )
            )
            route_kwargs.append(
                {
                    "initial_trace": ReActTrace(
                        problem=problem,
                        current_goal=problem,
                        project_context=self.project_context,
                        context_preamble=preamble,
                    )
                }
            )
        await emit(
            {
                "type": "stage_status",
                "stage": "thinking",
                "message": "开始推理…",
                **_STATUS_BAR_ONLY,
            }
        )
        run_log.info(
            "Deep-search escalation round: %d parallel routes", route_count
        )
        results = await run_subagents_parallel(
            problem,
            specs,
            shared,
            max_parallel=route_count,
            solve_kwargs={
                "on_event": emit,
                "session_log": run_log,
                "require_formal_verification": True,
                "session_id": session_id,
            },
            per_route_kwargs=route_kwargs,
        )
        solution = _select_deep_search_route(results, problem)
        run_log.info(
            "Deep-search round outcome: status=%s verified_routes=%d/%d",
            solution.verification_status,
            sum(
                1
                for r in results
                if r is not None and r.verification_status == "verified"
            ),
            len(results),
        )
        trace = solution.trace
        if trace is None:
            trace = ReActTrace(
                problem=problem,
                current_goal=problem,
                project_context=self.project_context,
                context_preamble=context_preamble,
            )
            trace.turns = solution.turns
        return solution, trace

    async def _maybe_inject_prior_trace(
        self,
        problem: str,
        augmented: str,
        prior_trace: dict,
        emit: EventCallback,
        run_log: logging.Logger,
    ) -> str:
        """Judge prior-trace relevance and prepend a preamble in one critic call."""
        turns = prior_trace.get("turns") or []
        if not turns:
            # No prior work to build on: skip the LLM round-trip entirely.
            return augmented
        strategy = _bounded_text(str(prior_trace.get("strategy", "unknown")), 200)
        raw_completed_stages = prior_trace.get("completed_stages") or []
        completed_stages = (
            raw_completed_stages if isinstance(raw_completed_stages, list) else []
        )

        # Build a compact summary of the prior session
        prior_summary = f"Strategy: {strategy}\n"
        if completed_stages:
            rendered_stages = ", ".join(
                _bounded_text(str(stage), 80) for stage in completed_stages[:20]
            )
            prior_summary += f"Completed stages: {rendered_stages}\n"
        prior_summary += f"Steps completed: {len(turns)}\n"
        if turns:
            first = turns[0]
            last = turns[-1]
            prior_summary += (
                "First step: "
                f"{_bounded_repr(first.get('thought') or first.get('action') or '', 200)}\n"
            )
            if len(turns) > 1:
                prior_summary += (
                    "Last step: "
                    f"{_bounded_repr(last.get('thought') or last.get('action') or '', 200)}\n"
                )
        prior_summary = _bounded_text(
            prior_summary,
            _PRIOR_TRACE_SUMMARY_MAX_CHARS,
        )
        prior_problem = prior_trace.get("problem", "")

        # One call: relevance verdict plus (when related) the preamble text.
        context_input = _bounded_prior_preamble_input(
            prior_problem=prior_problem,
            prior_summary=prior_summary,
            turns=turns,
            problem=problem,
        )
        try:
            response = await self.critic_llm.complete(
                [Message(role="user", content=context_input)],
                system=PRIOR_TRACE_CONTEXT_SYSTEM,
                temperature=0.0,
            )
            raw = response.text.strip()
            if raw.startswith("```"):
                lines = raw.splitlines()
                raw = "\n".join(
                    lines[1:-1] if lines[-1].startswith("```") else lines[1:]
                ).strip()
            data = json.loads(raw)
            related = bool(data.get("related"))
            preamble = str(data.get("preamble") or "").strip()
            run_log.info(
                "Prior trace relevance: related=%s reason=%s",
                related,
                str(data.get("reason", ""))[:120],
            )
        except Exception as exc:
            run_log.debug("Prior trace context synthesis failed: %s", exc)
            return augmented

        if not related or not preamble:
            return augmented

        await emit(
            {
                "type": "stage_status",
                "stage": "preparing_knowledge",
                "message": "正在并入先前会话上下文…",
                **_STATUS_BAR_ONLY,
            }
        )

        return _clip_context(
            f"Context from prior interrupted session:\n{preamble}\n\n{augmented}"
        )

    async def _maybe_evaluate(
        self,
        strategy: str,
        solution: ReActSolution,
        problem: str,
        emit: EventCallback | None,
        run_log: logging.Logger,
    ) -> None:
        """Supervisor decides whether to run the knowledge evaluator this session."""
        project_id = self.project_context.project_id
        if not project_id:
            return

        # Run curation only for substantial react sessions.
        run_eval = len(solution.turns) >= 5
        if not run_eval:
            run_log.debug(
                "Supervisor skipped knowledge evaluation (turns=%d)",
                len(solution.turns),
            )
            return

        run_log.info(
            "Supervisor invoking knowledge evaluator (turns=%d)", len(solution.turns)
        )
        try:
            from math_agent.agent.knowledge_evaluator import KnowledgeEvaluator

            evaluator = KnowledgeEvaluator(
                llm=self.critic_llm,
                knowledge_store=self.knowledge_store,
                project_store=self.project_store,
            )
            await evaluator.evaluate(project_id, context_hint=problem, on_event=emit)
        except Exception as exc:
            run_log.warning("Knowledge evaluation failed: %s", exc)
