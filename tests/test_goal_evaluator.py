import pytest

from math_agent.verification import (
    GoalEvaluator,
    GoalRun,
    SuccessCriteria,
    VerificationReport,
)


def test_goal_evaluator_passes_when_required_answer_and_formal_report_are_present():
    run = GoalRun.new(
        problem="Prove True.",
        criteria=SuccessCriteria(
            require_final_answer=True,
            require_formal_verification=True,
        ),
    )
    report = VerificationReport(
        source="lean",
        passed=True,
        confidence=1.0,
        metadata={
            "claim_bound": True,
            "formal_evidence": {
                "id": "formal-123",
                "target_claim": "Prove True.",
                "statement_bound": True,
                "primary_declaration": {
                    "name": "conjecta_target",
                    "signature": ": True",
                },
            },
        },
    )

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True is trivial.",
        reports=[report],
    )

    assert evaluation.status == "passed"
    assert evaluation.passed is True
    assert evaluation.issues == []


def test_goal_evaluator_requests_revision_for_missing_formal_report():
    run = GoalRun.new(
        problem="Prove True.",
        criteria=SuccessCriteria(
            require_final_answer=True,
            require_formal_verification=True,
        ),
    )

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True is trivial.",
        reports=[],
    )

    assert evaluation.status == "needs_revision"
    assert "Formal verification report is required." in evaluation.issues
    assert evaluation.passed is False


def test_goal_evaluator_rejects_unbound_formal_report():
    run = GoalRun.new(
        problem="Prove True.",
        criteria=SuccessCriteria(require_formal_verification=True),
    )

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True is trivial.",
        reports=[VerificationReport(source="lean", passed=True, confidence=1.0)],
    )

    assert evaluation.status == "needs_revision"
    assert evaluation.issues == [
        "Formal verification evidence must be bound to the accepted claim."
    ]


def test_goal_evaluator_blocks_when_lean_is_unavailable():
    run = GoalRun.new(
        problem="Prove True.",
        criteria=SuccessCriteria(require_formal_verification=True),
    )
    report = VerificationReport(
        source="lean",
        passed=False,
        issues=["lean executable is not available"],
        metadata={
            "claim_bound": True,
            "formal_evidence": {"id": "formal-unavailable"},
            "lean_available": False,
            "failure_kind": "lean_unavailable",
        },
    )

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True is trivial.",
        reports=[report],
    )

    assert evaluation.status == "blocked"
    assert evaluation.passed is False
    assert evaluation.next_actions == [
        "Install/configure Lean or disable formal verification for this goal."
    ]


def test_goal_evaluator_keeps_reviewer_failures_when_formal_verification_is_required():
    run = GoalRun.new(
        problem="Prove True.",
        criteria=SuccessCriteria(require_formal_verification=True),
    )

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True is trivial.",
        reports=[
            VerificationReport(
                source="lean",
                passed=True,
                confidence=1.0,
                metadata={
                    "claim_bound": True,
                    "formal_evidence": {
                        "id": "formal-123",
                        "target_claim": "Prove True.",
                        "statement_bound": True,
                        "primary_declaration": {
                            "name": "conjecta_target",
                            "signature": ": True",
                        },
                    },
                },
            ),
            VerificationReport(
                source="critic",
                passed=False,
                issues=["The prose conclusion has a logical gap."],
                confidence=1.0,
            ),
        ],
    )

    assert evaluation.status == "needs_revision"
    assert evaluation.passed is False
    assert evaluation.issues == ["The prose conclusion has a logical gap."]
    assert evaluation.metadata["report_count"] == 2
    assert evaluation.metadata["relevant_report_count"] == 1


def test_goal_evaluator_requires_configured_reviewer_quorum():
    run = GoalRun.new(
        problem="Prove True.",
        criteria=SuccessCriteria(
            min_report_count=2,
            required_report_sources=("critic", "fidelity"),
        ),
    )

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True.",
        reports=[VerificationReport(source="critic", passed=True)],
    )

    assert evaluation.passed is False
    assert any("2 verification report" in issue for issue in evaluation.issues)
    assert "Required verification report is missing: fidelity." in evaluation.issues


def _review(
    source: str,
    passed: bool,
    confidence: float,
    issues: list[str] | None = None,
) -> VerificationReport:
    return VerificationReport(
        source=source,
        passed=passed,
        issues=list(issues or []),
        confidence=confidence,
        metadata={"verdict": "PASS" if passed else "FAIL"},
    )


