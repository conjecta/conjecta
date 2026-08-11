"""Tests for pass@1 / pass@k summary reporting in math_agent.evaluation."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from math_agent.evaluation.models import EvalCase, TrialResult
from math_agent.evaluation.runner import pass_at_k_estimate, run_evaluation, summarize_results

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import evaluate_math_agent as eval_script


def _case(case_id: str) -> EvalCase:
    return EvalCase(id=case_id, problem="p", judge="exact", expected="4")


def _result(case_id: str, trial: int, correct: bool) -> TrialResult:
    return TrialResult(
        case_id=case_id,
        trial=trial,
        correct=correct,
        final_answer="4" if correct else "wrong",
        verification_status="best_effort",
        lean_proof_count=0,
        false_verified=False,
        latency_seconds=1.0,
        step_count=1,
        tool_call_count=1,
    )


def test_pass_at_k_estimate_edge_cases():
    assert pass_at_k_estimate(0, 4, 8) == 0.0
    assert pass_at_k_estimate(4, 4, 8) == 1.0
    assert pass_at_k_estimate(0, 0, 8) == 0.0
    # pass@1 is the per-case success rate.
    assert pass_at_k_estimate(1, 4, 1) == pytest.approx(0.25)
    # Unbiased estimator: 1 - C(n-c, k) / C(n, k).
    assert pass_at_k_estimate(1, 4, 2) == pytest.approx(1 - 3 / 6)
    assert pass_at_k_estimate(2, 4, 2) == pytest.approx(1 - 1 / 6)
    # k clamps to the trial count: pass@8 over 4 trials == "any correct".
    assert pass_at_k_estimate(1, 4, 8) == 1.0
    assert pass_at_k_estimate(1, 2, 8) == 1.0


def test_summarize_results_reports_pass_at_1_and_pass_at_k():
    cases = [_case("a"), _case("b")]
    results = [
        _result("a", 1, True),
        _result("a", 2, False),
        _result("a", 3, False),
        _result("a", 4, False),
        *(_result("b", trial, False) for trial in range(1, 5)),
    ]
    summary = summarize_results(cases, results, pass_k=8)
    assert summary.accuracy == pytest.approx(1 / 8)
    assert summary.pass_at_1 == pytest.approx(0.125)  # mean(1/4, 0)
    # k clamps to n=4: pass@8 == "any correct" == mean(1, 0).
    assert summary.pass_at_k == pytest.approx(0.5)
    assert summary.pass_k == 8

    summary_k2 = summarize_results(cases, results, pass_k=2)
    # Case a: 1 - C(3,2)/C(4,2) = 0.5; case b: 0.
    assert summary_k2.pass_at_k == pytest.approx(0.25)
    assert summary_k2.pass_k == 2


def test_summarize_results_default_pass_k_matches_legacy_semantics():
    """With k clamped to the trial count, pass@k stays "any trial correct"."""
    cases = [_case("a"), _case("b")]
    results = [
        _result("a", 1, False),
        _result("a", 2, True),
        _result("b", 1, False),
        _result("b", 2, False),
    ]
    summary = summarize_results(cases, results)
    assert summary.pass_at_k == pytest.approx(0.5)  # legacy pass@3-style value
    assert summary.pass_at_1 == pytest.approx(0.25)
    assert summary.pass_k == 8


def test_summary_to_dict_includes_pass_fields():
    summary = summarize_results([_case("a")], [_result("a", 1, True)], pass_k=8)
    data = summary.to_dict()
    assert data["pass_at_1"] == 1.0
    assert data["pass_at_k"] == 1.0
    assert data["pass_k"] == 8


def test_summarize_results_rejects_bad_pass_k():
    with pytest.raises(ValueError, match="pass_k"):
        summarize_results([_case("a")], [_result("a", 1, True)], pass_k=0)


@pytest.mark.asyncio
async def test_run_evaluation_forwards_pass_k():
    from types import SimpleNamespace

    calls = {"n": 0}

    async def solve(case):
        calls["n"] += 1
        return SimpleNamespace(
            final_answer="4" if calls["n"] == 1 else "wrong",
            verification_status="reviewed",
            lean_proofs=[],
            turns=[],
        )

    results, summary = await run_evaluation([_case("a")], solve, trials=2, pass_k=1)
    assert len(results) == 2
    assert summary.pass_at_k == pytest.approx(0.5)  # pass@1, not "any correct"
    assert summary.pass_at_1 == pytest.approx(0.5)
    assert summary.pass_k == 1


def test_pass_k_cli_flag():
    args = eval_script._parse_args(["--pass-k", "4"])
    assert args.pass_k == 4
    assert eval_script._parse_args([]).pass_k is None  # runner default (8) applies
    with pytest.raises(SystemExit):
        eval_script._parse_args(["--pass-k", "0"])
