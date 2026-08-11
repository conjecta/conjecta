from math_agent.agent.react_state import ReviewResult, ToolObservation
from math_agent.lean.result import LeanResult
from math_agent.lean.verifier import LeanCheckResult, LeanDiagnostic
from math_agent.verification import (
    VerificationReport,
    report_from_lean_result,
    report_from_review_result,
    report_from_tool_observation,
)


def test_report_from_review_result_preserves_reviewer_fields():
    review = ReviewResult(
        reviewer="critic",
        verdict="FAIL",
        issues=["gap"],
        suggestions=["justify the implication"],
        confidence=0.8,
    )

    report = report_from_review_result(review)

    assert report == VerificationReport(
        source="critic",
        passed=False,
        issues=["gap"],
        suggestions=["justify the implication"],
        confidence=0.8,
        metadata={"verdict": "FAIL"},
    )
    assert report.to_review_result().verdict == "FAIL"


def test_report_from_lean_result_uses_errors_and_gate_metadata():
    result = LeanResult(
        success=False,
        errors=["static placeholder gate found blocked tokens: sorry"],
        static_ok=False,
        blocked_tokens=["sorry"],
        failure_kind="placeholder",
    )

    report = report_from_lean_result(result)

    assert report.source == "lean"
    assert report.passed is False
    assert report.issues == ["static placeholder gate found blocked tokens: sorry"]
    assert report.suggestions == ["Remove blocked Lean placeholder tokens: sorry."]
    assert report.metadata["static_ok"] is False
    assert report.metadata["failure_kind"] == "placeholder"


def test_report_from_lean_check_result_uses_diagnostics_when_no_errors():
    check = LeanCheckResult(
        lean_file="Proof.lean",
        lean_available=True,
        static_ok=True,
        verification_ok=False,
        returncode=1,
        diagnostics=(
            LeanDiagnostic(
                kind="type_mismatch",
                message="type mismatch",
                line=3,
                column=12,
            ),
        ),
    )

    report = check.to_verification_report()

    assert report.passed is False
    assert report.issues == ["type mismatch"]
    assert report.metadata["diagnostics"][0]["kind"] == "type_mismatch"
    assert report.metadata["returncode"] == 1


def test_report_from_tool_observation_uses_error_before_output():
    observation = ToolObservation(
        success=False,
        output="full compiler output",
        error="type mismatch",
    )

    report = report_from_tool_observation(observation, source="lean_check")

    assert report.source == "lean_check"
    assert report.passed is False
    assert report.issues == ["type mismatch"]
    assert report.evidence == "full compiler output"
