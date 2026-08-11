from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Awaitable, Callable, TypeVar
from uuid import uuid4

from math_agent.agent.action_parser import (
    ActionParseError,
    parse_action,
    parse_action_with_repair,
)
from math_agent.agent.conclude_gate import ConcludeGate
from math_agent.agent.formal_evidence import (
    FORMAL_ACTIONS,
    attach_formal_evidence,
    evidence_id_from_observation,
)
from math_agent.agent.human_interaction import (
    create_interaction,
    hitl_should_pause,
    matching_decision,
    pause_for_human,
    record_decision,
)
from math_agent.agent.hooks import run_post_tool_hooks, run_pre_tool_hooks
from math_agent.agent.memory_consolidation import MemoryConsolidator
from math_agent.agent.prompt_difficulty import classify_easy_prompt
from math_agent.agent.prompts import (
    build_react_native_system_prompt,
    build_react_system_prompt,
)
from math_agent.agent.react_state import (
    Action,
    ProjectContext,
    ReActSolution,
    ReActTrace,
    ReActTurn,
    ReviewResult,
    ToolObservation,
    best_effort_answer,
    normalize_plan_items,
)
from math_agent.llm.retry import is_context_overflow_error
from math_agent.llm.tracking import CallCountingBackend, LLMCallCounter
from math_agent.llm.utils import confidence_from_mean_logprob
from math_agent.agent.reviewers import (
    CompletenessReviewer,
    CriticReviewer,
    FormalReviewer,
    KnowledgeReviewer,
    Reviewer,
    StatementFidelityReviewer,
)
from math_agent.agent.state import ReasoningState, ReasoningStep, StepType
from math_agent.agent.research_artifacts import _safe_component
from math_agent.agent.tools import ToolContext, ToolRegistry
from math_agent.billing.models import ToolCall
from math_agent.config import AgentConfig, LLMConfig
from math_agent.llm.base import LLMBackend, Message
from math_agent.types import EventCallback
from math_agent.agent.verification import (
    legacy_label,
    outcome_best_effort,
)
from math_agent.verification import (
    GoalEvaluator,
    GoalRun,
    SuccessCriteria,
)

log = logging.getLogger("math_agent.agent")

_T = TypeVar("_T")

_SPECIAL_ACTION_ARGUMENTS = {
    "conclude": "answer",
    "set_goal": "goal",
    "think": "text",
}
_TOOL_ACTION_ALIASES = {
    "search_web": "search",
    "web_fetch": "fetch_url",
}
_NON_CONSUMING_TOOL_ERRORS = {
    "unknown_action",
    "invalid_action_args",
    "identical_action_limit",
    "tool_call_budget_exhausted",
    # A pre-tool hook vetoed the call before the tool ran.
    "blocked_by_hook",
    # These do not represent a consumed conclusion attempt or tool call.
    "missing_formal_evidence",
    "missing_diagram",
    "human_rejected",
}

# Async callback injected into ToolContext.event_callback while a tool runs
# under _execute_with_heartbeat, so long tools can surface user-facing
# progress lines as tool_progress events without changing _execute_action's
# signature (tests monkeypatch it with two-arg doubles).
_TOOL_EVENT_CALLBACK: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "tool_event_callback", default=None
)

# Per-solve logger injected into ToolContext.session_log while a solve runs,
# so long tools (prove_by_lemmas) write diagnostics into the session log.
# Set per solve like _TOOL_EVENT_CALLBACK to avoid changing _execute_action's
# signature (tests monkeypatch it with two-arg doubles).
_TOOL_SESSION_LOG: contextvars.ContextVar[Any] = contextvars.ContextVar(
    "tool_session_log", default=None
)

# Keep in sync with the default ``max_turns`` of ``ReActTrace.context_window``;
# turns older than this window are incrementally compacted into a summary.
_CONTEXT_WINDOW_MAX_TURNS = 10