def test_llm_reviewer_all_fail_requires_revision():
    run = GoalRun.new(problem="Prove True.")

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True.",
        reports=[
            _review("critic", False, 0.9, ["gap in the proof"]),
            _review("fidelity", False, 0.8, ["answers a different claim"]),
        ],
    )

    assert evaluation.status == "needs_revision"
    assert evaluation.issues == ["gap in the proof", "answers a different claim"]
    vote = evaluation.metadata["review_vote"]
    assert vote["fail_weight"] == pytest.approx(1.7)
    assert vote["pass_weight"] == 0.0
    assert vote["margin"] == 0.0
    assert vote["needs_revision"] is True


def test_llm_reviewer_all_pass_accepted():
    run = GoalRun.new(problem="Prove True.")

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True.",
        reports=[
            _review("critic", True, 0.9),
            _review("fidelity", True, 0.8),
        ],
    )

    assert evaluation.status == "passed"
    assert evaluation.issues == []
    assert evaluation.metadata["review_vote"]["needs_revision"] is False


def test_llm_reviewer_weighted_pass_outvotes_fail():
    run = GoalRun.new(problem="Prove True.")

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True.",
        reports=[
            _review("critic", False, 0.4, ["minor doubt"]),
            _review("fidelity", True, 0.9),
        ],
    )

    # The issue stays on the report but does not force a revision.
    assert evaluation.status == "passed"
    assert evaluation.issues == []
    assert evaluation.metadata["review_vote"] == {
        "fail_weight": 0.4,
        "pass_weight": 0.9,
        "margin": 0.0,
        "needs_revision": False,
    }


def test_llm_reviewer_weighted_fail_outvotes_pass():
    run = GoalRun.new(problem="Prove True.")

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True.",
        reports=[
            _review("critic", False, 0.9, ["fatal gap"]),
            _review("fidelity", True, 0.4),
        ],
    )

    assert evaluation.status == "needs_revision"
    assert evaluation.issues == ["fatal gap"]


def test_llm_reviewer_tie_favors_fail():
    run = GoalRun.new(problem="Prove True.")

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True.",
        reports=[
            _review("critic", False, 0.7, ["uncertain gap"]),
            _review("fidelity", True, 0.7),
        ],
    )

    assert evaluation.status == "needs_revision"
    assert evaluation.issues == ["uncertain gap"]


def test_abstained_reviewer_casts_no_vote_and_is_not_counted():
    run = GoalRun.new(
        problem="Prove True.",
        criteria=SuccessCriteria(min_report_count=1),
    )
    abstained = VerificationReport(
        source="completeness",
        passed=False,
        confidence=0.0,
        metadata={"verdict": "UNAVAILABLE"},
    )

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True.",
        reports=[abstained, _review("critic", True, 0.9)],
    )

    assert evaluation.status == "passed"
    assert evaluation.issues == []


def test_all_reviewers_abstained_passes_unreviewed():
    run = GoalRun.new(problem="Prove True.")
    abstained = [
        VerificationReport(
            source=name,
            passed=False,
            confidence=0.0,
            metadata={"verdict": "UNAVAILABLE"},
        )
        for name in ("critic", "fidelity")
    ]

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True.",
        reports=abstained,
    )

    assert evaluation.status == "passed"
    assert evaluation.issues == []
    assert "review_vote" not in evaluation.metadata


def test_formal_report_fail_vetoes_despite_llm_pass_weight():
    run = GoalRun.new(problem="Prove True.")

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True.",
        reports=[
            VerificationReport(
                source="formal",
                passed=False,
                issues=["Lean formalization failed: type mismatch"],
                confidence=0.9,
                metadata={"verdict": "FAIL"},
            ),
            _review("critic", True, 1.0),
            _review("fidelity", True, 1.0),
        ],
    )

    assert evaluation.status == "needs_revision"
    assert evaluation.issues == ["Lean formalization failed: type mismatch"]


def test_goal_evaluator_rejects_formal_evidence_without_checked_declaration():
    run = GoalRun.new(
        problem="Prove True.",
        criteria=SuccessCriteria(require_formal_verification=True),
    )
    report = VerificationReport(
        source="lean",
        passed=True,
        metadata={
            "claim_bound": True,
            "formal_evidence": {
                "id": "formal-unparsed",
                "target_claim": "Prove True.",
            },
        },
    )

    evaluation = GoalEvaluator().evaluate(
        run,
        final_answer="True.",
        reports=[report],
    )

    assert evaluation.passed is False
    assert evaluation.issues == [
        "Formal verification evidence must identify the checked Lean declaration."
    ]
