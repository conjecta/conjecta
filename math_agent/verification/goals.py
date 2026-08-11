from __future__ import annotations

import time
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from math_agent.agent.formal_evidence import claims_match
from math_agent.verification.report import VerificationReport


@dataclass(frozen=True)
class SuccessCriteria:
    """Objective-level acceptance requirements for a solver run."""

    require_final_answer: bool = True
    require_formal_verification: bool = False
    verifier_sources: tuple[str, ...] = ("lean", "lean_check")
    min_confidence: float = 0.0
    max_open_issues: int = 0
    min_report_count: int = 0
    required_report_sources: tuple[str, ...] = ()
    # Confidence-weighted LLM reviewer voting: revision is required only when
    # the weighted FAIL votes reach the weighted PASS votes plus this margin
    # (0.0 lets ties favor FAIL). Formal/Lean reports veto directly.
    review_vote_margin: float = 0.0


@dataclass
class GoalRun:
    """A concrete attempt to solve one mathematical goal."""

    id: str
    problem: str
    criteria: SuccessCriteria = field(default_factory=SuccessCriteria)
    created_at: float = field(default_factory=time.time)
    artifacts: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        problem: str,
        criteria: SuccessCriteria | None = None,
    ) -> "GoalRun":
        return cls(
            id=uuid.uuid4().hex[:12],
            problem=problem,
            criteria=criteria or SuccessCriteria(),
        )

    def record_artifact(self, kind: str, artifact_id: str) -> None:
        self.artifacts.setdefault(kind, []).append(artifact_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "problem": self.problem,
            "criteria": {
                "require_final_answer": self.criteria.require_final_answer,
                "require_formal_verification": self.criteria.require_formal_verification,
                "verifier_sources": list(self.criteria.verifier_sources),
                "min_confidence": self.criteria.min_confidence,
                "max_open_issues": self.criteria.max_open_issues,
                "min_report_count": self.criteria.min_report_count,
                "required_report_sources": list(
                    self.criteria.required_report_sources
                ),
                "review_vote_margin": self.criteria.review_vote_margin,
            },
            "created_at": self.created_at,
            "artifacts": {key: list(value) for key, value in self.artifacts.items()},
        }


@dataclass(frozen=True)
class GoalEvaluation:
    """Structured outcome of checking a run against its success criteria."""

    status: str
    passed: bool
    issues: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "passed": self.passed,
            "issues": list(self.issues),
            "evidence": list(self.evidence),
            "next_actions": list(self.next_actions),
            "metadata": dict(self.metadata),
        }


