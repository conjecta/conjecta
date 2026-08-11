from types import SimpleNamespace

import pytest

from math_agent.agent.react_state import Action, ReActTurn, ToolObservation
from math_agent.evaluation import EvalCase, load_cases, run_evaluation
from math_agent.evaluation.judges import judge_solution


def _solution(answer, *, status="reviewed", lean_proofs=None, turns=None):
    return SimpleNamespace(
        final_answer=answer,
        verification_status=status,
        lean_proofs=list(lean_proofs or []),
        turns=list(turns or []),
    )


def test_judges_cover_exact_numeric_contains_and_formal():
    assert judge_solution(
        EvalCase(id="e", problem="p", judge="exact", expected="x = 4"),
        _solution("  X   = 4 "),
    )
    assert judge_solution(
        EvalCase(id="n", problem="p", judge="numeric", expected=10.0),
        _solution("The answer is 10."),
    )
    assert judge_solution(
        EvalCase(id="c", problem="p", judge="contains", expected=["prime", "infinite"]),
        _solution("There are infinitely many PRIME values; hence the set is infinite."),
    )
    assert judge_solution(
        EvalCase(id="f", problem="p", judge="formal"),
        _solution(
            "done", status="verified", lean_proofs=["theorem p : True := by trivial"]
        ),
    )
    assert judge_solution(
        EvalCase(
            id="fb", problem="p", judge="formal", expected=["theorem p", ": True"]
        ),
        _solution(
            "done", status="verified", lean_proofs=["theorem p : True := by trivial"]
        ),
    )
    assert not judge_solution(
        EvalCase(id="fm", problem="p", judge="formal", expected="False"),
        _solution(
            "done", status="verified", lean_proofs=["theorem p : True := by trivial"]
        ),
    )
    assert judge_solution(
        EvalCase(id="fr", problem="p", judge="formal_reject"),
        _solution("blocked", status="blocked"),
    )


def test_numeric_judge_reads_equation_rhs():
    """Regression: 'LHS = value' answers judge on the value, not the LHS numbers."""
    cases = [
        ("\\[\n\\operatorname{lcm}(12,18)=36.\n\\]", 36),
        ("The sum is\n\\[\n\\frac{20\\cdot 21}{2}=210.\n\\]", 210),
        ("Hence, $\\boxed{f'(2)=12}$.", 12.0),
        ("\\[\n\\boxed{\\lim_{x\\to 0}\\frac{\\sin x}{x}=1}\n\\]", 1.0),
        ("Their sum is\n\\[\n11+13+17+19+23=83.\n\\]", 83),
        ("sum is\n\\[\n(5-2)\\cdot 180^\\circ=540^\\circ.\n\\]", 540),
    ]
    for answer, expected in cases:
        assert judge_solution(
            EvalCase(id="t", problem="p", judge="numeric", expected=expected),
            _solution(answer),
        ), answer
    # A wrong right-hand side still fails.
    assert not judge_solution(
        EvalCase(id="t", problem="p", judge="numeric", expected=6),
        _solution("$\\boxed{x=5}$"),
    )
    # Chained equalities judge on the final value.
    assert judge_solution(
        EvalCase(id="t", problem="p", judge="numeric", expected=0.5),
        _solution("\\[\n\\lim_{x\\to 0}\\frac{e^x-1-x}{x^2}=\\frac12=0.5.\n\\]"),
    )


def test_judge_strips_ansi_escape_codes():
    """Regression: mirrors sometimes wrap answers in ANSI bold (\\x1b[1m...)."""
    assert judge_solution(
        EvalCase(id="n", problem="p", judge="numeric", expected=12),
        _solution("\x1b[1m$\\boxed{12}$\x1b[0m"),
    )
    assert judge_solution(
        EvalCase(id="e", problem="p", judge="exact", expected="5"),
        _solution("\x1b[1m5\x1b[0m"),
    )


def test_numeric_judge_rejects_partial_matches():
    """Regression: '(1,2)' must not match expected=2 or expected=12 by accident."""
    case_two = EvalCase(id="n2", problem="p", judge="numeric", expected=2)
    case_twelve = EvalCase(id="n12", problem="p", judge="numeric", expected=12)
    answer = _solution("The roots are (1,2).")

    assert not judge_solution(case_two, answer)
    assert not judge_solution(case_twelve, answer)

    # Last-number mode should accept the trailing value.
    assert judge_solution(
        EvalCase(
            id="n2_last",
            problem="p",
            judge="numeric",
            expected=2,
            numeric_match_mode="last",
        ),
        answer,
    )

    # Any-number mode preserves the old (buggy) permissive behaviour.
    assert judge_solution(
        EvalCase(
            id="n2_any",
            problem="p",
            judge="numeric",
            expected=2,
            numeric_match_mode="any",
        ),
        answer,
    )


