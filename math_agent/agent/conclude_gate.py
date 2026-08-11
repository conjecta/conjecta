"""Conclude gate: every check and decision around a `conclude` action.

Extracted from ``ReActAgent.solve`` so the main loop only orchestrates. This
is a pure refactor — formal-evidence binding, diagram requirements, reviewer
panel triggering (including skip reasons), goal evaluation, revision
counting, HITL pauses, and budget bookkeeping behave exactly as before.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any, Callable

from math_agent.agent.formal_evidence import (
    FORMAL_ACTIONS,
    claims_match,
    evidence_id_from_observation,
    formal_evidence_metadata,
)
from math_agent.agent.human_interaction import (
    create_interaction,
    hitl_should_pause,
    pause_for_human,
)
from math_agent.agent.react_state import (
    Action,
    ReActTrace,
    ReActTurn,
    ToolObservation,
)
from math_agent.agent.supervisor_intake import requires_diagram
from math_agent.agent.verification import (
    VerificationOutcome,
    legacy_label,
    outcome_best_effort,
    outcome_blocked,
    outcome_reviewed,
    outcome_unreviewed,
    outcome_verified,
)
from math_agent.types import EventCallback
from math_agent.verification import (
    GoalEvaluation,
    GoalEvaluator,
    GoalRun,
    SuccessCriteria,
    report_from_review_result,
    report_from_tool_observation,
)

if TYPE_CHECKING:
    from math_agent.agent.react_agent import ReActAgent


@dataclass
class ConcludeDecision:
    """Result of the conclude gate for one conclude action.

    ``revise=True`` means the main loop should continue (rejected/misrouted
    conclusion or another revision round); otherwise the loop breaks with the
    carried terminal values.
    """

    revise: bool
    candidate_answer: str
    verification_issues: list[str] = field(default_factory=list)
    conclusion_revisions: int = 0
    accepted_formal_evidence_id: str = ""
    final_answer: str = ""
    outcome: VerificationOutcome | None = None
    verification_status: str = ""
    wall_time_exhausted: bool = False
    terminated_without_synthesis: bool = False


class ConcludeGate:
    """Owns the conclude-action pipeline for one solve run."""

    def __init__(
        self,
        agent: ReActAgent,
        *,
        run_log: logging.Logger,
        emit: EventCallback,
        on_checkpoint: Callable[[dict[str, Any]], None] | None,
        deadline: float,
        goal_run: GoalRun,
        goal_evaluator: GoalEvaluator,
        require_formal_verification: bool,
    ) -> None:
        self.agent = agent
        self.run_log = run_log
        self.emit = emit
        self.on_checkpoint = on_checkpoint
        self.deadline = deadline
        self.goal_run = goal_run
        self.goal_evaluator = goal_evaluator
        self.require_formal_verification = require_formal_verification

    def _revise(
        self,
        *,
        candidate_answer: str,
        verification_issues: list[str],
        conclusion_revisions: int,
        accepted_formal_evidence_id: str,
    ) -> ConcludeDecision:
        return ConcludeDecision(
            revise=True,
            candidate_answer=candidate_answer,
            verification_issues=verification_issues,
            conclusion_revisions=conclusion_revisions,
            accepted_formal_evidence_id=accepted_formal_evidence_id,
        )

    async def handle(
        self,
        *,
        trace: ReActTrace,
        action: Action,
        thought: str,
        step_num: int,
        candidate_answer: str,
        action_confidence: float | None,
        conclusion_revisions: int,
        verification_issues: list[str],
        accepted_formal_evidence_id: str,
    ) -> ConcludeDecision:
        # Lazy import: react_agent imports this module, so the shared helpers
        # can only be pulled in at call time.
        from math_agent.agent.react_agent import (
            _await_phase,
            _has_diagram_for_conclusion,
            _trace_snapshot,
        )

        agent = self.agent
        config = agent.config
        emit = self.emit
        run_log = self.run_log
        on_checkpoint = self.on_checkpoint
        goal_run = self.goal_run
        require_formal_verification = self.require_formal_verification

        candidate_answer = action.args.get("answer", "")
        previous_conclusion_index = max(
            (
                index
                for index, prior_turn in enumerate(trace.turns)
                if prior_turn.action.name == "conclude"
            ),
            default=-1,
        )
        scoped_formal_turns = [
            prior_turn
            for prior_turn in trace.turns[previous_conclusion_index + 1 :]
            if prior_turn.action.name in FORMAL_ACTIONS
        ]
        requested_evidence_id = str(
            action.args.get("evidence_id") or ""
        ).strip()
        bound_formal_turn = next(
            (
                prior_turn
                for prior_turn in reversed(scoped_formal_turns)
                if evidence_id_from_observation(prior_turn.observation)
                == requested_evidence_id
            ),
            None,
        )
        concluded_claim = trace.current_goal or trace.problem
        bound_metadata = (
            dict(bound_formal_turn.observation.metadata)
            if bound_formal_turn is not None
            else {}
        )
        if require_formal_verification and bound_formal_turn is None:
            formal_tool_names = ", ".join(sorted(FORMAL_ACTIONS))
            issue = (
                "Formal verification is required before concluding. Call one "
                f"of the formal proof tools ({formal_tool_names}), then "
                "conclude with the exact returned Formal evidence ID."
            )
            turn = ReActTurn(
                thought=thought,
                action=action,
                observation=ToolObservation(
                    success=False,
                    output=issue,
                    error="missing_formal_evidence",
                ),
                step_num=step_num,
            )
            trace.turns.append(turn)
            trace.next_step_num = step_num + 1
            verification_issues = ["Formal verification report is required."]
            await agent._emit_turn(turn, emit)
            if on_checkpoint:
                on_checkpoint(_trace_snapshot(trace, strategy="react"))
            return self._revise(
                candidate_answer=candidate_answer,
                verification_issues=verification_issues,
                conclusion_revisions=conclusion_revisions,
                accepted_formal_evidence_id=accepted_formal_evidence_id,
            )
        plot_available = agent._registered_tool_name("plot_figure") is not None
        if (
            plot_available
            and requires_diagram(trace.problem)
            and not _has_diagram_for_conclusion(
                trace,
                candidate_answer=str(candidate_answer or ""),
                after_index=previous_conclusion_index,
            )
        ):
            issue = (
                "A diagram was requested. Call plot_figure to create the "
                "figure, then conclude with the returned markdown image "
                "link embedded on its own line in the answer."
            )
            turn = ReActTurn(
                thought=thought,
                action=action,
                observation=ToolObservation(
                    success=False,
                    output=issue,
                    error="missing_diagram",
                ),
                step_num=step_num,
            )
            trace.turns.append(turn)
            trace.next_step_num = step_num + 1
            await agent._emit_turn(turn, emit)
            if on_checkpoint:
                on_checkpoint(_trace_snapshot(trace, strategy="react"))
            return self._revise(
                candidate_answer=candidate_answer,
                verification_issues=verification_issues,
                conclusion_revisions=conclusion_revisions,
                accepted_formal_evidence_id=accepted_formal_evidence_id,
            )
        observation = ToolObservation(
            success=True,
            output=f"Conclusion: {candidate_answer}",
            lean_code=(
                bound_formal_turn.observation.lean_code
                if bound_formal_turn is not None
                else None
            ),
            metadata=bound_metadata,
        )
        turn = ReActTurn(
            thought=thought,
            action=action,
            observation=observation,
            step_num=step_num,
        )
        await emit(
            {
                "type": "step",
                "step_num": step_num,
                "action": action.name,
                "thought": thought,
                "observation": observation.output[:8000],
                "verified": None,
                "reviews": [],
            }
        )
        skip_reason = await agent._review_skip_reason(
            problem=trace.problem,
            action_confidence=action_confidence,
            require_formal_verification=require_formal_verification,
            run_log=run_log,
        )
        skip_review = bool(agent.reviewers) and skip_reason is not None
        run_review_panel = bool(agent.reviewers) and not skip_review
        if skip_review:
            run_log.info("Skipping reviewer panel (%s)", skip_reason)
            await emit(
                {
                    "type": "stage_status",
                    "stage": "accepting",
                    "message": (
                        "简单问题，跳过审查。"
                        if skip_reason == "easy_prompt"
                        else (
                            f"置信度较高（{(action_confidence or 0.0):.0%}），跳过审查。"
                        )
                    ),
                    "ui": "status_bar",
                }
            )
            turn.reviews = []
            observation.metadata = {
                **dict(observation.metadata),
                "skipped_review": True,
                "skip_review_reason": skip_reason,
                "action_confidence": action_confidence,
            }
            turn.observation = observation
        elif run_review_panel:
            await emit(
                {
                    "type": "stage_status",
                    "stage": "reviewing",
                    "message": "正在审查最终答案…",
                    "ui": "status_bar",
                }
            )
            try:
                turn = await _await_phase(
                    agent._evaluate_conclusion_candidates(
                        turn,
                        trace,
                        run_log,
                        require_formal_verification=require_formal_verification,
                        emit=emit,
                    ),
                    deadline=self.deadline,
                    run_log=run_log,
                    phase="reviewer_panel",
                    model_role="critic",
                )
                candidate_answer = turn.action.args.get("answer", candidate_answer)
            except asyncio.TimeoutError:
                trace.turns.append(turn)
                trace.next_step_num = step_num + 1
                trace.budget_consumption["conclusion_revisions"] = max(
                    conclusion_revisions,
                    len(
                        [
                            prior_turn
                            for prior_turn in trace.turns
                            if prior_turn.action.name == "conclude"
                        ]
                    ),
                )
                await agent._emit_turn(turn, emit)
                if on_checkpoint:
                    on_checkpoint(_trace_snapshot(trace, strategy="react"))
                return ConcludeDecision(
                    revise=False,
                    candidate_answer=candidate_answer,
                    verification_issues=verification_issues,
                    conclusion_revisions=conclusion_revisions,
                    accepted_formal_evidence_id=accepted_formal_evidence_id,
                    wall_time_exhausted=True,
                    terminated_without_synthesis=True,
                )
        else:
            turn.reviews = []
        trace.turns.append(turn)
        trace.next_step_num = step_num + 1
        trace.budget_consumption["conclusion_revisions"] = max(
            conclusion_revisions,
            len([t for t in trace.turns if t.action.name == "conclude"]),
        )
        await agent._emit_turn(turn, emit)
        if on_checkpoint:
            on_checkpoint(_trace_snapshot(trace, strategy="react"))
        formal_reports = []
        for formal_turn in (
            [bound_formal_turn] if bound_formal_turn is not None else []
        ):
            unavailable = (
                "unavailable" in (formal_turn.observation.output or "").lower()
            )
            formal_evidence = formal_evidence_metadata(formal_turn.observation)
            report_metadata = {
                "formal_evidence": formal_evidence,
                "claim_bound": claims_match(
                    formal_evidence.get("target_claim", ""),
                    concluded_claim,
                ),
                "concluded_claim": concluded_claim.strip(),
            }
            if unavailable:
                report_metadata.update(
                    {
                        "lean_available": False,
                        "failure_kind": "lean_unavailable",
                    }
                )
            formal_reports.append(
                report_from_tool_observation(
                    formal_turn.observation,
                    source=(
                        "lean"
                        if formal_turn.action.name == "formalize"
                        else formal_turn.action.name
                    ),
                    metadata=report_metadata,
                )
            )
        review_reports = (
            []
            if skip_review
            else [report_from_review_result(review) for review in turn.reviews]
        )
        if skip_review:
            evaluate_criteria = SuccessCriteria(
                require_final_answer=True,
                require_formal_verification=require_formal_verification,
                min_report_count=0,
                required_report_sources=(),
            )
        else:
            # Abstained (unavailable) reviewers cast no vote and are
            # excused from the quorum/source requirements.
            voting_reviews = [
                review for review in turn.reviews if not review.abstained
            ]
            evaluate_criteria = replace(
                goal_run.criteria,
                min_report_count=len(voting_reviews),
                required_report_sources=tuple(
                    review.reviewer for review in voting_reviews
                ),
            )
        evaluation = self.goal_evaluator.evaluate(
            GoalRun.new(problem=goal_run.problem, criteria=evaluate_criteria),
            final_answer=candidate_answer,
            reports=review_reports + formal_reports,
        )
        if skip_review and evaluation.passed:
            evaluation = GoalEvaluation(
                status="passed",
                passed=True,
                issues=[],
                evidence=list(evaluation.evidence),
                next_actions=[],
                metadata={
                    **dict(evaluation.metadata),
                    "skipped_review": True,
                    "skip_review_reason": skip_reason,
                    "action_confidence": action_confidence,
                },
            )
        verification_issues = list(evaluation.issues)
        if evaluation.passed:
            final_answer = candidate_answer
            if require_formal_verification:
                outcome = outcome_verified(
                    evidence_ids=(accepted_formal_evidence_id,)
                    if accepted_formal_evidence_id
                    else ()
                )
            elif skip_review:
                outcome = outcome_unreviewed()
            elif any(not review.abstained for review in turn.reviews):
                outcome = outcome_reviewed()
            else:
                outcome = outcome_unreviewed()
            verification_status = legacy_label(outcome)
            if require_formal_verification and bound_formal_turn is not None:
                accepted_formal_evidence_id = requested_evidence_id
            trace.proof_graph.mark_proved(
                trace.proof_graph.root_id,
                evidence_id=accepted_formal_evidence_id,
            )
            return ConcludeDecision(
                revise=False,
                candidate_answer=candidate_answer,
                verification_issues=verification_issues,
                conclusion_revisions=conclusion_revisions,
                accepted_formal_evidence_id=accepted_formal_evidence_id,
                final_answer=final_answer,
                outcome=outcome,
                verification_status=verification_status,
            )
        if evaluation.status == "blocked":
            if (
                hitl_should_pause(config.hitl, "reviewer_block")
                and config.hitl.ask_on_reviewer_block
                and len(trace.human_decisions)
                < config.hitl.max_interrupts_per_run
            ):
                interaction = create_interaction(
                    kind="reviewer_block",
                    stage="reviewing",
                    question="审阅器认为当前结论仍有关键问题。要接受当前尽力结果，还是提供修改意见？",
                    details={
                        "candidate_answer": candidate_answer,
                        "issues": verification_issues,
                    },
                )
                trace.pending_interaction = interaction
                if on_checkpoint:
                    on_checkpoint(_trace_snapshot(trace, strategy="react"))
                pause_for_human(trace, interaction)
            final_answer = candidate_answer
            outcome = outcome_blocked(limitations=tuple(verification_issues))
            verification_status = legacy_label(outcome)
            return ConcludeDecision(
                revise=False,
                candidate_answer=candidate_answer,
                verification_issues=verification_issues,
                conclusion_revisions=conclusion_revisions,
                accepted_formal_evidence_id=accepted_formal_evidence_id,
                final_answer=final_answer,
                outcome=outcome,
                verification_status=verification_status,
            )
        if config.max_conclusion_revisions >= 0 and (
            conclusion_revisions >= config.max_conclusion_revisions
        ):
            if (
                hitl_should_pause(config.hitl, "reviewer_block")
                and config.hitl.ask_on_reviewer_block
                and len(trace.human_decisions)
                < config.hitl.max_interrupts_per_run
            ):
                interaction = create_interaction(
                    kind="reviewer_block",
                    stage="reviewing",
                    question="自动修订预算已经用完。要接受当前尽力结果，还是补充指导后再试一次？",
                    details={
                        "candidate_answer": candidate_answer,
                        "issues": verification_issues,
                    },
                )
                trace.pending_interaction = interaction
                if on_checkpoint:
                    on_checkpoint(_trace_snapshot(trace, strategy="react"))
                pause_for_human(trace, interaction)
            final_answer = candidate_answer
            outcome = outcome_best_effort(limitations=tuple(verification_issues))
            verification_status = legacy_label(outcome)
            return ConcludeDecision(
                revise=False,
                candidate_answer=candidate_answer,
                verification_issues=verification_issues,
                conclusion_revisions=conclusion_revisions,
                accepted_formal_evidence_id=accepted_formal_evidence_id,
                final_answer=final_answer,
                outcome=outcome,
                verification_status=verification_status,
            )
        return self._revise(
            candidate_answer=candidate_answer,
            verification_issues=verification_issues,
            conclusion_revisions=conclusion_revisions + 1,
            accepted_formal_evidence_id=accepted_formal_evidence_id,
        )
