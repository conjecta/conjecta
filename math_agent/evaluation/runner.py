from __future__ import annotations

import json
import math
import statistics
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

from math_agent.evaluation.judges import judge_solution
from math_agent.evaluation.models import EvalCase, EvalSummary, TrialResult


SolveFn = Callable[[EvalCase], Awaitable[Any]]


def load_cases(path: str | Path) -> list[EvalCase]:
    cases: list[EvalCase] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                raw = json.loads(text)
                if not isinstance(raw, dict):
                    raise ValueError("row must be a JSON object")
                cases.append(EvalCase.from_dict(raw))
            except Exception as exc:
                raise ValueError(
                    f"Invalid evaluation row {line_number} in {path}: {exc}"
                ) from exc
    if not cases:
        raise ValueError(f"Evaluation dataset {path} contains no cases.")
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError(f"Evaluation dataset {path} contains duplicate case ids.")
    return cases


async def run_evaluation(
    cases: Iterable[EvalCase],
    solve: SolveFn,
    *,
    trials: int = 1,
    pass_k: int = 8,
) -> tuple[list[TrialResult], EvalSummary]:
    case_list = list(cases)
    if not case_list:
        raise ValueError("At least one evaluation case is required.")
    if trials < 1:
        raise ValueError("trials must be at least 1.")
    if pass_k < 1:
        raise ValueError("pass_k must be at least 1.")

    results: list[TrialResult] = []
    for case in case_list:
        for trial in range(1, trials + 1):
            started = time.monotonic()
            try:
                solution = await solve(case)
                correct = judge_solution(case, solution)
                status = str(getattr(solution, "verification_status", "best_effort"))
                lean_proofs = list(getattr(solution, "lean_proofs", []) or [])
                turns = list(getattr(solution, "turns", []) or [])
                research = _research_metadata(solution)
                usage = _eval_usage(solution)
                base_distribution = _tool_distribution(turns)
                tool_distribution = _merge_counts(
                    base_distribution,
                    research.get("tool_call_distribution", {}),
                )
                base_tool_calls = sum(base_distribution.values())
                worker_tool_calls = _coerce_nonnegative_int(
                    research.get("worker_tool_calls")
                )
                lemma_attempts, lemma_successes = _prove_by_lemmas_stats(
                    turns, tool_distribution, status
                )
                worker_steps = _coerce_nonnegative_int(research.get("worker_steps"))
                results.append(
                    TrialResult(
                        case_id=case.id,
                        trial=trial,
                        correct=correct,
                        final_answer=str(getattr(solution, "final_answer", "") or ""),
                        verification_status=status,
                        lean_proof_count=len(lean_proofs),
                        false_verified=status == "verified" and not correct,
                        latency_seconds=time.monotonic() - started,
                        step_count=len(turns) + worker_steps,
                        tool_call_count=base_tool_calls + worker_tool_calls,
                        planned_goal_count=_coerce_nonnegative_int(
                            research.get("planned_goal_count")
                        ),
                        proved_goal_count=_coerce_nonnegative_int(
                            research.get("proved_goal_count")
                        ),
                        prove_by_lemmas_attempts=lemma_attempts,
                        prove_by_lemmas_successes=lemma_successes,
                        research_goal_rounds=_coerce_nonnegative_int(
                            research.get("research_goal_rounds")
                        ),
                        counterexample_count=_coerce_nonnegative_int(
                            research.get("counterexample_count")
                        ),
                        replan_count=_coerce_nonnegative_int(
                            research.get("replan_count")
                        ),
                        peak_parallel_goals=_coerce_nonnegative_int(
                            research.get("peak_parallel_goals")
                        ),
                        tool_call_distribution=tool_distribution,
                        wall_time_breakdown=_float_mapping(
                            research.get("wall_time_breakdown")
                        ),
                        tool_time_breakdown=_tool_time_breakdown(solution),
                        input_tokens=usage["input_tokens"],
                        output_tokens=usage["output_tokens"],
                        total_tokens=usage["total_tokens"],
                        llm_calls=usage["llm_calls"],
                    )
                )
            except Exception as exc:
                results.append(
                    TrialResult(
                        case_id=case.id,
                        trial=trial,
                        correct=False,
                        final_answer="",
                        verification_status="error",
                        lean_proof_count=0,
                        false_verified=False,
                        latency_seconds=time.monotonic() - started,
                        step_count=0,
                        tool_call_count=0,
                        error=str(exc),
                    )
                )
    return results, summarize_results(case_list, results, pass_k=pass_k)


