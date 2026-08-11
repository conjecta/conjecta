from __future__ import annotations

import math
import re
from collections.abc import Iterable
from fractions import Fraction
from typing import Any

from math_agent.evaluation.models import EvalCase


_NUMBER_RE = re.compile(r"(?<![\w.])[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
# Remove only comma thousands separators (e.g. 1,000), not list separators (e.g. 1,2).
_THOUSANDS_SEP_RE = re.compile(r"(?<=\d),(?=\d{3}(?:,\d{3})*(?!\d))")

# Simple exact fraction forms: \frac{a}{b} (also \dfrac/\tfrac) and plain a/b
# with integer parts. The plain form is not read out of decimals (1.5/2),
# exponents (x^2/3), or chained slashes (1/2/3).
_LATEX_FRAC_RE = re.compile(r"\\[dt]?frac\s*\{\s*(-?\d+)\s*\}\s*\{\s*(-?\d+)\s*\}")
_PLAIN_FRAC_RE = re.compile(r"(?<![\w.*/^])(-?\d+)\s*/\s*(-?\d+)(?![\w*/])(?!\.\d)")

# LaTeX dressing stripped by the exact judge: inline/display math delimiters,
# \left/\right, \dfrac/\tfrac, and content wrappers like \boxed/\mathrm/\text.
_MATH_DELIMITER_RE = re.compile(r"\$\$?|\\\(|\\\)|\\\[|\\\]")
_LATEX_WRAPPER_RE = re.compile(
    r"\\(?:boxed|mathrm|mathbf|mathit|mathsf|mathcal|operatorname|text)\s*\{"
)
_TRAILING_PUNCT_RE = re.compile(r"[\s.,;:!?]+$")

# Whole-string simple exact forms the exact judge can compare as rationals.
_SIMPLE_INT_RE = re.compile(r"-?\d+")
_SIMPLE_DECIMAL_RE = re.compile(r"-?(?:\d+\.\d*|\.\d+)")
_SIMPLE_FRAC_RE = re.compile(r"(-?\d+)\s*/\s*(-?\d+)")
_SIMPLE_LATEX_FRAC_RE = re.compile(r"\\frac\s*\{\s*(-?\d+)\s*\}\s*\{\s*(-?\d+)\s*\}")

# A "clean exact token" is short math (e, 2i, \frac{1}{2}), never a prose word.
_EXACT_TOKEN_RE = re.compile(r"[0-9a-z\\+\-*/^_{}()=.]+")

# Terminal control sequences occasionally leak into model output from
# OpenAI-compatible gateways. Strip them before any judging so they cannot
# break exact/numeric matching.
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

# Markdown bold spans (**answer**) — models without \boxed training often mark
# the final answer this way.
_BOLD_SPAN_RE = re.compile(r"\*\*([^*]+)\*\*")

# Terminal failure markers: the agent's own "not solved" declarations. A
# contains-case whose answer carries one is never correct, no matter which
# expected keywords also appear (the research-dec-001 false positive: the
# final answer said "未能闭合证明：All reviewed routes failed..." yet matched
# the expected keywords). Sources (the research orchestrator is removed;
# these markers stay so historical research runs still judge correctly):
#   research _BEST_EFFORT_PREFIX      -> "未能闭合证明"
#   research blocked reason           -> "All reviewed routes failed"
#   research _OPEN_STATUS_GOAL_RE     -> "无法证明" / "cannot be proved"
#   HITL budget-extension question    -> "预算已用尽"
#   ReActSolution.verification_status -> "best_effort" self-declarations
_FAILURE_MARKERS = (
    "未能闭合证明",
    "all reviewed routes failed",
    "无法证明",
    "cannot be proved",
    "cannot be proven",
    "unable to prove",
    "预算已用尽",
    "预算已经用完",
    "预算用尽",
    "best_effort",
    "best-effort",
    "best effort",
)


def judge_solution(case: EvalCase, solution: Any) -> bool:
    answer = _ANSI_ESCAPE_RE.sub("", str(getattr(solution, "final_answer", "") or ""))
    if case.judge == "exact":
        return _judge_exact(answer, case.expected)
    if case.judge == "numeric":
        return _judge_numeric(
            answer, case.expected, case.tolerance, case.numeric_match_mode
        )
    if case.judge == "contains":
        if _has_failure_marker(answer):
            return False
        expected = (
            case.expected
            if isinstance(case.expected, list)
            else [case.expected]
        )
        return _contains_all(answer, (str(item) for item in expected))
    if case.judge == "formal":
        passed = (
            str(getattr(solution, "verification_status", "")) == "verified"
            and bool(getattr(solution, "lean_proofs", []) or [])
        )
        if not passed or case.expected is None or case.expected == "":
            return passed
        expected = case.expected if isinstance(case.expected, list) else [case.expected]
        code = "\n".join(str(item) for item in (getattr(solution, "lean_proofs", []) or []))
        return _contains_all(code, (str(item) for item in expected))
    if case.judge == "formal_reject":
        return str(getattr(solution, "verification_status", "")) != "verified"
    raise ValueError(f"Unsupported judge: {case.judge}")


def _judge_exact(answer: str, expected: Any) -> bool:
    expected_norm = _normalize_math(str(expected))
    if not expected_norm:
        return False
    # \boxed{...} is the declared final answer: a derivation chain ending in
    # \boxed{2i} judges as "2i", not as the whole surrounding text.
    for content in _boxed_contents(answer):
        content_norm = _normalize_math(content)
        if content_norm and (
            content_norm == expected_norm
            or _sympy_equal(content_norm, expected_norm)
        ):
            return True
    answer_norm = _normalize_math(answer)
    if answer_norm == expected_norm:
        return True
    # Whole-string simple exact forms compare as exact rationals: \frac{1}{2}
    # == 0.5, 27/5 == 5.4. Anything more complex stays textual.
    answer_fraction = _parse_simple_rational(answer_norm)
    expected_fraction = _parse_simple_rational(expected_norm)
    if answer_fraction is not None and expected_fraction is not None:
        return answer_fraction == expected_fraction
    # Symbolic equivalence rescues competition forms the textual and rational
    # paths cannot see: \frac{\sqrt{3}}{2} == sqrt(3)/2, 2\pi == 2 pi, ...
    if _sympy_equal(answer_norm, expected_norm):
        return True
    # Leading prose words ("Euler's number, e") pass only when the expected
    # value is a clean exact token that appears as the trailing token of the
    # answer. Conservative by construction: prose expectations never qualify.
    tail = _trailing_token(answer_norm)
    return (
        _is_exact_token(expected_norm)
        and tail is not None
        and tail != answer_norm
        and (tail == expected_norm or _sympy_equal(tail, expected_norm))
    )


def _judge_numeric(
    answer: str, expected: Any, tolerance: float, mode: str = "all"
) -> bool:
    target_fraction = _as_exact_fraction(expected)
    if target_fraction is not None:
        target = float(target_fraction)
    else:
        try:
            target = float(expected)
        except (TypeError, ValueError):
            # Symbolic expected value (e.g. \frac{\sqrt{3}}{2}, 2\pi): reduce
            # it to a float via sympy, else the case cannot be judged.
            target = _sympy_to_float(str(expected))
            if target is None:
                return False
    candidates = _numeric_candidates(answer)
    boxed_symbolic: list[float] = []
    for content in _boxed_contents(answer):
        # A boxed value that is not a simple rational is symbolic content
        # (\frac{\sqrt{3}}{2}); plain extraction only sees the stray digits
        # inside it, so the sympy evaluation is the trustworthy candidate.
        if _parse_simple_rational(_normalize_math(content)) is None:
            value = _sympy_to_float(content)
            if value is not None:
                boxed_symbolic.append(value)
    if boxed_symbolic:
        candidates = [(None, value) for value in boxed_symbolic]
    if not candidates:
        return False

    def matches(candidate: tuple[Fraction | None, float]) -> bool:
        fraction, value = candidate
        if fraction is not None and target_fraction is not None:
            if fraction == target_fraction:
                return True
            # A decimal expected value is a rounded rendering of the exact
            # fraction when they differ by at most half a unit of the last
            # decimal place (8/3 vs 2.6667). Integer expectations and plain
            # number candidates stay on the strict tolerance path below.
            places = _decimal_places(target_fraction)
            if places is not None and abs(fraction - target_fraction) <= Fraction(
                1, 2 * 10**places
            ):
                return True
        return math.isclose(value, target, rel_tol=tolerance, abs_tol=tolerance)

    if mode == "any":
        return any(matches(candidate) for candidate in candidates)
    if mode == "last":
        return matches(candidates[-1])
    # mode == "all": every number in the answer must match the expected value.
    return all(matches(candidate) for candidate in candidates)


def _last_equation_rhs(text: str) -> str | None:
    """Right-hand side after the last '=' when it holds a number or fraction.

    An equation chain (\\frac{20\\cdot 21}{2} = 210, f'(2) = 12) declares its
    result on the right-hand side; the left sides carry intermediate numbers
    that must not become candidates under the strict "all" mode.
    """
    index = text.rfind("=")
    while index != -1:
        tail = text[index + 1 :]
        if (
            _NUMBER_RE.search(tail)
            or _LATEX_FRAC_RE.search(tail)
            or _PLAIN_FRAC_RE.search(tail)
        ):
            return tail
        index = text.rfind("=", 0, index)
    return None


def _numeric_candidates(text: str) -> list[tuple[Fraction | None, float]]:
    """(exact rational, float) candidates in reading order.

    \boxed{...} content is the declared final answer and wins over the
    surrounding prose. Simple fractions (a/b, \\frac{a}{b}) carry their exact
    rational value so 27/5 can equal 5.4; the spans they cover are masked
    before plain number extraction so numerator and denominator are not
    double-counted as standalone candidates.
    """
    boxed = _boxed_contents(text)
    source = "\n".join(boxed) if boxed else text
    if not boxed:
        # No \boxed: markdown bold in the final answer (… = **10**) is the
        # same "declared final answer" signal models use in prose answers.
        bold = [
            span
            for span in _BOLD_SPAN_RE.findall(text)
            if any(char.isdigit() for char in span)
        ]
        if bold:
            source = "\n".join(bold)
    rhs = _last_equation_rhs(source)
    if rhs is not None:
        source = rhs
    # "9\pi \approx 28.2743" declares its numeric value after the
    # approximation marker; the exact-form coefficient (9) is not a
    # standalone candidate.
    approx_index = max(source.rfind("\\approx"), source.rfind("≈"))
    if approx_index != -1:
        tail = source[approx_index:]
        if _NUMBER_RE.search(tail):
            source = tail
    cleaned = _THOUSANDS_SEP_RE.sub("", source)
    found: list[tuple[int, Fraction | None, float]] = []
    masked = list(cleaned)
    for regex in (_LATEX_FRAC_RE, _PLAIN_FRAC_RE):
        for match in regex.finditer("".join(masked)):
            numerator, denominator = int(match.group(1)), int(match.group(2))
            if denominator == 0:
                continue
            fraction = Fraction(numerator, denominator)
            found.append((match.start(), fraction, float(fraction)))
            for index in range(match.start(), match.end()):
                masked[index] = " "
    for match in _NUMBER_RE.finditer("".join(masked)):
        try:
            found.append((match.start(), None, float(match.group(0))))
        except ValueError:
            continue
    found.sort(key=lambda item: item[0])
    return [(fraction, value) for _, fraction, value in found]


def _boxed_contents(text: str) -> list[str]:
    contents = []
    for match in re.finditer(r"\\boxed\s*\{", text):
        depth = 1
        index = match.end()
        while index < len(text) and depth:
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
            index += 1
        if depth == 0:
            contents.append(text[match.end() : index - 1])
    return contents


def _as_exact_fraction(expected: Any) -> Fraction | None:
    """Exact rational form of a simple expected value, else None."""
    try:
        if isinstance(expected, bool):
            return None
        if isinstance(expected, int):
            return Fraction(expected)
        if isinstance(expected, float):
            return Fraction(str(expected))
        return _parse_simple_rational(str(expected))
    except (TypeError, ValueError, ArithmeticError):
        return None


def _decimal_places(value: Fraction) -> int | None:
    """Decimal digit count when the denominator is a pure power of ten > 1."""
    denominator = value.denominator
    places = 0
    while denominator > 1 and denominator % 10 == 0:
        denominator //= 10
        places += 1
    if denominator != 1 or places == 0:
        return None
    return places


def _parse_simple_rational(text: str) -> Fraction | None:
    """Fraction for a whole-string simple exact form, else None."""
    text = text.strip()
    if _SIMPLE_INT_RE.fullmatch(text):
        return Fraction(int(text))
    if _SIMPLE_DECIMAL_RE.fullmatch(text):
        return Fraction(text)
    for regex in (_SIMPLE_FRAC_RE, _SIMPLE_LATEX_FRAC_RE):
        match = regex.fullmatch(text)
        if match and int(match.group(2)) != 0:
            return Fraction(int(match.group(1)), int(match.group(2)))
    return None


def _normalize_math(value: str) -> str:
    """Normalize LaTeX dressing so wrapped answers compare by content."""
    text = _MATH_DELIMITER_RE.sub(" ", _normalize(value))
    text = text.replace("\\left", "").replace("\\right", "")
    text = re.sub(r"\\[dt]frac", r"\\frac", text)
    previous = None
    while previous != text:
        previous = text
        text = _unwrap_latex_group(text)
    text = _TRAILING_PUNCT_RE.sub("", text)
    return " ".join(text.split())


def _unwrap_latex_group(text: str) -> str:
    """Replace the first \\boxed{/\\mathrm{/\\text{...} group with its content."""
    match = _LATEX_WRAPPER_RE.search(text)
    if not match:
        return text
    depth = 1
    index = match.end()
    while index < len(text) and depth:
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
        index += 1
    if depth:
        return text
    return text[: match.start()] + text[match.end() : index - 1] + text[index:]


def _trailing_token(text: str) -> str | None:
    tokens = re.split(r"[\s,]+", text.strip())
    return tokens[-1] if tokens and tokens[-1] else None


def _is_exact_token(text: str) -> bool:
    """True for short math tokens (e, 2i, \\frac{1}{2}), not prose words."""
    if not text or len(text) > 20 or not _EXACT_TOKEN_RE.fullmatch(text):
        return False
    return len(text) == 1 or any(
        char in text for char in "0123456789\\^_{}+-*/=."
    )


def _has_failure_marker(answer: str) -> bool:
    normalized = _normalize(answer)
    return any(marker in normalized for marker in _FAILURE_MARKERS)


# ---------------------------------------------------------------------------
# Symbolic equivalence fallback (sympy).
#
# The exact/numeric judges above only understand plain rationals. Competition
# answers like \frac{\sqrt{3}}{2}, 2\pi, or e^{i\pi} fell through to textual
# mismatch, which forced whole Omni-MATH slices out of the benchmark. The
# helpers below conservatively convert a normalized math string into a sympy
# expression and compare symbolically. Every step bails out to None on any
# ambiguity so this path can only rescue true math, never prose.
# ---------------------------------------------------------------------------

# LaTeX commands we know how to render as sympy calls. Anything outside this
# whitelist makes the whole symbolic path give up on the string.
_SYMPY_KNOWN_WORDS = frozenset(
    {
        "sqrt", "pi", "sin", "cos", "tan", "sec", "csc", "cot", "arcsin",
        "arccos", "arctan", "sinh", "cosh", "tanh", "ln", "log", "exp",
        "gcd", "lcm", "binom", "floor", "ceil", "abs", "min", "max",
        "factorial", "oo", "inf", "infinity", "e", "i",
    }
)

_LATEX_SQRT_RE = re.compile(r"\\sqrt\s*(?:\[\s*([^\]]+)\s*\])?\s*\{")
_LATEX_CMD_RE = re.compile(r"\\([a-zA-Z]+)")
_ASCII_WORD_RE = re.compile(r"[a-zA-Z]+")


def _extract_braced(text: str, start: int) -> tuple[str, int] | None:
    """Return (content, end_index) of the {...} group whose '{' is at start."""
    if start >= len(text) or text[start] != "{":
        return None
    depth = 1
    index = start + 1
    while index < len(text) and depth:
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
        index += 1
    if depth:
        return None
    return text[start + 1 : index - 1], index


def _latex_to_sympy(text: str) -> str | None:
    """Convert a normalized math string to a sympy-parseable string, or None.

    Conservative: unknown LaTeX commands, unbalanced braces, or leftover
    non-math words all yield None so the caller falls back to older judges.
    """
    if not text or len(text) > 200:
        return None
    out = text
    # \sqrt[k]{x} / \sqrt{x} -> (x)**(1/k) / sqrt(x), innermost braces first.
    while True:
        match = _LATEX_SQRT_RE.search(out)
        if not match:
            break
        group = _extract_braced(out, match.end() - 1)
        if group is None:
            return None
        content, end = group
        if match.group(1):
            replacement = f"(({content})**(1/({match.group(1)})))"
        else:
            replacement = f"sqrt({content})"
        out = out[: match.start()] + replacement + out[end:]
    # \frac{a}{b} (and \dfrac/\tfrac, already collapsed by _normalize_math).
    while True:
        match = re.search(r"\\frac\s*\{", out)
        if not match:
            break
        num = _extract_braced(out, match.end() - 1)
        if num is None:
            return None
        num_content, num_end = num
        den = _extract_braced(out, num_end)
        if den is None:
            # allow \frac12 style: two single-char arguments
            tail = out[num_end : num_end + 1]
            if not tail.strip():
                return None
            num_content = out[match.end() - 1 : num_end].strip("{}")
            den_content, den_end = tail, num_end + 1
        else:
            den_content, den_end = den
        out = (
            out[: match.start()]
            + f"(({num_content})/({den_content}))"
            + out[den_end:]
        )
    # Remaining backslash commands must be known math words; render each one.
    rendered = []
    cursor = 0
    for match in _LATEX_CMD_RE.finditer(out):
        rendered.append(out[cursor : match.start()])
        name = match.group(1)
        if name in {"left", "right", "displaystyle", "limits"}:
            rendered.append(" ")
        elif name in {"cdot", "times", "ast"}:
            rendered.append("*")
        elif name == "div":
            rendered.append("/")
        elif name == "infty":
            rendered.append(" oo ")
        elif name in _SYMPY_KNOWN_WORDS:
            rendered.append(f" {name} ")
        else:
            return None
        cursor = match.end()
    rendered.append(out[cursor:])
    out = "".join(rendered)
    # Stray backslash escapes (\! \, \;) carry no meaning for us.
    out = out.replace("\\", " ")
    # Bare words must be known math names or single-letter symbols; anything
    # longer is prose and disqualifies the string.
    for match in _ASCII_WORD_RE.finditer(out):
        word = match.group(0)
        if len(word) > 1 and word not in _SYMPY_KNOWN_WORDS:
            return None
    # Subscripts (x_1, a_{n}) are unsupported: bail out rather than guess.
    if "_" in out:
        return None
    # x^{n} -> x**(n); remaining braces become parens, which keeps the paren
    # count balanced by construction.
    out = out.replace("^{", "**(")
    out = out.replace("{", "(").replace("}", ")")
    out = out.replace("^", "**")
    out = re.sub(r"\s+", " ", out).strip()
    if not out or out.count("(") != out.count(")"):
        return None
    return out


def _sympy_parse(text: str):
    """Parse text into a sympy expression, or None when not clean math."""
    converted = _latex_to_sympy(text)
    if converted is None:
        return None
    try:
        import sympy
        from sympy.parsing.sympy_parser import (
            convert_xor,
            factorial_notation,
            implicit_multiplication_application,
            parse_expr,
            standard_transformations,
        )

        return parse_expr(
            converted,
            # Competition answers write Euler's number and the imaginary unit
            # as plain e / i; bind them to the sympy constants.
            local_dict={"e": sympy.E, "i": sympy.I},
            transformations=standard_transformations
            + (convert_xor, factorial_notation, implicit_multiplication_application),
            evaluate=True,
        )
    except Exception:
        return None


def _sympy_equal(a: str, b: str) -> bool:
    """True when both strings parse as math and are symbolically equal."""
    expr_a = _sympy_parse(a)
    expr_b = _sympy_parse(b)
    if expr_a is None or expr_b is None:
        return False
    try:
        import sympy

        diff = sympy.simplify(expr_a - expr_b)
        if diff == 0:
            return True
        # Symbolic simplification is incomplete; fall back to numeric check
        # when both sides evaluate to finite numbers.
        val_a = complex(expr_a.evalf(30))
        val_b = complex(expr_b.evalf(30))
        return math.isclose(val_a.real, val_b.real, rel_tol=1e-9, abs_tol=1e-12) and math.isclose(
            val_a.imag, val_b.imag, rel_tol=1e-9, abs_tol=1e-12
        )
    except Exception:
        return False


def _sympy_to_float(text: str) -> float | None:
    """Real numeric value of a math string via sympy, or None."""
    expr = _sympy_parse(text)
    if expr is None:
        return None
    try:
        value = complex(expr.evalf(30))
    except Exception:
        return None
    if abs(value.imag) > 1e-12 or not math.isfinite(value.real):
        return None
    return value.real


def _contains_all(answer: str, expected: Iterable[str]) -> bool:
    normalized = _normalize(answer)
    required = [_normalize(item) for item in expected if _normalize(item)]
    return bool(required) and all(item in normalized for item in required)


def _normalize(value: str) -> str:
    return " ".join((value or "").strip().casefold().split())
