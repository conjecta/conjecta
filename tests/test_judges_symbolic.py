"""Symbolic-equivalence judging via the sympy fallback in evaluation/judges.py."""

from types import SimpleNamespace

from math_agent.evaluation import EvalCase
from math_agent.evaluation.judges import judge_solution


def _solution(answer, *, status="reviewed", lean_proofs=None):
    return SimpleNamespace(
        final_answer=answer,
        verification_status=status,
        lean_proofs=list(lean_proofs or []),
    )


def test_exact_judge_accepts_symbolic_equivalents():
    cases = [
        (r"\frac{\sqrt{3}}{2}", "sqrt(3)/2"),
        (r"\frac{\sqrt{3}}{2}", r"\frac{1}{2}\sqrt{3}"),
        ("2\\pi", "\\pi + \\pi"),
        (r"e^{i\pi}", "-1"),
        (r"\sqrt{2}", r"2^{1/2}"),
        (r"\frac{1+\sqrt{5}}{2}", r"\frac{1+\sqrt{5}}{2}"),
    ]
    for expected, answer in cases:
        assert judge_solution(
            EvalCase(id="s", problem="p", judge="exact", expected=expected),
            _solution(answer),
        ), f"{expected!r} should equal {answer!r}"


def test_exact_judge_rejects_symbolic_mismatches():
    cases = [
        (r"\frac{\sqrt{3}}{2}", "sqrt(2)/2"),
        ("2\\pi", "3\\pi"),
        (r"\sqrt{2}", "1.414"),
    ]
    for expected, answer in cases:
        assert not judge_solution(
            EvalCase(id="s", problem="p", judge="exact", expected=expected),
            _solution(answer),
        ), f"{expected!r} should NOT equal {answer!r}"


def test_exact_judge_never_treats_prose_as_math():
    assert not judge_solution(
        EvalCase(id="s", problem="p", judge="exact", expected="4"),
        _solution("the answer is clearly four"),
    )
    # Identical prose still passes through the textual equality path; the
    # sympy path must simply stay out of the way without raising.
    assert judge_solution(
        EvalCase(
            id="s", problem="p", judge="exact", expected="some prose expectation"
        ),
        _solution("some prose expectation"),
    )


def test_numeric_judge_evaluates_symbolic_expected():
    assert judge_solution(
        EvalCase(
            id="n",
            problem="p",
            judge="numeric",
            expected=r"\frac{\sqrt{3}}{2}",
            tolerance=1e-6,
        ),
        _solution("The answer is 0.8660254."),
    )


def test_numeric_judge_reads_symbolic_boxed_answer():
    assert judge_solution(
        EvalCase(id="n", problem="p", judge="numeric", expected=0.8660254, tolerance=1e-6),
        _solution(r"Thus \(x = \boxed{\frac{\sqrt{3}}{2}}\)."),
    )
    assert not judge_solution(
        EvalCase(id="n", problem="p", judge="numeric", expected=0.5, tolerance=1e-6),
        _solution(r"Thus \(x = \boxed{\frac{\sqrt{3}}{2}}\)."),
    )


def test_numeric_judge_keeps_plain_fraction_exactness():
    # Regression: simple boxed rationals must still use the exact-fraction
    # path (8/3 vs a rounded 2.6667 expected decimal).
    assert judge_solution(
        EvalCase(id="n", problem="p", judge="numeric", expected="2.6667", tolerance=1e-6),
        _solution(r"\(\boxed{\frac{8}{3}}\)"),
    )


def test_numeric_judge_treats_markdown_bold_as_declared_answer():
    # Prose answer without \boxed: the bold final value is the declared answer
    # and intermediate numbers must not fail the "all" match mode.
    assert judge_solution(
        EvalCase(id="n", problem="p", judge="numeric", expected=10, tolerance=1e-9),
        _solution("120% of 30 = 36, and 130% of 20 = 26. So 36 - 26 = **10**."),
    )
    assert not judge_solution(
        EvalCase(id="n", problem="p", judge="numeric", expected=11, tolerance=1e-9),
        _solution("120% of 30 = 36, and 130% of 20 = 26. So 36 - 26 = **10**."),
    )


def test_exact_judge_reads_boxed_answer_in_derivation_chain():
    # Regression (fast-alg-008): a derivation chain ending in \boxed{2i}
    # must judge as "2i", not fail on the surrounding LaTeX.
    assert judge_solution(
        EvalCase(id="s", problem="p", judge="exact", expected="2i"),
        _solution(
            "\\[\n(1+i)^2=1+2i+i^2=1+2i-1=\\boxed{2i}.\n\\]"
        ),
    )
    assert not judge_solution(
        EvalCase(id="s", problem="p", judge="exact", expected="3i"),
        _solution(
            "\\[\n(1+i)^2=1+2i+i^2=1+2i-1=\\boxed{2i}.\n\\]"
        ),
    )


def test_numeric_judge_prefers_value_after_approx_marker():
    # Regression (fast-geo-001/006/007): "9\pi \approx 28.2743" declares the
    # numeric value after \approx; the exact-form coefficient (9) and the
    # radius (3) must not fail the "all" match mode.
    area_answer = (
        "The area is\n\\[\nA=\\pi r^2=\\pi(3)^2=9\\pi\\approx 28.2743.\n\\]\n"
        "So the numerical area is approximately $28.2743$ square units."
    )
    assert judge_solution(
        EvalCase(
            id="n", problem="p", judge="numeric",
            expected=28.274333882308138, tolerance=0.001,
        ),
        _solution(area_answer),
    )
    assert not judge_solution(
        EvalCase(
            id="n", problem="p", judge="numeric",
            expected=99.0, tolerance=0.001,
        ),
        _solution(area_answer),
    )
    circumference_answer = (
        "The circumference is\n\\[\nC=2\\pi r=2\\pi(7)=14\\pi\\approx 43.9823.\n\\]\n"
        "So the numerical value is approximately $43.9823$ units."
    )
    assert judge_solution(
        EvalCase(
            id="n", problem="p", judge="numeric",
            expected=43.982297150257104, tolerance=0.001,
        ),
        _solution(circumference_answer),
    )