def test_numeric_judge_accepts_exact_fraction_forms():
    # fast-alg-006 regression: 27/5 == 5.4 as exact rationals.
    assert judge_solution(
        EvalCase(id="n", problem="p", judge="numeric", expected=5.4),
        _solution("27/5"),
    )
    # A rounded decimal expectation matches within half a last-place unit.
    assert judge_solution(
        EvalCase(id="n", problem="p", judge="numeric", expected=2.6667),
        _solution("8/3"),
    )
    assert judge_solution(
        EvalCase(id="n", problem="p", judge="numeric", expected=0.5),
        _solution("\\frac{1}{2}"),
    )
    # A genuinely different value must still fail.
    assert not judge_solution(
        EvalCase(id="n", problem="p", judge="numeric", expected=5.5),
        _solution("27/5"),
    )


def test_numeric_judge_prefers_boxed_final_answer():
    # fast-calc-009 regression: the derivation mentions many numbers; the
    # boxed fraction is the declared final answer.
    answer = (
        "The area is\n\\[\n\\int_0^2 x^2\\,dx=\\left[\\frac{x^3}{3}\\right]_0^2"
        "=\\frac{8}{3}.\n\\]\nSo the area is $\\boxed{\\frac{8}{3}}$ square units."
    )
    assert judge_solution(
        EvalCase(id="n", problem="p", judge="numeric", expected=2.6666666666666665),
        _solution(answer),
    )
    # fast-geo-008 regression: a lone inline fraction in prose also matches.
    assert judge_solution(
        EvalCase(id="n", problem="p", judge="numeric", expected=2.6666666666666665),
        _solution("The slope is $\\frac{8}{3}$."),
    )


def test_exact_judge_normalizes_latex_and_leading_prose():
    # fast-alg-008 regression.
    case = EvalCase(id="e", problem="p", judge="exact", expected="2i")
    assert judge_solution(case, _solution("$2i$"))
    assert judge_solution(case, _solution("\\(2i\\)"))
    assert judge_solution(case, _solution("$\\boxed{2i}$"))
    assert not judge_solution(case, _solution("$-2i$"))

    # fast-calc-003 regression: leading prose words are stripped only when a
    # clean exact token follows.
    euler = EvalCase(id="e", problem="p", judge="exact", expected="e")
    assert judge_solution(euler, _solution("Euler's number, $e$"))
    assert judge_solution(euler, _solution("Euler's number $e$"))
    assert judge_solution(euler, _solution("\\mathrm{e}"))
    assert not judge_solution(euler, _solution("where kate"))
    # Prose expectations never qualify for the trailing-token shortcut.
    wordy = EvalCase(id="e", problem="p", judge="exact", expected="proof")
    assert not judge_solution(wordy, _solution("here is the proof"))


def test_exact_judge_compares_simple_rational_forms():
    assert judge_solution(
        EvalCase(id="e", problem="p", judge="exact", expected="0.5"),
        _solution("\\frac{1}{2}"),
    )
    assert judge_solution(
        EvalCase(id="e", problem="p", judge="exact", expected="5.4"),
        _solution("27/5"),
    )
    assert not judge_solution(
        EvalCase(id="e", problem="p", judge="exact", expected="0.5"),
        _solution("\\frac{1}{3}"),
    )


def test_contains_judge_failure_markers_override_keyword_matches():
    # research-dec-001 regression: the answer matched the expected keywords
    # yet was a terminal failure declaration.
    case = EvalCase(
        id="research-dec-001",
        problem="p",
        judge="contains",
        expected=["n^2", "induction"],
    )
    assert not judge_solution(
        case,
        _solution(
            "本轮研究预算内未能闭合证明：All reviewed routes failed for lemma base. "
            "Partial notes mention n^2 and induction."
        ),
    )
    assert not judge_solution(
        case,
        _solution("这里无法证明；n^2 by induction was attempted."),
    )
    assert not judge_solution(
        case,
        _solution("This cannot be proved with n^2 induction in budget."),
    )
    assert not judge_solution(
        case,
        _solution("best_effort answer: n^2 by induction."),
    )
    # Positive cases without failure markers still pass.
    assert judge_solution(
        case,
        _solution("Proof by induction: the sum of the first n odd numbers is n^2."),
    )