def pass_at_k_estimate(num_correct: int, num_trials: int, k: int) -> float:
    """Unbiased pass@k estimator for one case: 1 - C(n-c, k) / C(n, k).

    k is clamped to the trial count n, so with k >= n this reduces to "any
    trial correct" — the historical pass_at_k semantics (e.g. pass@3 for a
    trials=3 run stays pass@3).
    """
    if num_trials <= 0 or num_correct <= 0:
        return 0.0
    k = max(1, min(k, num_trials))
    failures = num_trials - num_correct
    if failures < k:
        return 1.0
    return 1.0 - math.comb(failures, k) / math.comb(num_trials, k)


def summarize_results(
    cases: list[EvalCase],
    results: list[TrialResult],
    *,
    pass_k: int = 8,
) -> EvalSummary:
    if pass_k < 1:
        raise ValueError("pass_k must be at least 1.")
    by_case: dict[str, list[TrialResult]] = defaultdict(list)
    for result in results:
        by_case[result.case_id].append(result)
    verified = [
        result for result in results if result.verification_status == "verified"
    ]
    false_verified = [result for result in verified if result.false_verified]
    latencies = [result.latency_seconds for result in results]
    by_tag: dict[str, dict[str, Any]] = {}
    case_by_id = {case.id: case for case in cases}
    tagged_results: dict[str, list[TrialResult]] = defaultdict(list)
    for result in results:
        for tag in case_by_id[result.case_id].tags:
            tagged_results[tag].append(result)
    for tag, tag_results in tagged_results.items():
        by_tag[tag] = {
            "accuracy": _mean(result.correct for result in tag_results),
            "trial_count": float(len(tag_results)),
            "lemma_success_rate": _lemma_success_rate(tag_results),
            "average_steps": _mean(result.step_count for result in tag_results),
            "average_tool_calls": _mean(
                result.tool_call_count for result in tag_results
            ),
            "counterexample_trigger_rate": _mean(
                result.counterexample_count > 0 for result in tag_results
            ),
            "average_latency_seconds": _mean(
                result.latency_seconds for result in tag_results
            ),
            "tool_call_distribution": _aggregate_tool_distribution(tag_results),
            "wall_time_breakdown": _average_breakdown(tag_results, "wall_time_breakdown"),
            "tool_time_breakdown": _average_breakdown(tag_results, "tool_time_breakdown"),
        }
    return EvalSummary(
        case_count=len(cases),
        trial_count=len(results),
        accuracy=_mean(result.correct for result in results),
        pass_at_k=_mean(
            pass_at_k_estimate(
                sum(1 for result in by_case[case.id] if result.correct),
                len(by_case[case.id]),
                pass_k,
            )
            for case in cases
        ),
        pass_at_1=_mean(
            pass_at_k_estimate(
                sum(1 for result in by_case[case.id] if result.correct),
                len(by_case[case.id]),
                1,
            )
            for case in cases
        ),
        pass_k=pass_k,
        verified_count=len(verified),
        false_verified_count=len(false_verified),
        false_verified_rate=(len(false_verified) / len(verified) if verified else 0.0),
        average_latency_seconds=statistics.fmean(latencies) if latencies else 0.0,
        p95_latency_seconds=_percentile(latencies, 0.95),
        average_steps=_mean(result.step_count for result in results),
        average_tool_calls=_mean(result.tool_call_count for result in results),
        by_tag=by_tag,
        lemma_success_rate=_lemma_success_rate(results),
        average_research_goal_rounds=_mean(
            result.research_goal_rounds for result in results
        ),
        counterexample_trigger_rate=_mean(
            result.counterexample_count > 0 for result in results
        ),
        average_peak_parallel_goals=_mean(
            result.peak_parallel_goals for result in results
        ),
        tool_call_distribution=_aggregate_tool_distribution(results),
        wall_time_breakdown=_average_breakdown(results, "wall_time_breakdown"),
        tool_time_breakdown=_average_breakdown(results, "tool_time_breakdown"),
        average_input_tokens=_mean(result.input_tokens for result in results),
        average_total_tokens=_mean(result.total_tokens for result in results),
        median_total_tokens=(
            float(statistics.median([r.total_tokens for r in results])) if results else 0.0
        ),
        average_llm_calls=_mean(result.llm_calls for result in results),
    )


def write_results(
    path: str | Path,
    results: list[TrialResult],
    summary: EvalSummary,
) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as handle:
        for result in results:
            handle.write(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")
        handle.write(
            json.dumps(
                {"type": "summary", **summary.to_dict()},
                ensure_ascii=False,
            )
            + "\n"
        )


def _mean(values: Iterable[float | int | bool]) -> float:
    items = [float(value) for value in values]
    return statistics.fmean(items) if items else 0.0


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]