def _estimate_context_tokens(text: str) -> int:
    """chars/4 token estimate, matching the heuristic used in llm/openai.py."""
    return max(1, len(text) // 4)

_FIGURE_EMBED_RE = re.compile(
    r"!\[[^\]]*\]\([^)\s]+(?:/figures/|\.(?:png|jpe?g|gif|svg))[^)\s]*\)",
    re.IGNORECASE,
)


class ReActAgent:
    def __init__(
        self,
        llm: LLMBackend,
        critic_llm: LLMBackend,
        config: AgentConfig,
        lean_runner=None,
        lean_codegen=None,
        premise_retriever=None,
        knowledge_store=None,
        project_context: ProjectContext | None = None,
        consolidator: MemoryConsolidator | None = None,
        tool_registry: ToolRegistry | None = None,
        allowed_tools: tuple[str, ...] | None = None,
        llm_call_counter: LLMCallCounter | None = None,
    ) -> None:
        # Shared per-solve LLM call counter (llm.max_calls_per_problem budget):
        # actor, critic, codegen, and tool-side tactic generation all funnel
        # through wrappers that increment this one counter. When the caller
        # passes the same backend for actor and critic, share one wrapper so
        # `self.llm is self.critic_llm` keeps its original meaning. Parallel
        # subagent routes inject a shared counter so the budget caps the whole
        # batch instead of multiplying per route.
        self._llm_call_counter = llm_call_counter or LLMCallCounter()
        wrapped_llm = CallCountingBackend(llm, self._llm_call_counter)
        self.llm = wrapped_llm
        self.critic_llm = (
            wrapped_llm
            if critic_llm is llm
            else CallCountingBackend(critic_llm, self._llm_call_counter)
        )
        if lean_codegen is not None:
            codegen_llm = getattr(lean_codegen, "llm", None)
            if codegen_llm is llm:
                lean_codegen.llm = self.llm
            elif codegen_llm is critic_llm:
                lean_codegen.llm = self.critic_llm
            elif codegen_llm is not None:
                lean_codegen.llm = CallCountingBackend(
                    codegen_llm, self._llm_call_counter
                )
        self.config = config
        self.project_context = project_context or ProjectContext()
        self.consolidator = consolidator
        self.allowed_tools = (
            frozenset(allowed_tools) if allowed_tools is not None else None
        )
        self.event_scope: dict[str, Any] = {}
        self._figure_dir: Path | None = None
        self._figure_url_prefix: str | None = None
        # Cached easy/hard verdict for the current solve (None = not classified).
        self._easy_verdict: bool | None = None
        self.tools = tool_registry or ToolRegistry(
            enabled_tools=config.tools,
            lean_runner=lean_runner,
            lean_codegen=lean_codegen,
            premise_retriever=premise_retriever,
            llm=self.llm,
            knowledge_store=knowledge_store,
            agent_config=config,
        )
        self.reviewers: list[Reviewer] = []
        enabled = set(config.reviewers_enabled or [])
        if "critic" in enabled:
            self.reviewers.append(CriticReviewer(llm=self.critic_llm))
        if "formal" in enabled:
            self.reviewers.append(FormalReviewer(lean_codegen=lean_codegen))
        if "knowledge" in enabled:
            self.reviewers.append(KnowledgeReviewer(knowledge_store=knowledge_store))
        if "fidelity" in enabled:
            self.reviewers.append(StatementFidelityReviewer(llm=self.critic_llm))
        if "completeness" in enabled:
            self.reviewers.append(CompletenessReviewer(llm=self.critic_llm))

    def _prepare_figure_dir(
        self, session_id: str | None
    ) -> tuple[Path | None, str | None]:
        """Set up the per-solve figure directory for the plot_figure tool.

        Returns ``(figure_dir, url_prefix)``; both are None when the tool is
        disabled or the directory cannot be created.  The URL prefix is only
        available for web solves (a real session id); CLI solves fall back to
        referencing the local file path.
        """
        if "plot_figure" not in (self.config.tools or []):
            return None, None
        component = _safe_component(session_id or f"cli-{uuid4().hex[:8]}")
        figure_dir = Path(self.config.artifact_root) / component / "figures"
        try:
            figure_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            log.warning("Figure directory unavailable (%s); plots disabled", exc)
            return None, None
        url_prefix = f"/api/solve/figures/{component}" if session_id else None
        return figure_dir, url_prefix

    def _registered_tool_name(self, action_name: str) -> str | None:
        canonical_name = _TOOL_ACTION_ALIASES.get(action_name, action_name)
        if canonical_name in self.tools.available and (
            self.allowed_tools is None or canonical_name in self.allowed_tools
        ):
            return canonical_name
        return None

    def _validate_action(self, action: Action) -> tuple[str, str] | None:
        if action.name == "update_plan":
            if not isinstance(action.args.get("items"), list):
                return (
                    "invalid_action_args",
                    'Action update_plan requires an "items" list argument.',
                )
            return None
        required_arg = _SPECIAL_ACTION_ARGUMENTS.get(action.name)
        if required_arg is None:
            tool_name = self._registered_tool_name(action.name)
            if tool_name is None:
                return (
                    "unknown_action",
                    f"Unknown action: {action.name}",
                )
            tool_args = self.tools.argument_map(tool_name)
            if tool_args is None:
                return (
                    "unknown_action",
                    f"Unknown action: {action.name}",
                )
            # The first listed argument is required; later entries are optional
            # but participate in fingerprinting.
            if isinstance(tool_args, tuple) and not tool_args:
                return None
            required_arg = tool_args[0] if isinstance(tool_args, tuple) else tool_args

        value = action.args.get(required_arg)
        if not isinstance(value, str) or not value.strip():
            return (
                "invalid_action_args",
                (
                    f"Action {action.name} requires a non-empty string "
                    f"argument named {required_arg}."
                ),
            )
        return None

    async def solve(
        self,
        problem: str,
        on_event: EventCallback | None = None,
        session_log: logging.Logger | None = None,
        on_checkpoint: Callable[[dict[str, Any]], None] | None = None,
        plan_text: str = "",
        initial_goal: str = "",
        attachments: list[dict] | None = None,
        require_formal_verification: bool = False,
        initial_trace: ReActTrace | None = None,
        human_decision: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> ReActSolution:
        self._easy_verdict = None
        self._figure_dir, self._figure_url_prefix = self._prepare_figure_dir(
            session_id
        )
        if initial_trace is None:
            trace = ReActTrace(
                problem=problem,
                current_goal=initial_goal or problem,
                project_context=self.project_context,
                plan_text=plan_text,
            )
        else:
            trace = initial_trace
            if not trace.current_goal:
                trace.current_goal = initial_goal or problem
            if plan_text and not trace.plan_text:
                trace.plan_text = plan_text
        root_goal = trace.proof_graph.ensure_root(problem)
        if trace.current_goal and trace.current_goal != problem:
            active = trace.proof_graph.upsert_goal(
                trace.current_goal,
                activate=True,
            )
            if active.id not in root_goal.depends_on:
                root_goal.depends_on.append(active.id)
        run_log = session_log or log
        _TOOL_SESSION_LOG.set(run_log)
        goal_run = GoalRun.new(
            problem=problem,
            criteria=SuccessCriteria(
                require_final_answer=True,
                require_formal_verification=require_formal_verification,
                min_report_count=len(self.reviewers),
                required_report_sources=tuple(
                    reviewer.name for reviewer in self.reviewers
                ),
                review_vote_margin=float(
                    getattr(self.config, "review_vote_margin", 0.0)
                ),
            ),
        )
        goal_evaluator = GoalEvaluator()

        async def emit(event: dict[str, Any]) -> None:
            run_log.debug("event: %s", event.get("type"))
            if on_event:
                await on_event(event)

        # Show the root goal in the live DAG panel from the very start.
        await self._emit_proof_graph_if_changed(trace, emit)

        final_answer = ""
        candidate_answer = ""
        accepted_formal_evidence_id = ""
        outcome = outcome_best_effort()
        verification_status = legacy_label(outcome)
        verification_issues: list[str] = []
        terminated_without_synthesis = False
        wall_time_exhausted = False
        llm_budget_exhausted = False
        # Per-problem LLM call budget (llm.max_calls_per_problem), enforced
        # alongside the wall-clock deadline below. The backend carries the
        # configured value (see llm.factory); tests with fake backends fall
        # back to the dataclass default.
        max_llm_calls = int(
            getattr(self.llm, "max_calls_per_problem", None)
            or LLMConfig().max_calls_per_problem
        )
        self._llm_call_counter.reset()
        max_wall_seconds = max(0.0, float(self.config.max_wall_seconds))
        deadline = asyncio.get_running_loop().time() + max_wall_seconds
        conclude_gate = ConcludeGate(
            self,
            run_log=run_log,
            emit=emit,
            on_checkpoint=on_checkpoint,
            deadline=deadline,
            goal_run=goal_run,
            goal_evaluator=goal_evaluator,
            require_formal_verification=require_formal_verification,
        )

        await self._maybe_plan(trace, run_log, emit, deadline)
        await self._maybe_claim_check(trace, run_log, emit, deadline)

        if trace.pending_interaction:
            matched = matching_decision(trace, human_decision)
            if matched is None:
                pause_for_human(trace, trace.pending_interaction)
            pending, decision = matched
            kind = str(pending.get("kind") or "")
            details = (
                pending.get("details")
                if isinstance(pending.get("details"), dict)
                else {}
            )
            if kind == "reviewer_block" and decision["decision"] == "approve":
                record_decision(trace, decision)
                if on_checkpoint:
                    on_checkpoint(_trace_snapshot(trace, strategy="react"))
                trace.tool_stats = self.tools.tool_stats
                return ReActSolution(
                    problem=problem,
                    turns=trace.turns,
                    final_answer=str(
                        details.get("candidate_answer") or best_effort_answer(trace)
                    ),
                    verification_status=legacy_label(outcome_best_effort()),
                    verification_issues=[
                        str(item) for item in (details.get("issues") or [])
                    ],
                    trace=trace,
                    llm_call_count=self._llm_call_counter.calls,
                    verification_outcome=outcome_best_effort(),
                )
            if kind == "tool_approval":
                raw_action = (
                    details.get("action") if isinstance(details, dict) else None
                )
                if decision["decision"] == "edit" and isinstance(
                    decision.get("edited_action"), dict
                ):
                    raw_action = decision["edited_action"]
                if not isinstance(raw_action, dict):
                    raise ValueError("Pending tool approval is missing its action.")
                action = Action(
                    name=str(raw_action.get("name") or ""),
                    args=(
                        dict(raw_action.get("args") or {})
                        if isinstance(raw_action.get("args"), dict)
                        else {}
                    ),
                )
                validation_error = self._validate_action(action)
                if validation_error is not None:
                    raise ValueError(validation_error[1])
                step_num = max(
                    trace.next_step_num,
                    int(details.get("step_num") or trace.next_step_num),
                )
                if decision["decision"] in {"approve", "edit"}:
                    current_tool_calls = trace.budget_consumption.get("tool_calls", 0)
                    if current_tool_calls >= self.config.max_tool_calls:
                        observation = ToolObservation(
                            success=False,
                            output=(
                                f"Tool-call budget exhausted after {current_tool_calls} "
                                "consumed calls; human-approved action was not executed."
                            ),
                            error="tool_call_budget_exhausted",
                        )
                    else:
                        await emit(
                            {
                                "type": "tool_start",
                                "step_num": step_num,
                                "tool": action.name,
                                "args_preview": _preview_action_args(
                                    action.args,
                                    limit=2000 if action.name == "compute" else 240,
                                ),
                                "human_approved": True,
                            }
                        )
                        observation = await _await_phase(
                            self._execute_with_heartbeat(action, trace, emit, step_num),
                            deadline=deadline,
                            run_log=run_log,
                            phase="tool_execution",
                            model_role="main",
                        )
                        trace.budget_consumption["tool_calls"] = current_tool_calls + 1
                        observation = attach_formal_evidence(
                            action,
                            observation,
                            target_claim=trace.current_goal or trace.problem,
                        )
                        if action.name in FORMAL_ACTIONS:
                            trace.proof_graph.record_formal_attempt(
                                success=observation.success,
                                evidence_id=evidence_id_from_observation(observation),
                                issue=observation.error
                                or (
                                    ""
                                    if observation.success
                                    else observation.output[:500]
                                ),
                            )
                        await emit(
                            {
                                "type": "tool_done",
                                "step_num": step_num,
                                "tool": action.name,
                                "success": observation.success,
                                "output": observation.output[:2000],
                                "error": observation.error,
                            }
                        )
                else:
                    feedback = decision.get("feedback") or "No reason supplied."
                    observation = ToolObservation(
                        success=decision["decision"] == "respond",
                        output=f"Human {decision['decision']}: {feedback}",
                        error=(
                            None
                            if decision["decision"] == "respond"
                            else "human_rejected"
                        ),
                    )
                turn = ReActTurn(
                    thought="Apply the recorded human decision to the pending action.",
                    action=action,
                    observation=observation,
                    step_num=step_num,
                )
                trace.turns.append(turn)
                trace.next_step_num = step_num + 1
                await self._emit_turn(turn, emit, trace)
            elif kind == "reviewer_block":
                feedback = (
                    decision.get("feedback") or "Revise the answer before concluding."
                )
                step_num = trace.next_step_num
                trace.turns.append(
                    ReActTurn(
                        thought="Human guidance after reviewer escalation.",
                        action=Action(name="think", args={"text": feedback}),
                        observation=ToolObservation(
                            success=True,
                            output=f"Human feedback: {feedback}",
                        ),
                        step_num=step_num,
                    )
                )
                trace.next_step_num = step_num + 1
            record_decision(trace, decision)
            if on_checkpoint:
                on_checkpoint(_trace_snapshot(trace, strategy="react"))
        prior_conclusions = [
            turn
            for turn in trace.turns
            if turn.action.name == "conclude"
            and turn.observation.error not in _NON_CONSUMING_TOOL_ERRORS
        ]
        conclusion_revisions = max(
            len(prior_conclusions),
            trace.budget_consumption.get("conclusion_revisions", 0),
        )
        conclusion_budget_exhausted = (
            self.config.max_conclusion_revisions >= 0
            and conclusion_revisions > self.config.max_conclusion_revisions
        )
        if conclusion_budget_exhausted and prior_conclusions:
            latest_conclusion = prior_conclusions[-1]
            candidate_answer = latest_conclusion.action.args.get("answer", "")
            verification_issues = list(
                dict.fromkeys(
                    issue
                    for review in latest_conclusion.reviews
                    for issue in review.issues
                )
            )
        max_steps = self.config.max_react_steps
        search_mathlib_count = max(
            sum(turn.action.name == "search_mathlib" for turn in trace.turns),
            trace.budget_consumption.get("search_mathlib_calls", 0),
        )
        inferred_tool_calls = sum(
            self._registered_tool_name(turn.action.name) is not None
            and turn.observation.error not in _NON_CONSUMING_TOOL_ERRORS
            for turn in trace.turns
        )
        tool_calls = max(
            inferred_tool_calls,
            trace.budget_consumption.get("tool_calls", 0),
        )
        next_step_num = max(
            trace.next_step_num,
            max((turn.step_num for turn in trace.turns), default=0) + 1,
        )
        step_numbers = (
            () if conclusion_budget_exhausted else range(next_step_num, max_steps + 1)
        )
        for step_num in step_numbers:
            if (
                max_llm_calls > 0
                and self._llm_call_counter.calls >= max_llm_calls
            ):
                run_log.warning(
                    "LLM-call budget exhausted (%d/%d); ending solve as best_effort",
                    self._llm_call_counter.calls,
                    max_llm_calls,
                )
                llm_budget_exhausted = True
                terminated_without_synthesis = True
                break
            is_first_step = not trace.turns
            await self._maybe_compact_context(trace, run_log)
            # Retain attachments when the model is only revising a conclusion
            # so that the original problem images remain visible.
            only_conclusions_so_far = all(
                turn.action.name == "conclude" for turn in trace.turns
            )
            try:
                if self._native_tools_enabled():
                    thought, action, action_confidence = await _await_phase(
                        self._generate_action_native(
                            trace,
                            run_log,
                            emit,
                            attachments=attachments
                            if (is_first_step or only_conclusions_so_far)
                            else None,
                            require_formal_verification=require_formal_verification,
                        ),
                        deadline=deadline,
                        run_log=run_log,
                        phase="model_generation",
                        model_role="main",
                    )
                else:
                    raw_response, action_confidence = await _await_phase(
                        self._generate_action(
                            trace,
                            run_log,
                            emit,
                            attachments=attachments
                            if (is_first_step or only_conclusions_so_far)
                            else None,
                            require_formal_verification=require_formal_verification,
                        ),
                        deadline=deadline,
                        run_log=run_log,
                        phase="model_generation",
                        model_role="main",
                    )
                    action = await _await_phase(
                        self._parse_action_safe(raw_response, run_log),
                        deadline=deadline,
                        run_log=run_log,
                        phase="action_parsing",
                        model_role="main",
                    )
                    thought = _extract_thought(raw_response)
            except asyncio.TimeoutError:
                wall_time_exhausted = True
                terminated_without_synthesis = True
                break

            run_log.info("Step %s action: %s", step_num, action.name)
            if action.name == "conclude" and isinstance(action.args, dict):
                run_log.info("Step %s conclude args: %s", step_num, action.args)
            await emit(
                {"type": "step_start", "step_num": step_num, "action": action.name}
            )

            validation_error = self._validate_action(action)
            if validation_error is not None:
                error_code, message = validation_error
                turn = ReActTurn(
                    thought=thought,
                    action=action,
                    observation=ToolObservation(
                        success=False,
                        output=message,
                        error=error_code,
                    ),
                    step_num=step_num,
                )
                trace.turns.append(turn)
                trace.next_step_num = step_num + 1
                await self._emit_turn(turn, emit, trace)
                if on_checkpoint:
                    on_checkpoint(_trace_snapshot(trace, strategy="react"))
                continue

            if (
                _consecutive_identical_actions(trace, action, self.tools)
                >= self.config.max_identical_action_repeats
            ):
                issue = "Stopped before executing a third consecutive identical action."
                turn = ReActTurn(
                    thought=thought,
                    action=action,
                    observation=ToolObservation(
                        success=False,
                        output=issue,
                        error="identical_action_limit",
                    ),
                    step_num=step_num,
                )
                trace.turns.append(turn)
                trace.next_step_num = step_num + 1
                verification_issues = list(dict.fromkeys([*verification_issues, issue]))
                terminated_without_synthesis = True
                await self._emit_turn(turn, emit, trace)
                if on_checkpoint:
                    on_checkpoint(_trace_snapshot(trace, strategy="react"))
                break

            if action.name == "conclude":
                decision = await conclude_gate.handle(
                    trace=trace,
                    action=action,
                    thought=thought,
                    step_num=step_num,
                    candidate_answer=candidate_answer,
                    action_confidence=action_confidence,
                    conclusion_revisions=conclusion_revisions,
                    verification_issues=verification_issues,
                    accepted_formal_evidence_id=accepted_formal_evidence_id,
                )
                candidate_answer = decision.candidate_answer
                verification_issues = decision.verification_issues
                conclusion_revisions = decision.conclusion_revisions
                accepted_formal_evidence_id = decision.accepted_formal_evidence_id
                if decision.revise:
                    continue
                final_answer = decision.final_answer
                if decision.outcome is not None:
                    outcome = decision.outcome
                    verification_status = decision.verification_status
                wall_time_exhausted = decision.wall_time_exhausted
                terminated_without_synthesis = decision.terminated_without_synthesis
                break

            registered_tool_name = self._registered_tool_name(action.name)
            if (
                hitl_should_pause(self.config.hitl, "tool_approval")
                and registered_tool_name is not None
                and action.name in self.config.hitl.approval_tools
                and action.name not in self.config.hitl.auto_approve_tools
                and len(trace.human_decisions) < self.config.hitl.max_interrupts_per_run
            ):
                interaction = create_interaction(
                    kind="tool_approval",
                    stage="tool_approval",
                    question=f"Agent 请求执行 {action.name}。是否允许？",
                    details={
                        "step_num": step_num,
                        "action": {"name": action.name, "args": dict(action.args)},
                    },
                )
                trace.pending_interaction = interaction
                if on_checkpoint:
                    on_checkpoint(_trace_snapshot(trace, strategy="react"))
                pause_for_human(trace, interaction)
            if (
                registered_tool_name is not None
                and tool_calls >= self.config.max_tool_calls
            ):
                issue = f"Tool-call budget exhausted after {tool_calls} consumed calls."
                turn = ReActTurn(
                    thought=thought,
                    action=action,
                    observation=ToolObservation(
                        success=False,
                        output=issue,
                        error="tool_call_budget_exhausted",
                    ),
                    step_num=step_num,
                )
                trace.turns.append(turn)
                trace.next_step_num = step_num + 1
                trace.budget_consumption["tool_calls"] = tool_calls
                verification_issues = list(dict.fromkeys([*verification_issues, issue]))
                # Do NOT set terminated_without_synthesis here: unlike a wall-time
                # timeout, the LLM is still available when the tool budget runs
                # out, so fall through to final-answer synthesis and return the
                # gathered partial results instead of a bare status blurb.
                await self._emit_turn(turn, emit, trace)
                if on_checkpoint:
                    on_checkpoint(_trace_snapshot(trace, strategy="react"))
                break

            if registered_tool_name is not None:
                tool_calls += 1
                trace.budget_consumption["tool_calls"] = tool_calls

            await emit(
                {
                    "type": "tool_start",
                    "step_num": step_num,
                    "tool": action.name,
                    "args_preview": _preview_action_args(
                        action.args,
                        limit=2000 if action.name == "compute" else 240,
                    ),
                }
            )

            display_tool = action.name
            if action.name == "search_mathlib":
                search_mathlib_count += 1
                trace.budget_consumption["search_mathlib_calls"] = search_mathlib_count
                search_mathlib_max_calls = max(
                    0, int(getattr(self.config, "search_mathlib_max_calls", 3))
                )
                if search_mathlib_count > search_mathlib_max_calls:
                    observation = ToolObservation(
                        success=True,
                        output=(
                            f"You have already used search_mathlib "
                            f"{search_mathlib_max_calls} times. "
                            "The exact result is probably not in mathlib4. "
                            "Stop searching and proceed with formalize/lean_check."
                        ),
                        error="search_mathlib_limit_reached",
                    )
                else:
                    try:
                        observation = await _await_phase(
                            self._execute_with_heartbeat(action, trace, emit, step_num),
                            deadline=deadline,
                            run_log=run_log,
                            phase="tool_execution",
                            model_role="main",
                        )
                    except asyncio.TimeoutError:
                        observation = ToolObservation(
                            success=False,
                            output="Tool execution exceeded the solve wall-time budget.",
                            error="wall_time_budget_exhausted",
                        )
                        wall_time_exhausted = True
                        terminated_without_synthesis = True
            else:
                try:
                    observation = await _await_phase(
                        self._execute_with_heartbeat(action, trace, emit, step_num),
                        deadline=deadline,
                        run_log=run_log,
                        phase="tool_execution",
                        model_role="main",
                    )
                except asyncio.TimeoutError:
                    observation = ToolObservation(
                        success=False,
                        output="Tool execution exceeded the solve wall-time budget.",
                        error="wall_time_budget_exhausted",
                    )
                    wall_time_exhausted = True
                    terminated_without_synthesis = True

            if (
                observation.error == "blocked_by_hook"
                and registered_tool_name is not None
            ):
                # A pre-tool hook vetoed the call before it ran: refund the
                # tool budget so the block has the same non-consuming
                # semantics as an invalid action.
                tool_calls -= 1
                trace.budget_consumption["tool_calls"] = tool_calls

            observation = attach_formal_evidence(
                action,
                observation,
                target_claim=trace.current_goal or trace.problem,
            )
            if action.name in FORMAL_ACTIONS:
                trace.proof_graph.record_formal_attempt(
                    success=observation.success,
                    evidence_id=evidence_id_from_observation(observation),
                    issue=observation.error
                    or ("" if observation.success else observation.output[:500]),
                )

            await emit(
                {
                    "type": "tool_done",
                    "step_num": step_num,
                    "tool": display_tool,
                    "success": observation.success,
                    "output": observation.output[:2000],
                    "error": observation.error,
                }
            )

            turn = ReActTurn(
                thought=thought,
                action=action,
                observation=observation,
                step_num=step_num,
            )
            if action.name == "set_goal":
                trace.current_goal = action.args.get("goal", trace.current_goal)
                raw_dependencies = action.args.get("depends_on") or []
                dependencies = (
                    [str(item) for item in raw_dependencies]
                    if isinstance(raw_dependencies, list)
                    else []
                )
                try:
                    trace.proof_graph.upsert_goal(
                        trace.current_goal,
                        goal_id=str(action.args.get("goal_id") or ""),
                        depends_on=dependencies,
                        activate=True,
                    )
                except (KeyError, ValueError) as exc:
                    observation.success = False
                    observation.error = "invalid_proof_goal"
                    observation.output = f"Could not update proof goal graph: {exc}"
            trace.turns.append(turn)
            trace.next_step_num = step_num + 1
            await self._emit_turn(turn, emit, trace)

            mid_verify_issue = await self._maybe_mid_verify(
                trace, turn, emit, deadline, run_log
            )
            if mid_verify_issue:
                verification_issues = list(
                    dict.fromkeys([*verification_issues, mid_verify_issue])
                )

            if on_checkpoint:
                on_checkpoint(_trace_snapshot(trace, strategy="react"))

            if wall_time_exhausted:
                break

            if observation.lean_code:
                await emit(
                    {
                        "type": "lean",
                        "code": observation.lean_code,
                        "success": observation.success,
                        "errors": [],
                    }
                )
        if not final_answer:
            if candidate_answer:
                final_answer = candidate_answer
            elif not terminated_without_synthesis:
                # Step budget exhausted without an explicit conclusion. Synthesize a
                # real final answer from the trace instead of returning a raw thought.
                try:
                    final_answer = await _await_phase(
                        self._synthesize_final_answer(trace, run_log, emit),
                        deadline=deadline,
                        run_log=run_log,
                        phase="final_answer_synthesis",
                        model_role="main",
                    )
                except asyncio.TimeoutError:
                    wall_time_exhausted = True
                    terminated_without_synthesis = True
        if wall_time_exhausted:
            issue = f"Solve exceeded the {max_wall_seconds:g}-second wall-time budget."
            verification_issues = list(dict.fromkeys([*verification_issues, issue]))
            outcome = outcome_best_effort(limitations=tuple(verification_issues))
            verification_status = legacy_label(outcome)
        if llm_budget_exhausted:
            issue = (
                "Solve exceeded the per-problem LLM-call budget "
                f"({self._llm_call_counter.calls}/{max_llm_calls} calls)."
            )
            verification_issues = list(dict.fromkeys([*verification_issues, issue]))
            outcome = outcome_best_effort(limitations=tuple(verification_issues))
            verification_status = legacy_label(outcome)
        if not final_answer or not final_answer.strip():
            final_answer = best_effort_answer(trace)
        if not final_answer or not final_answer.strip():
            final_answer = "No solution could be produced within the allotted steps."

        if accepted_formal_evidence_id:
            accepted_formal_turn = next(
                (
                    turn
                    for turn in trace.turns
                    if turn.action.name in FORMAL_ACTIONS
                    and turn.observation.success
                    and evidence_id_from_observation(turn.observation)
                    == accepted_formal_evidence_id
                ),
                None,
            )
            if (
                accepted_formal_turn is not None
                and accepted_formal_turn.observation.lean_code
            ):
                from math_agent.agent.knowledge.promotion import promote_verified_lean

                promotion_msg = promote_verified_lean(
                    self.tools.knowledge_store,
                    self.project_context.project_id or "default",
                    accepted_formal_turn.observation.lean_code,
                    evidence_id=accepted_formal_evidence_id,
                    accepted_evidence_id=accepted_formal_evidence_id,
                    status=(
                        "approved" if verification_status == "verified" else "candidate"
                    ),
                )
                run_log.info("Promotion result: %s", promotion_msg)
                await emit({"type": "status", "message": promotion_msg})

        # Final graph state (conclude may have marked the root proved).
        await self._emit_proof_graph_if_changed(trace, emit)

        trace.tool_stats = self.tools.tool_stats
        solution = ReActSolution(
            problem=problem,
            turns=trace.turns,
            final_answer=final_answer,
            llm_call_count=self._llm_call_counter.calls,
            lean_proofs=(
                [
                    turn.observation.lean_code
                    for turn in trace.turns
                    if turn.action.name in FORMAL_ACTIONS
                    and turn.observation.success
                    and turn.observation.lean_code
                    and evidence_id_from_observation(turn.observation)
                    == accepted_formal_evidence_id
                ]
                if verification_status == "verified"
                else []
            ),
            verification_status=verification_status,
            verification_issues=verification_issues,
            trace=trace,
            verification_outcome=outcome,
        )
        if self.config.memory_consolidation_enabled and self.consolidator is not None:
            try:
                await self.consolidator.consolidate(trace, solution)
            except Exception as exc:
                run_log.warning("Memory consolidation failed: %s", exc)

        return solution

    async def _maybe_plan(
        self,
        trace: ReActTrace,
        run_log: logging.Logger,
        emit: EventCallback,
        deadline: float,
    ) -> None:
        """One up-front planning call for non-easy fresh solves.

        The unified planner emits both the informal strategy (stored in
        ``trace.plan_text``) and a Lean formalization sketch (stored in
        ``trace.formalization_plan``) in a single call. Planning is an
        optimization, never a requirement: any failure leaves both empty and
        the loop runs as before.
        """
        if not self.config.planning_enabled:
            return
        if trace.plan_text or trace.turns:
            return
        if trace.pending_interaction:
            return
        if await self._is_easy_prompt(trace.problem, run_log):
            return
        await emit(
            {
                "type": "stage_status",
                "stage": "planning",
                "message": "正在规划求解策略…",
                "ui": "status_bar",
            }
        )
        try:
            from math_agent.agent.planner import UnifiedPlanner

            planner = UnifiedPlanner(self.llm, mathlib_search=_mathlib_search())
            unified = await _await_phase(
                planner.plan(trace.problem[:6000], session_log=run_log),
                deadline=deadline,
                run_log=run_log,
                phase="planning",
                model_role="main",
            )
        except Exception as exc:
            run_log.warning("Planning failed; solving without a plan: %s", exc)
            return
        if unified is None:
            run_log.warning("Planning returned nothing; solving without a plan")
            return
        if unified.plan_text:
            trace.plan_text = unified.plan_text[
                : max(0, int(self.config.planning_max_chars))
            ]
        formalization = asdict(unified.formalization)
        if any(formalization.get(key) for key in _FORMALIZATION_CONTENT_KEYS):
            trace.formalization_plan = formalization

    async def _maybe_claim_check(
        self,
        trace: ReActTrace,
        run_log: logging.Logger,
        emit: EventCallback,
        deadline: float,
    ) -> None:
        """Hypothesis audit + computational refute for non-easy normal solves."""
        if not bool(self.config.normal_claim_check_enabled):
            return
        if trace.research_mode or trace.claim_check or trace.turns:
            return
        if await self._is_easy_prompt(trace.problem, run_log):
            return
        await emit(
            {
                "type": "stage_status",
                "stage": "claim_check",
                "message": "正在审查命题…",
                "ui": "status_bar",
            }
        )
        try:
            from math_agent.agent.claim_check import (
                apply_claim_check_to_trace,
                run_claim_check,
            )

            result = await _await_phase(
                run_claim_check(
                    problem=trace.problem,
                    llm=self.llm,
                    critic_llm=self.critic_llm,
                    tool_registry=self.tools,
                    project_context=trace.project_context,
                    refute_enabled=bool(self.config.research_refutation_enabled),
                    max_tool_calls=max(
                        0, int(self.config.normal_claim_check_max_tool_calls)
                    ),
                    on_event=emit,
                ),
                deadline=deadline,
                run_log=run_log,
                phase="claim_check",
                model_role="critic",
            )
            apply_claim_check_to_trace(trace, result)
            run_log.info(
                "Claim check status=%s counterexample=%s",
                result.status,
                result.counterexample_found,
            )
            await emit(
                {
                    "type": "stage_status",
                    "stage": "claim_check",
                    "message": "命题审查完成。",
                    "ui": "status_bar",
                }
            )
        except Exception as exc:
            run_log.warning("Claim check failed; continuing solve: %s", exc)
            trace.claim_check = {
                "status": "ok",
                "issues": [],
                "revised_claim": "",
                "counterexample_found": False,
                "refute_summary": f"Claim check failed: {exc}",
                "refute": {"status": "error"},
            }
            await emit(
                {
                    "type": "stage_status",
                    "stage": "claim_check",
                    "message": "命题审查未完成，继续求解。",
                    "ui": "status_bar",
                }
            )

    async def _maybe_mid_verify(
        self,
        trace: ReActTrace,
        turn: ReActTurn,
        emit: EventCallback,
        deadline: float,
        run_log: logging.Logger,
    ) -> str | None:
        """Checkpoint-verify a checkable intermediate claim from the latest turn.

        Returns an issue string when verification found the claim to be wrong,
        otherwise None. Checkpoint tool calls draw on their own budget and
        never consume max_tool_calls.
        """
        if not bool(getattr(self.config, "mid_verify_enabled", False)):
            return None
        if trace.research_mode or turn.action.name == "conclude":
            return None
        if await self._is_easy_prompt(trace.problem, run_log):
            return None
        max_calls = max(0, int(getattr(self.config, "mid_verify_max_calls", 0)))
        used = trace.budget_consumption.get("mid_verify_calls", 0)
        if used >= max_calls:
            return None
        every = max(1, int(getattr(self.config, "mid_verify_every", 1)))
        if len(trace.turns) % every != 0:
            return None
        await emit(
            {
                "type": "stage_status",
                "stage": "mid_verify",
                "message": "正在验证中间结论…",
                "ui": "status_bar",
            }
        )
        try:
            from math_agent.agent.mid_verify import (
                format_mid_verify_note,
                run_mid_verify,
            )

            result = await _await_phase(
                run_mid_verify(
                    turn=turn,
                    problem=trace.problem,
                    llm=self.llm,
                    critic_llm=self.critic_llm,
                    tool_registry=self.tools,
                    project_context=trace.project_context,
                    on_event=emit,
                ),
                deadline=deadline,
                run_log=run_log,
                phase="mid_verify",
                model_role="critic",
            )
        except Exception as exc:
            run_log.warning("Mid-verify failed; continuing solve: %s", exc)
            return None
        if not result.checked:
            return None
        trace.budget_consumption["mid_verify_calls"] = used + 1
        trace.mid_verifications.append(result.to_dict())
        note = format_mid_verify_note(result)
        if note:
            turn.observation.output = f"{turn.observation.output}{note}"
        run_log.info(
            "Mid-verify method=%s passed=%s claim=%s",
            result.method,
            result.passed,
            result.claim[:120],
        )
        if result.passed:
            return None
        corrections = trace.budget_consumption.get("mid_verify_corrections", 0) + 1
        trace.budget_consumption["mid_verify_corrections"] = corrections
        max_corrections = max(
            1, int(getattr(self.config, "mid_verify_max_corrections", 2))
        )
        issue = (
            "Mid-trace verification failed for intermediate claim: "
            f"{result.claim[:200]}"
        )
        if corrections >= max_corrections:
            # The correction budget is exhausted: surface a durable review issue
            # (carried into verification_issues and the final metadata) instead
            # of the removed research-mode escalation signal.
            run_log.info(
                "Mid-verify correction budget exhausted after %d failures",
                corrections,
            )
            issue += (
                " (repeated mid-verify failures exhausted the correction budget; "
                "treat this claim as unresolved)"
            )
        return issue

    def _native_tools_enabled(self) -> bool:
        """Whether the main backend speaks native function calling."""
        return bool(getattr(self.llm, "supports_native_tools", False))

    async def _is_easy_prompt(self, problem: str, run_log: logging.Logger) -> bool:
        """Cached easy/hard verdict for the current solve.

        Trivial prompts short-circuit structurally; everything else is decided
        by one cheap critic call (see ``prompt_difficulty``). The verdict only
        gates heavy stages, so classifier failures conservatively stay "hard".
        """
        if self._easy_verdict is None:
            self._easy_verdict = await classify_easy_prompt(
                problem,
                self.critic_llm,
                mode=str(getattr(self.config, "easy_prompt_classifier", "critic")),
                run_log=run_log,
            )
        return self._easy_verdict

    def _action_request(
        self,
        trace: ReActTrace,
        attachments: list[dict] | None,
        require_formal_verification: bool,
        *,
        native: bool,
    ) -> tuple[list[Message], str, list[dict] | None]:
        """Build (messages, system_prompt, tool_schemas) for one action step.

        ``tool_schemas`` is only populated for the native function-calling
        protocol; the legacy JSON protocol returns None there.
        """
        from math_agent.agent.supervisor import build_first_user_content

        window_cap = (
            self.config.research_context_max_chars
            if trace.research_mode
            else self.config.react_context_max_chars
        )
        context = trace.context_window(max_chars=max(4_000, int(window_cap)))
        messages = [
            Message(role="user", content=build_first_user_content(context, attachments))
        ]

        # Build the system prompt dynamically so tool descriptions are never
        # hardcoded and MCP tools can be disclosed progressively.
        step = len(trace.turns)
        tool_descriptions = self.tools.describe_visible_tools(
            context=trace.problem,
            step=step,
            progressive=self.config.mcp_progressive_disclosure,
            mcp_top_k=self.config.mcp_initial_top_k,
            allowed_names=self.allowed_tools,
        )
        tool_list = self.tools.format_tool_list(tool_descriptions)
        if native:
            system_prompt = build_react_native_system_prompt(
                tool_descriptions=tool_list,
                require_formal_verification=require_formal_verification,
                search_mathlib_max_calls=max(
                    0, int(getattr(self.config, "search_mathlib_max_calls", 3))
                ),
            )
            tool_schemas: list[dict] | None = self.tools.native_tool_schemas(
                tool_descriptions
            )
        else:
            system_prompt = build_react_system_prompt(
                tool_descriptions=tool_list,
                require_formal_verification=require_formal_verification,
                search_mathlib_max_calls=max(
                    0, int(getattr(self.config, "search_mathlib_max_calls", 3))
                ),
            )
            tool_schemas = None
        return messages, system_prompt, tool_schemas

    async def _generate_action(
        self,
        trace: ReActTrace,
        run_log: logging.Logger,
        emit: EventCallback | None,
        attachments: list[dict] | None = None,
        require_formal_verification: bool = False,
    ) -> tuple[str, float | None]:
        messages, system_prompt, _ = self._action_request(
            trace,
            attachments,
            require_formal_verification,
            native=False,
        )

        if emit:
            await emit({"type": "llm_start", "label": "Generating the next action..."})
        chunks: list[str] = []
        mean_lp: float | None = None
        overflow_retried = False
        while True:
            chunks.clear()
            mean_lp = None
            try:
                async for response in self.llm.stream(
                    messages,
                    system=system_prompt,
                    response_format={"type": "json_object"},
                    logprobs=True,
                ):
                    chunk_text = response.text
                    chunks.append(chunk_text)
                    if response.mean_logprob is not None:
                        mean_lp = response.mean_logprob
                    if emit and chunk_text:
                        await emit({"type": "token", "content": chunk_text})
                break
            except Exception as exc:
                if not overflow_retried and is_context_overflow_error(exc):
                    # The provider rejected the prompt as too long: fold the
                    # older half of the in-window turns into the compacted
                    # summary, rebuild the request, and retry this call once.
                    overflow_retried = True
                    run_log.warning(
                        "Context overflow during action generation; "
                        "compacting context and retrying once: %s",
                        exc,
                    )
                    await self._force_compact_for_overflow(trace, run_log)
                    messages, system_prompt, _ = self._action_request(
                        trace,
                        attachments,
                        require_formal_verification,
                        native=False,
                    )
                    continue
                run_log.exception("LLM stream failed")
                raise
        response = "".join(chunks)
        confidence = confidence_from_mean_logprob(mean_lp)
        run_log.debug(
            "LLM response received chars=%s mean_logprob=%s confidence=%s",
            len(response),
            mean_lp,
            confidence,
        )
        return response, confidence

    async def _generate_action_native(
        self,
        trace: ReActTrace,
        run_log: logging.Logger,
        emit: EventCallback | None,
        attachments: list[dict] | None = None,
        require_formal_verification: bool = False,
    ) -> tuple[str, Action, float | None]:
        """One action step over native function calling.

        The streamed content is the plain-text thought (emitted as token
        events exactly like the legacy path); the action comes from the first
        tool_call of the final summary chunk.
        """
        messages, system_prompt, tool_schemas = self._action_request(
            trace,
            attachments,
            require_formal_verification,
            native=True,
        )

        if emit:
            await emit({"type": "llm_start", "label": "Generating the next action..."})
        chunks: list[str] = []
        mean_lp: float | None = None
        tool_calls: tuple[ToolCall, ...] | None = None
        overflow_retried = False
        while True:
            chunks.clear()
            mean_lp = None
            tool_calls = None
            try:
                async for response in self.llm.stream(
                    messages,
                    system=system_prompt,
                    logprobs=True,
                    tools=tool_schemas,
                ):
                    chunk_text = response.text
                    chunks.append(chunk_text)
                    if response.mean_logprob is not None:
                        mean_lp = response.mean_logprob
                    if response.tool_calls:
                        tool_calls = response.tool_calls
                    if emit and chunk_text:
                        await emit({"type": "token", "content": chunk_text})
                break
            except Exception as exc:
                if not overflow_retried and is_context_overflow_error(exc):
                    # Same overflow recovery as the legacy path: compact the
                    # older half of the in-window turns and retry once.
                    overflow_retried = True
                    run_log.warning(
                        "Context overflow during native action generation; "
                        "compacting context and retrying once: %s",
                        exc,
                    )
                    await self._force_compact_for_overflow(trace, run_log)
                    messages, system_prompt, tool_schemas = self._action_request(
                        trace,
                        attachments,
                        require_formal_verification,
                        native=True,
                    )
                    continue
                run_log.exception("LLM stream failed")
                raise
        thought = "".join(chunks).strip()
        confidence = confidence_from_mean_logprob(mean_lp)
        run_log.debug(
            "LLM native response received chars=%s tool_calls=%s mean_logprob=%s",
            len(thought),
            len(tool_calls or ()),
            mean_lp,
        )
        action = self._action_from_tool_calls(thought, tool_calls, run_log)
        return thought, action, confidence

    @staticmethod
    def _action_from_tool_calls(
        thought: str,
        tool_calls: tuple[ToolCall, ...] | None,
        run_log: logging.Logger,
    ) -> Action:
        if not tool_calls:
            run_log.info("Native response had no tool_call; degrading to think")
            return Action(
                name="think",
                args={"text": thought or "No reasoning provided."},
            )
        if len(tool_calls) > 1:
            run_log.warning(
                "Model returned %d tool_calls in one step; using the first (%s)",
                len(tool_calls),
                tool_calls[0].name,
            )
        call = tool_calls[0]
        name = (call.name or "").strip()
        if not name:
            run_log.warning("Native tool_call had an empty name; degrading to think")
            return Action(
                name="think",
                args={"text": thought or "No reasoning provided."},
            )
        args = call.arguments if isinstance(call.arguments, dict) else {}
        return Action(name=name, args=args)

    async def _review_skip_reason(
        self,
        *,
        problem: str,
        action_confidence: float | None,
        require_formal_verification: bool,
        run_log: logging.Logger,
    ) -> str | None:
        """Return why the reviewer panel can be skipped, or None to keep reviewing."""
        if not self.reviewers:
            return None
        if bool(self.config.force_review):
            return None
        # The easy verdict costs one cheap critic call per solve; only pay it
        # when one of the easy-gated policies is actually enabled.
        easy: bool | None = None
        if bool(self.config.skip_review_on_easy_prompt) or bool(
            self.config.normal_force_review
        ):
            easy = await self._is_easy_prompt(problem, run_log)
        if (
            not require_formal_verification
            and bool(self.config.skip_review_on_easy_prompt)
            and easy
        ):
            return "easy_prompt"
        # Non-easy normal solves must not skip reviewers via logprob confidence.
        if bool(self.config.normal_force_review) and not easy:
            return None
        if self._should_skip_review(action_confidence):
            return "high_confidence"
        return None

    def _should_skip_review(self, action_confidence: float | None) -> bool:
        threshold = float(self.config.skip_review_min_confidence)
        if threshold <= 0:
            return False
        if action_confidence is None:
            return False
        return action_confidence >= threshold

    async def _parse_action_safe(
        self, raw_response: str, run_log: logging.Logger
    ) -> Action:
        try:
            return parse_action(raw_response)
        except ActionParseError:
            pass
        repaired = await parse_action_with_repair(raw_response, self.llm)
        if repaired is not None:
            return repaired
        run_log.warning("Failed to parse action even after repair")
        return Action(
            name="think",
            args={"text": f"Failed to parse model response: {raw_response[:200]}"},
        )

    async def _execute_action(
        self, action: Action, trace: ReActTrace
    ) -> ToolObservation:
        if action.name == "think":
            return ToolObservation(success=True, output="Thinking recorded.")

        if action.name == "set_goal":
            goal = action.args.get("goal", trace.current_goal)
            return ToolObservation(success=True, output=f"Goal updated to: {goal}")

        if action.name == "update_plan":
            items = normalize_plan_items(action.args.get("items"))
            trace.plan_items = items
            return ToolObservation(
                success=True,
                output=f"Plan checklist updated ({len(items)} item(s)).",
            )

        state = ReasoningState(
            problem=trace.problem,
            current_goal=trace.current_goal,
            steps=[
                ReasoningStep(
                    content=turn.thought,
                    step_type=StepType.TOOL_USE
                    if turn.action.name != "think"
                    else StepType.REASONING,
                    tool_name=turn.action.name,
                    tool_result=turn.observation.output,
                    tool_success=turn.observation.success,
                )
                for turn in trace.turns
            ],
        )
        tool_ctx = ToolContext(
            lean_runner=self.tools.lean_runner,
            lean_codegen=self.tools.lean_codegen,
            project_context=trace.project_context,
            state=state,
            llm=self.llm,
            critic_llm=self.critic_llm,
            agent_config=self.config,
            figure_dir=self._figure_dir,
            figure_url_prefix=self._figure_url_prefix,
            formalization_plan=trace.formalization_plan or None,
            event_callback=_TOOL_EVENT_CALLBACK.get(),
            session_log=_TOOL_SESSION_LOG.get(),
        )
        try:
            run_pre_tool_hooks(action.name, action.args)
        except Exception as exc:
            # A pre-tool hook vetoed this call: surface the reason as the
            # observation without running the tool. The "blocked_by_hook"
            # error code keeps the call out of the tool budget (invalid
            # semantics, see _NON_CONSUMING_TOOL_ERRORS).
            log.info(
                "Tool call blocked by pre-tool hook: %s: %s", action.name, exc
            )
            return ToolObservation(
                success=False,
                output=f"Tool call blocked by pre-tool hook: {exc}",
                error="blocked_by_hook",
            )
        try:
            observation = await self.tools.execute_action(action, tool_ctx)
        except Exception as exc:
            run_log = logging.getLogger("math_agent.agent")
            run_log.warning("Tool execution failed for %s: %s", action.name, exc)
            return ToolObservation(
                success=False,
                output=f"Tool execution failed: {exc}",
                error=str(exc),
            )
        # Post-tool hooks are pure observers; run_post_tool_hooks swallows
        # and logs their own failures.
        run_post_tool_hooks(action.name, action.args, observation)
        return observation

    async def _execute_with_heartbeat(
        self,
        action: Action,
        trace: ReActTrace,
        emit: EventCallback,
        step_num: int,
    ) -> ToolObservation:
        """Run a tool while emitting periodic tool_progress events.

        Long tools (formalize/lean_check trigger multi-minute Lean builds) would
        otherwise leave the WebSocket silent long enough for the client or an
        intermediary proxy to drop the connection. The heartbeat keeps bytes
        flowing and tells the user work is still in progress.
        """
        interval = getattr(self.config, "tool_heartbeat_seconds", 10.0) or 10.0
        stop = asyncio.Event()

        async def _beat() -> None:
            elapsed = 0.0
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    elapsed += interval
                    await emit(
                        {
                            "type": "tool_progress",
                            "step_num": step_num,
                            "tool": action.name,
                            "elapsed_seconds": int(elapsed),
                            "message": f"Still working on {action.name} ({int(elapsed)}s elapsed)…",
                        }
                    )

        beat_task = asyncio.create_task(_beat())

        async def _progress(message: str) -> None:
            await emit(
                {
                    "type": "tool_progress",
                    "step_num": step_num,
                    "tool": action.name,
                    "detail": message,
                }
            )

        token = _TOOL_EVENT_CALLBACK.set(_progress)
        try:
            return await self._execute_action(action, trace)
        finally:
            _TOOL_EVENT_CALLBACK.reset(token)
            stop.set()
            beat_task.cancel()
            try:
                await beat_task
            except asyncio.CancelledError:
                pass
            except Exception:
                # A failed heartbeat callback must never hide the tool result
                # or turn a clean cancellation into an unhandled exception.
                pass

    async def _synthesize_final_answer(
        self,
        trace: ReActTrace,
        run_log: logging.Logger,
        emit: EventCallback,
    ) -> str:
        """Write a coherent final answer from the trace when the loop never concluded."""
        transcript_parts: list[str] = []
        for turn in trace.turns:
            transcript_parts.append(f"Thought: {turn.thought}")
            transcript_parts.append(f"Action: {turn.action.name} {turn.action.args}")
            transcript_parts.append(f"Observation: {turn.observation.output[:800]}")
        transcript = "\n".join(transcript_parts)[:6000]

        user = (
            f"Problem:\n{trace.problem}\n\n"
            f"Work so far (the reasoning loop ran out of steps before concluding):\n{transcript}\n\n"
            "Write the best complete final answer you can, based on the work above. "
            "If the problem is not fully solved, give the most useful partial result and "
            "state clearly what remains. Do not mention steps, tools, or this instruction."
        )
        try:
            await emit(
                {
                    "type": "stage_status",
                    "stage": "finalizing",
                    "message": "正在整理最终答案…",
                    "ui": "status_bar",
                }
            )
            response = await self.llm.complete(
                [Message(role="user", content=user)],
                system="You are a mathematician writing the final answer to a problem.",
                temperature=0.2,
            )
            return (response.text or "").strip()
        except Exception as exc:
            run_log.warning("Final-answer synthesis failed: %s", exc)
            return best_effort_answer(trace)

    async def _maybe_compact_context(
        self,
        trace: ReActTrace,
        run_log: logging.Logger,
    ) -> None:
        """Summarize turns that have fallen out of the context window.

        ``ReActTrace.context_window`` only shows the most recent
        ``_CONTEXT_WINDOW_MAX_TURNS`` turns; instead of silently forgetting
        older turns, fold their key facts into ``trace.compacted_summary``
        with one cheap critic-model call. Older turns are additionally
        compacted ahead of time when the rendered actor context would exceed
        the configured token budget (``react_context_max_tokens``; the
        max-chars cap in ``context_window`` remains the final backstop).
        On any LLM failure, degrade to the previous behavior (old turns are
        dropped unsummarized).
        """
        if trace.research_mode:
            # Research workers compact via the research orchestrator.
            return
        target = self._compaction_target(trace, run_log)
        if target <= trace.compacted_turn_count:
            return
        new_turns = trace.turns[trace.compacted_turn_count : target]
        try:
            summary = await self._summarize_dropped_turns(trace, new_turns)
        except Exception as exc:
            run_log.warning(
                "Context compaction failed; dropping old turns unsummarized: %s",
                exc,
            )
        else:
            if summary:
                trace.compacted_summary = summary[:2000]
        trace.compacted_turn_count = target

    def _compaction_target(
        self,
        trace: ReActTrace,
        run_log: logging.Logger,
    ) -> int:
        """How many leading turns should be folded into the compacted summary.

        Starts from the turn-count window overflow and raises the target
        while the rendered context (with the not-yet-compacted turns kept
        visible) still exceeds the token budget. Always keeps the latest
        turn visible.
        """
        window_target = max(0, len(trace.turns) - _CONTEXT_WINDOW_MAX_TURNS)
        target = max(window_target, trace.compacted_turn_count)
        max_chars = max(4_000, int(self.config.react_context_max_chars))
        max_tokens = int(getattr(self.config, "react_context_max_tokens", 0) or 0)
        if max_tokens <= 0:
            max_tokens = max(1, max_chars // 4)
        while target < len(trace.turns) - 1:
            visible = trace.turns[target:]
            context = trace.context_window(
                max_turns=len(visible), max_chars=max_chars
            )
            if _estimate_context_tokens(context) <= max_tokens:
                break
            target += max(1, (len(trace.turns) - target) // 2)
        if target > max(window_target, trace.compacted_turn_count):
            run_log.info(
                "Proactive context compaction: token estimate exceeded budget "
                "(%d tokens); compacting %d turn(s)",
                max_tokens,
                target - trace.compacted_turn_count,
            )
        return target

    async def _force_compact_for_overflow(
        self,
        trace: ReActTrace,
        run_log: logging.Logger,
    ) -> None:
        """Fold the older half of the in-window turns into the summary.

        Used after the provider rejects a request for exceeding the context
        limit; the retried call then runs against a smaller prompt.
        """
        start = max(
            trace.compacted_turn_count,
            len(trace.turns) - _CONTEXT_WINDOW_MAX_TURNS,
            0,
        )
        extra = (len(trace.turns) - start) // 2
        if extra <= 0:
            run_log.warning(
                "Context overflow but nothing left to compact "
                "(%d turn(s), %d already compacted)",
                len(trace.turns),
                trace.compacted_turn_count,
            )
            return
        target = start + extra
        new_turns = trace.turns[trace.compacted_turn_count : target]
        try:
            summary = await self._summarize_dropped_turns(trace, new_turns)
        except Exception as exc:
            run_log.warning("Overflow-triggered compaction failed: %s", exc)
        else:
            if summary:
                trace.compacted_summary = summary[:2000]
        trace.compacted_turn_count = target
        run_log.info(
            "Overflow-triggered compaction: folded %d in-window turn(s) into "
            "the summary (compacted_turn_count=%d)",
            extra,
            target,
        )

    async def _summarize_dropped_turns(
        self,
        trace: ReActTrace,
        dropped_turns: list[ReActTurn],
    ) -> str:
        digest_parts: list[str] = []
        digest_chars = 0
        for turn in dropped_turns:
            lines = [f"Step {turn.step_num} [{turn.action.name}]"]
            if turn.thought:
                lines.append(f"thought: {turn.thought[:400]}")
            if turn.action.name == "conclude":
                answer = str(turn.action.args.get("answer") or "")
                lines.append(f"conclusion: {answer[:600]}")
                if turn.reviews:
                    lines.append(
                        "reviews: "
                        + "; ".join(
                            f"{review.reviewer}={review.verdict}"
                            for review in turn.reviews
                        )
                    )
            elif turn.action.args:
                lines.append(f"args: {str(turn.action.args)[:300]}")
            output = (turn.observation.output or "")[:400]
            if output:
                lines.append(f"finding: {output}")
            block = "\n".join(lines)
            if digest_chars + len(block) > 6000:
                break
            digest_parts.append(block)
            digest_chars += len(block)
        prompt = (
            "Earlier progress summary (may be empty):\n"
            f"{trace.compacted_summary or '(none)'}\n\n"
            f"Current goal: {trace.current_goal[:1000]}\n\n"
            "Newly finished steps leaving the context window:\n"
            + "\n\n".join(digest_parts)
            + "\n\nMerge the above into an updated running summary of at most "
            "2000 characters. Keep the current goal, proved lemmas and "
            "intermediate results, conclusions with their review/verification "
            "status, and key tool findings. Drop dead ends unless they rule "
            "out an approach. Output only the summary."
        )
        response = await self.critic_llm.complete(
            [Message(role="user", content=prompt)],
            system=(
                "You maintain a running summary of earlier work in a "
                "mathematical problem-solving trace. Be terse and factual."
            ),
            temperature=0.2,
        )
        return (response.text or "").strip()

    async def _run_reviewers(
        self,
        turn: ReActTurn,
        trace: ReActTrace,
        run_log: logging.Logger,
    ) -> list[ReviewResult]:
        if not self.reviewers or turn.action.name != "conclude":
            return []
        results = await asyncio.gather(
            *[r.review(turn, trace) for r in self.reviewers],
            return_exceptions=True,
        )
        reviews: list[ReviewResult] = []
        for reviewer, result in zip(self.reviewers, results, strict=True):
            if isinstance(result, Exception):
                run_log.warning("Reviewer %s failed: %s", reviewer.name, result)
                fallback_reviewer: Reviewer | None = None
                if self.llm is not self.critic_llm:
                    if isinstance(reviewer, CriticReviewer):
                        fallback_reviewer = CriticReviewer(llm=self.llm)
                    elif isinstance(reviewer, StatementFidelityReviewer):
                        fallback_reviewer = StatementFidelityReviewer(llm=self.llm)
                    elif isinstance(reviewer, CompletenessReviewer):
                        fallback_reviewer = CompletenessReviewer(llm=self.llm)
                if fallback_reviewer is not None:
                    try:
                        fallback_result = await fallback_reviewer.review(turn, trace)
                    except Exception as fallback_exc:
                        run_log.warning(
                            "Reviewer %s fallback failed: %s",
                            reviewer.name,
                            fallback_exc,
                        )
                    else:
                        run_log.info(
                            "Reviewer %s recovered with the main model backend",
                            reviewer.name,
                        )
                        reviews.append(fallback_result)
                        continue
                reviews.append(
                    ReviewResult(
                        reviewer=reviewer.name,
                        verdict="UNAVAILABLE",
                        suggestions=[
                            "Retry verification before accepting the answer."
                        ],
                        confidence=0.0,
                    )
                )
            else:
                reviews.append(result)
        return reviews

    async def _evaluate_conclusion_candidates(
        self,
        primary: ReActTurn,
        trace: ReActTrace,
        run_log: logging.Logger,
        *,
        require_formal_verification: bool,
        emit: EventCallback,
    ) -> ReActTurn:
        primary.reviews = await self._run_reviewers(primary, trace, run_log)
        candidate_count = max(
            1,
            int(getattr(self.config, "conclusion_candidate_count", 1)),
        )
        min_turns = max(
            0,
            int(getattr(self.config, "candidate_search_min_turns", 4)),
        )
        if (
            require_formal_verification
            or candidate_count <= 1
            or len(trace.turns) < min_turns
            or not self.reviewers
        ):
            return primary

        alternatives = await self._generate_alternative_conclusions(
            trace,
            primary.action.args.get("answer", ""),
            count=candidate_count - 1,
        )
        candidates = [primary]
        for answer in alternatives:
            observation = ToolObservation(
                success=True,
                output=f"Conclusion: {answer}",
            )
            candidate = ReActTurn(
                thought="Alternative complete solution candidate.",
                action=Action(name="conclude", args={"answer": answer}),
                observation=observation,
                step_num=primary.step_num,
            )
            candidates.append(candidate)
        reviews_list = await asyncio.gather(
            *(self._run_reviewers(candidate, trace, run_log) for candidate in candidates[1:])
        )
        for candidate, reviews in zip(candidates[1:], reviews_list):
            candidate.reviews = reviews

        selected = max(candidates, key=_candidate_score)
        await emit(
            {
                "type": "candidate_search",
                "candidate_count": len(candidates),
                "selected_index": candidates.index(selected),
                "scores": [_candidate_score(candidate) for candidate in candidates],
            }
        )
        return selected

    async def _generate_alternative_conclusions(
        self,
        trace: ReActTrace,
        primary_answer: str,
        *,
        count: int,
    ) -> list[str]:
        transcript = trace.context_window(max_turns=10)[-9000:]
        prompt = (
            f"Problem and work so far:\n{transcript}\n\n"
            f"Primary candidate:\n{primary_answer[:6000]}\n\n"
            f"Generate {count} genuinely different complete candidate answers. "
            "Prefer a different proof strategy or an independently checked derivation. "
            "Output only JSON with this shape: "
            '{"candidates":[{"answer":"..."}]}.'
        )
        response = await self.llm.complete(
            [Message(role="user", content=prompt)],
            system=(
                "You generate independent complete mathematical solution candidates. "
                "Do not critique or rank them. Output valid JSON only."
            ),
            temperature=0.7,
            response_format={"type": "json_object"},
        )
        try:
            data = json.loads(response.text)
        except (TypeError, json.JSONDecodeError):
            return []
        raw_candidates = data.get("candidates") if isinstance(data, dict) else []
        if not isinstance(raw_candidates, list):
            return []
        answers: list[str] = []
        seen = {primary_answer.strip()}
        for item in raw_candidates:
            if not isinstance(item, dict):
                continue
            answer = str(item.get("answer") or "").strip()
            if answer and answer not in seen:
                seen.add(answer)
                answers.append(answer)
            if len(answers) >= count:
                break
        return answers

    async def _emit_turn(
        self, turn: ReActTurn, emit: EventCallback, trace: ReActTrace | None = None
    ) -> None:
        voting = [r for r in turn.reviews if not r.abstained]
        verified = (
            all(r.verdict == "PASS" for r in voting) if voting else None
        )
        # Keep conclude answers intact for math rendering; truncate other tool output.
        obs_limit = 8000 if turn.action.name == "conclude" else 500
        await emit(
            {
                "type": "step",
                "step_num": turn.step_num,
                "action": turn.action.name,
                "thought": turn.thought,
                "observation": turn.observation.output[:obs_limit],
                "verified": verified,
                "reviews": [
                    {"reviewer": r.reviewer, "verdict": r.verdict, "issues": r.issues}
                    for r in turn.reviews
                ],
            }
        )
        if trace is not None:
            await self._emit_proof_graph_if_changed(trace, emit)

    async def _emit_proof_graph_if_changed(
        self, trace: ReActTrace, emit: EventCallback
    ) -> None:
        """Stream the proof-goal DAG to clients whenever it mutates.

        The web frontend renders this as the live DAG panel (reactflow), so
        users can watch subgoals flip pending → in_progress → proved/failed.
        """
        graph = trace.proof_graph
        if not graph.goals:
            return
        fingerprint = (
            graph.root_id,
            graph.active_goal_id,
            tuple(
                sorted(
                    (goal.id, goal.status, goal.attempts, tuple(goal.depends_on))
                    for goal in graph.goals.values()
                )
            ),
        )
        if fingerprint == getattr(trace, "_emitted_graph_fp", None):
            return
        trace._emitted_graph_fp = fingerprint
        await emit({"type": "proof_graph", "proof_graph": graph.to_dict()})


async def _await_phase(
    awaitable: Awaitable[_T],
    *,
    deadline: float,
    run_log: logging.Logger,
    phase: str,
    model_role: str,
) -> _T:
    started = time.monotonic()
    remaining = max(0.0, deadline - asyncio.get_running_loop().time())
    try:
        return await asyncio.wait_for(awaitable, timeout=remaining)
    finally:
        run_log.info(
            "phase_duration phase=%s model_role=%s duration_seconds=%.3f",
            phase,
            model_role,
            time.monotonic() - started,
        )


# Fields that make a formalization sketch worth keeping on the trace.
_FORMALIZATION_CONTENT_KEYS = (
    "restatement",
    "goal_type",
    "recommended_theorem",
    "recommended_imports",
    "proof_strategy",
    "lemmas",
)


def _mathlib_search():
    """Local mathlib4 search for plan validation, when a checkout exists."""
    try:
        from math_agent.lean.mathlib_search import default_search

        return default_search()
    except Exception:
        return None


def _has_diagram_for_conclusion(
    trace: ReActTrace,
    *,
    candidate_answer: str,
    after_index: int,
) -> bool:
    """Whether this conclusion is backed by a fresh plot_figure embed."""
    if not _FIGURE_EMBED_RE.search(candidate_answer or ""):
        return False
    return any(
        turn.action.name == "plot_figure" and turn.observation.success
        for turn in trace.turns[after_index + 1 :]
    )


def _action_fingerprint(
    action: Action,
    tool_registry: ToolRegistry | None = None,
) -> str:
    effective_name = _TOOL_ACTION_ALIASES.get(action.name, action.name)
    arg_map: str | tuple[str, ...] | None = _SPECIAL_ACTION_ARGUMENTS.get(
        effective_name
    )
    if arg_map is None and tool_registry is not None:
        arg_map = tool_registry.argument_map(effective_name)
    if isinstance(arg_map, tuple):
        effective_args = {key: action.args.get(key) for key in arg_map}
    elif isinstance(arg_map, str):
        effective_args = {arg_map: action.args.get(arg_map)}
    else:
        effective_args = action.args
    return json.dumps(
        {"name": effective_name, "args": effective_args},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _consecutive_identical_actions(
    trace: ReActTrace,
    action: Action,
    tool_registry: ToolRegistry,
) -> int:
    fingerprint = _action_fingerprint(action, tool_registry)
    count = 0
    for turn in reversed(trace.turns):
        if _action_fingerprint(turn.action, tool_registry) != fingerprint:
            break
        count += 1
    return count


def _extract_thought(text: str) -> str:
    """Extract human-readable thought text from one or more JSON action blobs."""
    raw = (text or "").strip()
    if not raw:
        return ""

    thoughts: list[str] = []

    def _collect(obj: object) -> None:
        if not isinstance(obj, dict):
            return
        thought = obj.get("thought")
        if isinstance(thought, str) and thought.strip():
            thoughts.append(thought.strip())

    try:
        _collect(json.loads(raw))
    except Exception:
        # Models sometimes emit concatenated JSON objects: `{...}\n{...}`.
        decoder = json.JSONDecoder()
        idx = 0
        while idx < len(raw):
            while idx < len(raw) and raw[idx].isspace():
                idx += 1
            if idx >= len(raw):
                break
            try:
                obj, end = decoder.raw_decode(raw, idx)
            except Exception:
                break
            _collect(obj)
            idx = end

    if thoughts:
        return "\n\n".join(thoughts)

    # Fallback: pull the first closed "thought" string if present.
    match = re.search(r'"thought"\s*:\s*"((?:\\.|[^"\\])*)"', raw)
    if match:
        try:
            return json.loads(f'"{match.group(1)}"')
        except Exception:
            return match.group(1).replace('\\"', '"').replace("\\n", "\n")
    return raw if not raw.lstrip().startswith("{") else ""


def _trace_snapshot(trace: ReActTrace, strategy: str) -> dict[str, Any]:
    """Produce a complete serialisable snapshot of the trace for checkpointing."""
    return trace.to_checkpoint(strategy=strategy)


def _preview_action_args(args: dict[str, Any], limit: int = 240) -> str:
    try:
        raw = json.dumps(args, ensure_ascii=False, sort_keys=True)
    except TypeError:
        raw = str(args)
    if len(raw) <= limit:
        return raw
    return raw[: limit - 3].rstrip() + "..."


def _candidate_score(turn: ReActTurn) -> tuple[int, int, float, int]:
    reviews = [review for review in turn.reviews if not review.abstained]
    if not reviews:
        return (0, 0, 0.0, 0)
    pass_count = sum(review.verdict == "PASS" for review in reviews)
    all_pass = int(pass_count == len(reviews))
    confidence = sum(review.confidence for review in reviews) / len(reviews)
    issue_count = sum(len(review.issues) for review in reviews)
    return (all_pass, pass_count, confidence, -issue_count)


def _needs_searching_fallback(observation: ToolObservation) -> bool:
    from math_agent.search.tavily import is_tavily_failure_message

    text = (observation.output or "").strip()
    return is_tavily_failure_message(text)