def test_load_cases_rejects_duplicate_ids(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        '{"id":"same","problem":"p","judge":"exact","expected":"1"}\n'
        '{"id":"same","problem":"q","judge":"exact","expected":"2"}\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate"):
        load_cases(path)


def test_eval_case_accepts_list_expected():
    """Regression: list-type expected values must not raise a TypeError."""
    case = EvalCase.from_dict(
        {"id": "c", "problem": "p", "judge": "contains", "expected": ["a", "b"]}
    )
    assert case.expected == ["a", "b"]


def test_eval_case_numeric_match_mode_validation():
    case = EvalCase.from_dict(
        {
            "id": "n",
            "problem": "p",
            "judge": "numeric",
            "expected": 1,
            "numeric_match_mode": "last",
        }
    )
    assert case.numeric_match_mode == "last"

    with pytest.raises(ValueError, match="numeric_match_mode"):
        EvalCase.from_dict(
            {
                "id": "n",
                "problem": "p",
                "judge": "numeric",
                "expected": 1,
                "numeric_match_mode": "first",
            }
        )


@pytest.mark.asyncio
async def test_runner_reports_pass_at_k_and_false_verified():
    cases = [
        EvalCase(id="a", problem="p", judge="exact", expected="4", tags=("smoke",)),
        EvalCase(id="b", problem="q", judge="exact", expected="5", tags=("smoke",)),
    ]
    calls = {"a": 0, "b": 0}

    async def solve(case):
        calls[case.id] += 1
        if case.id == "a":
            answer = "wrong" if calls[case.id] == 1 else "4"
            return _solution(answer)
        return _solution(
            "wrong",
            status="verified",
            lean_proofs=["theorem unrelated : True := by trivial"],
            turns=[
                ReActTurn(
                    thought="check",
                    action=Action(name="lean_check", args={"code": "..."}),
                    observation=ToolObservation(success=True, output="passed"),
                )
            ],
        )

    results, summary = await run_evaluation(cases, solve, trials=2)

    assert len(results) == 4
    assert summary.accuracy == 0.25
    assert summary.pass_at_k == 0.5
    assert summary.verified_count == 2
    assert summary.false_verified_count == 2
    assert summary.false_verified_rate == 1.0
    assert summary.by_tag["smoke"]["trial_count"] == 4.0


@pytest.mark.asyncio
async def test_runner_aggregates_research_graph_and_tool_metrics():
    case = EvalCase(
        id="research",
        problem="p",
        judge="exact",
        expected="done",
        tags=("decompose",),
    )
    turn = ReActTurn(
        thought="calculate",
        action=Action(name="compute", args={"code": "print(1)"}),
        observation=ToolObservation(success=True, output="1"),
    )

    async def solve(_case):
        return SimpleNamespace(
            final_answer="done",
            verification_status="reviewed",
            lean_proofs=[],
            turns=[turn],
            metadata={
                "research": {
                    "planned_goal_count": 3,
                    "proved_goal_count": 2,
                    "research_goal_rounds": 4,
                    "counterexample_count": 1,
                    "replan_count": 1,
                    "peak_parallel_goals": 2,
                    "worker_steps": 5,
                    "worker_tool_calls": 3,
                    "tool_call_distribution": {"compute": 2, "search": 1},
                    "wall_time_breakdown": {
                        "goal_batches_seconds": 1.5,
                        "total_seconds": 2.0,
                    },
                }
            },
        )

    results, summary = await run_evaluation([case], solve)

    result = results[0]
    assert result.step_count == 6
    assert result.tool_call_count == 4
    assert result.tool_call_distribution == {"compute": 3, "search": 1}
    assert result.planned_goal_count == 3
    assert result.proved_goal_count == 2
    # lemma_success_rate now measures prove_by_lemmas conversion; this trial
    # made no prove_by_lemmas calls, so the rate is 0.
    assert summary.lemma_success_rate == 0.0
    assert summary.counterexample_trigger_rate == 1.0
    assert summary.average_peak_parallel_goals == 2.0
    assert summary.by_tag["decompose"]["average_steps"] == 6.0
    assert summary.by_tag["decompose"]["wall_time_breakdown"]["total_seconds"] == 2.0


@pytest.mark.asyncio
async def test_runner_lemma_success_rate_tracks_prove_by_lemmas():
    """lemma_success_rate aggregates prove_by_lemmas tool outcomes."""
    case = EvalCase(id="f", problem="p", judge="formal", tags=("formal",))
    turns = [
        ReActTurn(
            thought="try decomposition",
            action=Action(name="prove_by_lemmas", args={"statement": "True"}),
            observation=ToolObservation(success=False, output="could not be verified"),
        ),
        ReActTurn(
            thought="retry with simpler lemmas",
            action=Action(name="prove_by_lemmas", args={"statement": "True"}),
            observation=ToolObservation(success=True, output="PASSED"),
        ),
    ]

    async def solve(_case):
        return _solution(
            "done",
            status="verified",
            lean_proofs=["theorem p : True := by trivial"],
            turns=turns,
        )

    results, summary = await run_evaluation([case], solve)

    result = results[0]
    assert result.prove_by_lemmas_attempts == 2
    assert result.prove_by_lemmas_successes == 1
    assert summary.lemma_success_rate == pytest.approx(0.5)
    assert summary.by_tag["formal"]["lemma_success_rate"] == pytest.approx(0.5)
    assert result.to_dict()["lemma_success_rate"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_runner_lemma_success_rate_zero_without_prove_by_lemmas():
    case = EvalCase(id="n", problem="p", judge="numeric", expected=1)

    async def solve(_case):
        return _solution("1")

    _, summary = await run_evaluation([case], solve)
    assert summary.lemma_success_rate == 0.0