class GoalEvaluator:
    """Evaluate whether solver artifacts satisfy a goal's success criteria."""

    def evaluate(
        self,
        run: GoalRun,
        *,
        final_answer: str | None,
        reports: Iterable[VerificationReport],
    ) -> GoalEvaluation:
        criteria = run.criteria
        report_list = list(reports)
        required_verifier_reports = self._relevant_reports(criteria, report_list)
        issues: list[str] = []
        evidence: list[str] = []
        next_actions: list[str] = []

        if criteria.require_final_answer and not (final_answer or "").strip():
            issues.append("Final answer is required.")

        if criteria.require_formal_verification and not required_verifier_reports:
            issues.append("Formal verification report is required.")
        elif criteria.require_formal_verification and not any(
            report.metadata.get("claim_bound") is True
            and isinstance(report.metadata.get("formal_evidence"), dict)
            and bool(report.metadata["formal_evidence"].get("id"))
            and claims_match(
                report.metadata["formal_evidence"].get("target_claim", ""),
                report.metadata.get("concluded_claim", run.problem),
            )
            for report in required_verifier_reports
        ):
            issues.append(
                "Formal verification evidence must be bound to the accepted claim."
            )
        elif (
            criteria.require_formal_verification
            and any(report.passed for report in required_verifier_reports)
            and not any(
                report.passed
                and isinstance(report.metadata.get("formal_evidence"), dict)
                and report.metadata["formal_evidence"].get("statement_bound") is True
                and bool(
                    report.metadata["formal_evidence"]
                    .get("primary_declaration", {})
                    .get("signature")
                )
                for report in required_verifier_reports
            )
        ):
            issues.append(
                "Formal verification evidence must identify the checked Lean declaration."
            )

        voting_reports = [
            report for report in report_list if not self._is_abstained(report)
        ]
        if len(voting_reports) < criteria.min_report_count:
            issues.append(
                "At least "
                f"{criteria.min_report_count} verification report(s) are required; "
                f"received {len(voting_reports)}."
            )
        # Abstained reviewers were attempted, so their sources still satisfy
        # explicit source requirements.
        available_sources = {report.source for report in report_list}
        for source in criteria.required_report_sources:
            if source not in available_sources:
                issues.append(f"Required verification report is missing: {source}.")

        blocked = False
        llm_report_count = 0
        llm_fail_weight = 0.0
        llm_pass_weight = 0.0
        llm_fail_issues: list[str] = []
        for report in voting_reports:
            if report.evidence:
                evidence.append(report.evidence)
            if self._is_infrastructure_block(report):
                blocked = True
                issues.extend(report.issues or ["Formal verifier is unavailable."])
                if not next_actions:
                    next_actions.append(
                        "Install/configure Lean or disable formal verification for this goal."
                    )
                continue
            if report.confidence < criteria.min_confidence:
                issues.append(
                    f"{report.source} confidence {report.confidence:.2f} is below "
                    f"required {criteria.min_confidence:.2f}."
                )
            if self._is_formal_source(report.source):
                # Deterministic/formal reports keep a hard veto on FAIL.
                if not report.passed:
                    issues.extend(
                        report.issues or [f"{report.source} verification failed."]
                    )
                if len(report.issues) > criteria.max_open_issues:
                    issues.extend(report.issues[criteria.max_open_issues :])
                continue
            # LLM reviewers cast a confidence-weighted vote instead of an
            # automatic veto; their issues stay on the report either way.
            llm_report_count += 1
            weight = max(0.0, float(report.confidence))
            if report.passed:
                llm_pass_weight += weight
            else:
                llm_fail_weight += weight
                llm_fail_issues.extend(
                    report.issues or [f"{report.source} verification failed."]
                )

        review_vote: dict[str, Any] | None = None
        if llm_report_count:
            needs_revision = (
                llm_fail_weight >= llm_pass_weight + criteria.review_vote_margin
            )
            review_vote = {
                "fail_weight": llm_fail_weight,
                "pass_weight": llm_pass_weight,
                "margin": criteria.review_vote_margin,
                "needs_revision": needs_revision,
            }
            if needs_revision:
                issues.extend(llm_fail_issues)

        if blocked:
            status = "blocked"
        elif issues:
            status = "needs_revision"
        else:
            status = "passed"

        return GoalEvaluation(
            status=status,
            passed=status == "passed",
            issues=self._dedupe(issues),
            evidence=evidence,
            next_actions=next_actions,
            metadata={
                "goal_id": run.id,
                "report_count": len(report_list),
                "relevant_report_count": len(required_verifier_reports),
                **({"review_vote": review_vote} if review_vote else {}),
            },
        )

    def _relevant_reports(
        self,
        criteria: SuccessCriteria,
        reports: list[VerificationReport],
    ) -> list[VerificationReport]:
        if not criteria.require_formal_verification:
            return reports
        accepted = set(criteria.verifier_sources)
        return [report for report in reports if report.source in accepted]

    def _is_infrastructure_block(self, report: VerificationReport) -> bool:
        return (
            report.metadata.get("lean_available") is False
            or report.metadata.get("failure_kind") == "lean_unavailable"
        )

    def _is_abstained(self, report: VerificationReport) -> bool:
        """Reviewer reports marked UNAVAILABLE cast no vote and raise no issue."""
        return report.metadata.get("verdict") == "UNAVAILABLE"

    def _is_formal_source(self, source: str) -> bool:
        lowered = source.lower()
        return "lean" in lowered or "formal" in lowered

    def _dedupe(self, values: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for value in values:
            if value and value not in seen:
                seen.add(value)
                result.append(value)
        return result