def _research_metadata(solution: Any) -> dict[str, Any]:
    metadata = getattr(solution, "metadata", {})
    if isinstance(metadata, dict) and isinstance(metadata.get("research"), dict):
        return dict(metadata["research"])
    trace = getattr(solution, "trace", None)
    trace_metrics = getattr(trace, "research_metrics", {})
    return dict(trace_metrics) if isinstance(trace_metrics, dict) else {}


def _eval_usage(solution: Any) -> dict[str, int]:
    zeros = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "llm_calls": 0}
    metadata = getattr(solution, "metadata", {})
    if not isinstance(metadata, dict):
        return zeros
    raw = metadata.get("eval_usage")
    if not isinstance(raw, dict):
        return zeros
    return {
        "input_tokens": _coerce_nonnegative_int(raw.get("input_tokens")),
        "output_tokens": _coerce_nonnegative_int(raw.get("output_tokens")),
        "total_tokens": _coerce_nonnegative_int(raw.get("total_tokens")),
        "llm_calls": _coerce_nonnegative_int(raw.get("llm_calls")),
    }


def _tool_time_breakdown(solution: Any) -> dict[str, float]:
    """Per-tool cumulative wall seconds, as tracked live by ToolRegistry."""
    trace = getattr(solution, "trace", None)
    raw = getattr(trace, "tool_stats", None)
    if not isinstance(raw, dict):
        return {}
    result: dict[str, float] = {}
    for name, stats in raw.items():
        if not isinstance(stats, dict):
            continue
        try:
            result[str(name)] = max(0.0, float(stats.get("wall_seconds", 0.0)))
        except (TypeError, ValueError):
            continue
    return result


def _tool_distribution(turns: Iterable[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for turn in turns:
        name = str(getattr(getattr(turn, "action", None), "name", "") or "")
        if name in {"", "think", "set_goal", "conclude"}:
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts


def _merge_counts(left: dict[str, Any], right: Any) -> dict[str, int]:
    result = {str(name): _coerce_nonnegative_int(count) for name, count in left.items()}
    if isinstance(right, dict):
        for name, count in right.items():
            key = str(name)
            result[key] = result.get(key, 0) + _coerce_nonnegative_int(count)
    return result


def _coerce_nonnegative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _float_mapping(value: Any) -> dict[str, float]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, float] = {}
    for key, raw in value.items():
        try:
            result[str(key)] = max(0.0, float(raw))
        except (TypeError, ValueError):
            continue
    return result


def _prove_by_lemmas_stats(
    turns: Iterable[Any],
    tool_distribution: dict[str, int],
    verification_status: str,
) -> tuple[int, int]:
    """Count prove_by_lemmas attempts and successes for one trial.

    Exact counts come from the main agent's tool observations: a call counts
    as succeeded when its observation reported success, which for
    prove_by_lemmas means the assembled theorem verified in Lean. Calls made
    by legacy research workers are only visible in the merged tool
    distribution; those are approximated as successful iff the whole trial
    ended up verified (noted here because per-call worker outcomes are not
    recorded on the result row).
    """
    attempted = 0
    succeeded = 0
    for turn in turns:
        name = str(getattr(getattr(turn, "action", None), "name", "") or "")
        if name != "prove_by_lemmas":
            continue
        attempted += 1
        if bool(getattr(getattr(turn, "observation", None), "success", False)):
            succeeded += 1
    extra = _coerce_nonnegative_int(
        tool_distribution.get("prove_by_lemmas")
    ) - attempted
    if extra > 0:
        attempted += extra
        if verification_status == "verified":
            succeeded += extra
    return attempted, succeeded


def _lemma_success_rate(results: Iterable[TrialResult]) -> float:
    """Aggregate prove_by_lemmas conversion: verified calls / attempted calls."""
    items = list(results)
    attempted = sum(result.prove_by_lemmas_attempts for result in items)
    succeeded = sum(result.prove_by_lemmas_successes for result in items)
    return succeeded / attempted if attempted else 0.0


def _aggregate_tool_distribution(
    results: Iterable[TrialResult],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        counts = _merge_counts(counts, result.tool_call_distribution)
    return counts


def _average_breakdown(
    results: Iterable[TrialResult],
    attr: str,
) -> dict[str, float]:
    items = list(results)
    keys = {key for result in items for key in getattr(result, attr)}
    return {
        key: _mean(getattr(result, attr).get(key, 0.0) for result in items)
        for key in sorted(keys)
    }
