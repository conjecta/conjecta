import pytest

from math_agent.evaluation.models import EvalCase, TrialResult
from math_agent.evaluation.runner import run_evaluation

CASE = EvalCase(id="c1", problem="compute 6*7", judge="exact", expected="42")


class _Solution:
    verification_status = "best_effort"
    final_answer = "42"
    lean_proofs: list = []
    turns: list = []
    metadata = {
        "eval_usage": {
            "input_tokens": 10,
            "output_tokens": 5,
            "total_tokens": 15,
            "llm_calls": 2,
        }
    }


@pytest.mark.asyncio
async def test_trial_records_token_usage():
    async def solve(case):
        return _Solution()

    results, summary = await run_evaluation([CASE], solve)
    assert results[0].input_tokens == 10
    assert results[0].total_tokens == 15
    assert results[0].llm_calls == 2
    assert summary.average_total_tokens == 15.0
    assert summary.median_total_tokens == 15.0
    assert summary.average_llm_calls == 2.0


def test_trial_result_usage_fields_serialize():
    row = TrialResult(
        case_id="c",
        trial=1,
        correct=True,
        final_answer="",
        verification_status="best_effort",
        lean_proof_count=0,
        false_verified=False,
        latency_seconds=0.1,
        step_count=1,
        tool_call_count=0,
        input_tokens=1,
        output_tokens=2,
        total_tokens=3,
        llm_calls=4,
    ).to_dict()
    assert row["input_tokens"] == 1
    assert row["output_tokens"] == 2
    assert row["total_tokens"] == 3
    assert row["llm_calls"] == 4
