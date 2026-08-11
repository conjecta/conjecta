#!/usr/bin/env python3
"""Generate data/eval/fast.jsonl, data/eval/formal.jsonl and data/eval/formal_hard.jsonl.

Expected numeric values are computed here so ground-truth correctness is
independent of the agent under evaluation.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import sympy as sp

ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = ROOT / "data" / "eval"
EVAL_DIR.mkdir(parents=True, exist_ok=True)


def write_jsonl(path: Path, cases: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for case in cases:
            handle.write(json.dumps(case, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Fast tier: hand-authored cases across four domains and difficulty levels.
#
# Answer-format policy: expression-valued answers are probed NUMERICALLY (e.g.
# "evaluate the result at x=..." or "give the larger root as a decimal"). The
# `contains` judge collapses whitespace but keeps single spaces between tokens,
# so an expected like "x^2-x-6" fails whenever the model writes "x^2 - x - 6".
# Numeric probing extracts numbers regardless of formatting and is robust.
# ---------------------------------------------------------------------------
_x = sp.Symbol("x")
_xr = sp.Symbol("x", real=True)  # for Abs-equation solving, which needs a real symbol
fast_cases: list[dict] = []

# Algebra
fast_cases.extend(
    [
        {
            "id": "fast-alg-001",
            "problem": "Solve for x: 3x + 7 = 22. Answer with only the value of x.",
            "judge": "exact",
            "expected": "5",
            "tags": ["algebra", "easy"],
        },
        {
            "id": "fast-alg-002",
            "problem": "The equation x^2 - 5x + 6 = 0 has two roots. Answer with only the larger root.",
            "judge": "numeric",
            "expected": float(max(sp.solve(_x ** 2 - 5 * _x + 6, _x))),
            "tags": ["algebra", "easy"],
        },
        {
            "id": "fast-alg-003",
            "problem": "Expand (x + 2)(x - 3) and evaluate the resulting polynomial at x = 5. Answer with only the number.",
            "judge": "numeric",
            "expected": float(sp.expand((_x + 2) * (_x - 3)).subs(_x, 5)),
            "tags": ["algebra", "medium"],
        },
        {
            "id": "fast-alg-004",
            "problem": "Compute 2^10. Answer with only the number.",
            "judge": "exact",
            "expected": "1024",
            "tags": ["algebra", "easy"],
        },
        {
            "id": "fast-alg-005",
            "problem": "What is the sum of the first 20 positive integers?",
            "judge": "numeric",
            "expected": 20 * 21 // 2,
            "tags": ["algebra", "easy"],
        },
        {
            "id": "fast-alg-006",
            "problem": "Solve the system 2x + 3y = 13 and x - y = 1. Answer with only the value of x + y.",
            "judge": "numeric",
            "expected": float(
                (lambda s: s[_x] + s[sp.Symbol("y")])(
                    sp.solve(
                        [2 * _x + 3 * sp.Symbol("y") - 13, _x - sp.Symbol("y") - 1],
                        [_x, sp.Symbol("y")],
                        dict=True,
                    )[0]
                )
            ),
            "tags": ["algebra", "medium"],
        },
        {
            "id": "fast-alg-007",
            "problem": "x^2 - 9 factors as (x - a)(x + a) for a positive integer a. What is a?",
            "judge": "numeric",
            "expected": 3,
            "tags": ["algebra", "easy"],
        },
        {
            "id": "fast-alg-008",
            "problem": "Compute (1 + i)^2, where i^2 = -1. Answer in simplest form.",
            "judge": "exact",
            "expected": "2i",
            "tags": ["algebra", "medium"],
        },
        {
            "id": "fast-alg-009",
            "problem": "Consider x^2 - 4x + 1 = 0. Answer with the larger root as a decimal rounded to 3 decimal places.",
            "judge": "numeric",
            "expected": float(max(sp.solve(_x ** 2 - 4 * _x + 1, _x))),
            "tolerance": 1e-2,
            "tags": ["algebra", "medium"],
        },
        {
            "id": "fast-alg-010",
            "problem": "The expression (a^2 - b^2)/(a - b) simplifies to a linear expression in a and b. Evaluate that simplified expression at a = 7, b = 4. Answer with only the number.",
            "judge": "numeric",
            "expected": 7 + 4,
            "tags": ["algebra", "medium"],
        },
        # -- hard --
        {
            "id": "fast-alg-011",
            "problem": "Find the sum of all real solutions x to |x - 3| + |x + 2| = 7. Answer with only the number.",
            "judge": "numeric",
            "expected": float(
                sum(sp.solve(sp.Abs(_xr - 3) + sp.Abs(_xr + 2) - 7, _xr))
            ),
            "tags": ["algebra", "hard"],
        },
        {
            "id": "fast-alg-012",
            "problem": "What is the remainder when the polynomial x^100 is divided by x^2 - 1? It is a constant; answer with only that constant.",
            "judge": "numeric",
            "expected": int(sp.rem(_x ** 100, _x ** 2 - 1, _x)),
            "tags": ["algebra", "hard"],
        },
        {
            "id": "fast-alg-013",
            "problem": "What is the coefficient of x^2 in the expansion of (1 + x)^6? Answer with only the number.",
            "judge": "numeric",
            "expected": int(sp.expand((1 + _x) ** 6).coeff(_x, 2)),
            "tags": ["algebra", "hard"],
        },
    ]
)

# Number theory
fast_cases.extend(
    [
        {
            "id": "fast-nt-001",
            "problem": "What is gcd(48, 180)?",
            "judge": "numeric",
            "expected": int(sp.gcd(48, 180)),
            "tags": ["number_theory", "easy"],
        },
        {
            "id": "fast-nt-002",
            "problem": "What is lcm(12, 18)?",
            "judge": "numeric",
            "expected": int(sp.lcm(12, 18)),
            "tags": ["number_theory", "easy"],
        },
        {
            "id": "fast-nt-003",
            "problem": "Is 91 prime? Answer 'yes' or 'no' only.",
            "judge": "exact",
            "expected": "no",
            "tags": ["number_theory", "easy"],
        },
        {
            "id": "fast-nt-004",
            "problem": "Compute 1234 mod 97.",
            "judge": "numeric",
            "expected": 1234 % 97,
            "tags": ["number_theory", "easy"],
        },
        {
            "id": "fast-nt-005",
            "problem": "Compute Euler's totient φ(36).",
            "judge": "numeric",
            "expected": int(sp.totient(36)),
            "tags": ["number_theory", "medium"],
        },
        {
            "id": "fast-nt-006",
            "problem": "What is the sum of all prime numbers between 10 and 30?",
            "judge": "numeric",
            "expected": sum(p for p in range(11, 30) if sp.isprime(p)),
            "tags": ["number_theory", "medium"],
        },
        {
            "id": "fast-nt-007",
            "problem": "What is the digit sum of 2^15?",
            "judge": "numeric",
            "expected": sum(int(d) for d in str(2 ** 15)),
            "tags": ["number_theory", "medium"],
        },
        {
            "id": "fast-nt-008",
            "problem": "How many positive divisors does 72 have?",
            "judge": "numeric",
            "expected": int(sp.divisor_count(72)),
            "tags": ["number_theory", "easy"],
        },
        {
            "id": "fast-nt-009",
            "problem": "What is the remainder when 2^20 is divided by 100?",
            "judge": "numeric",
            "expected": (2 ** 20) % 100,
            "tags": ["number_theory", "medium"],
        },
        {
            "id": "fast-nt-010",
            "problem": "Find the smallest positive integer x such that 3x ≡ 7 (mod 11).",
            "judge": "numeric",
            "expected": next(x for x in range(1, 12) if (3 * x) % 11 == 7),
            "tags": ["number_theory", "medium"],
        },
        # -- hard --
        {
            "id": "fast-nt-011",
            "problem": "How many trailing zeros does 100! (100 factorial) have? Answer with only the number.",
            "judge": "numeric",
            "expected": 100 // 5 + 100 // 25,
            "tags": ["number_theory", "hard"],
        },
        {
            "id": "fast-nt-012",
            "problem": "Find the last two digits of 7^100. Answer with only the number (0 to 99).",
            "judge": "numeric",
            "expected": pow(7, 100, 100),
            "tags": ["number_theory", "hard"],
        },
        {
            "id": "fast-nt-013",
            "problem": "What is 3^2026 mod 5? Answer with only the number.",
            "judge": "numeric",
            "expected": pow(3, 2026, 5),
            "tags": ["number_theory", "hard"],
        },
        {
            "id": "fast-nt-014",
            "problem": "Find the smallest positive integer n such that n! is divisible by 1000. Answer with only n.",
            "judge": "numeric",
            "expected": next(
                n for n in range(1, 100) if sp.factorial(n) % 1000 == 0
            ),
            "tags": ["number_theory", "hard"],
        },
    ]
)

# Calculus
fast_cases.extend(
    [
        {
            "id": "fast-calc-001",
            "problem": "Compute the derivative of f(x)=x^3 at x=2.",
            "judge": "numeric",
            "expected": float(sp.diff(_x ** 3).subs(_x, 2)),
            "tags": ["calculus", "easy"],
        },
        {
            "id": "fast-calc-002",
            "problem": "Compute the definite integral of 2x from 0 to 1.",
            "judge": "numeric",
            "expected": float(sp.integrate(2 * _x, (_x, 0, 1))),
            "tags": ["calculus", "easy"],
        },
        {
            "id": "fast-calc-003",
            "problem": "What is the limit of (1 + 1/n)^n as n → ∞? Answer with the constant name.",
            "judge": "exact",
            "expected": "e",
            "tags": ["calculus", "medium"],
        },
        {
            "id": "fast-calc-004",
            "problem": "Compute the derivative of sin(x) at x=0.",
            "judge": "numeric",
            "expected": float(sp.diff(sp.sin(_x)).subs(_x, 0)),
            "tags": ["calculus", "easy"],
        },
        {
            "id": "fast-calc-005",
            "problem": "Find the maximum value of f(x) = -x^2 + 4x + 1.",
            "judge": "numeric",
            "expected": float((-_x ** 2 + 4 * _x + 1).subs(_x, 2)),
            "tags": ["calculus", "medium"],
        },
        {
            "id": "fast-calc-006",
            "problem": "Compute the definite integral of 1/x from 1 to e.",
            "judge": "numeric",
            "expected": float(sp.integrate(1 / _x, (_x, 1, sp.E))),
            "tags": ["calculus", "medium"],
        },
        {
            "id": "fast-calc-007",
            "problem": "Compute the derivative of e^x at x = ln(2).",
            "judge": "numeric",
            "expected": float(sp.diff(sp.exp(_x)).subs(_x, sp.log(2))),
            "tags": ["calculus", "medium"],
        },
        {
            "id": "fast-calc-008",
            "problem": "What is the limit of sin(x)/x as x → 0?",
            "judge": "numeric",
            "expected": 1.0,
            "tags": ["calculus", "easy"],
        },
        {
            "id": "fast-calc-009",
            "problem": "Compute the area under y = x^2 from x=0 to x=2.",
            "judge": "numeric",
            "expected": float(sp.integrate(_x ** 2, (_x, 0, 2))),
            "tags": ["calculus", "medium"],
        },
        {
            "id": "fast-calc-010",
            "problem": "Compute the gradient of f(x,y)=x^2+y^2 at the point (1,2), then answer with only the magnitude (Euclidean norm) of that gradient vector, as a decimal.",
            "judge": "numeric",
            "expected": float(sp.sqrt(2 ** 2 + 4 ** 2)),
            "tolerance": 1e-2,
            "tags": ["calculus", "medium"],
        },
        # -- hard --
        {
            "id": "fast-calc-011",
            "problem": "Find the global maximum value of f(x) = x^3 - 3x on the interval [-2, 2]. Answer with only the number.",
            "judge": "numeric",
            "expected": float(
                max(
                    (_x ** 3 - 3 * _x).subs(_x, p)
                    for p in (-2, -1, 1, 2)
                )
            ),
            "tags": ["calculus", "hard"],
        },
        {
            "id": "fast-calc-012",
            "problem": "Evaluate the definite integral of x*e^x from 0 to 1. Answer as a decimal.",
            "judge": "numeric",
            "expected": float(sp.integrate(_x * sp.exp(_x), (_x, 0, 1))),
            "tolerance": 1e-3,
            "tags": ["calculus", "hard"],
        },
        {
            "id": "fast-calc-013",
            "problem": "Compute the limit of (e^x - 1 - x) / x^2 as x → 0. Answer as a decimal.",
            "judge": "numeric",
            "expected": float(sp.limit((sp.exp(_x) - 1 - _x) / _x ** 2, _x, 0)),
            "tolerance": 1e-3,
            "tags": ["calculus", "hard"],
        },
    ]
)

# Geometry (text / coordinate / trig only)
fast_cases.extend(
    [
        {
            "id": "fast-geo-001",
            "problem": "Compute the area of a circle with radius 3. Give a numerical value.",
            "judge": "numeric",
            "expected": float(math.pi * 3 ** 2),
            "tolerance": 1e-3,
            "tags": ["geometry", "easy"],
        },
        {
            "id": "fast-geo-002",
            "problem": "What is the length of the hypotenuse of a right triangle with legs 3 and 4?",
            "judge": "numeric",
            "expected": 5.0,
            "tags": ["geometry", "easy"],
        },
        {
            "id": "fast-geo-003",
            "problem": "What is the area of a triangle with base 5 and height 8?",
            "judge": "numeric",
            "expected": 20.0,
            "tags": ["geometry", "easy"],
        },
        {
            "id": "fast-geo-004",
            "problem": "What is the distance between the points (1,2) and (4,6)?",
            "judge": "numeric",
            "expected": float(sp.sqrt((4 - 1) ** 2 + (6 - 2) ** 2)),
            "tags": ["geometry", "easy"],
        },
        {
            "id": "fast-geo-005",
            "problem": "What is the sum of the interior angles of a convex pentagon, in degrees?",
            "judge": "numeric",
            "expected": 540.0,
            "tags": ["geometry", "easy"],
        },
        {
            "id": "fast-geo-006",
            "problem": "Compute the circumference of a circle with radius 7. Give a numerical value.",
            "judge": "numeric",
            "expected": float(2 * math.pi * 7),
            "tolerance": 1e-3,
            "tags": ["geometry", "medium"],
        },
        {
            "id": "fast-geo-007",
            "problem": "Compute the volume of a sphere with radius 3. Give a numerical value.",
            "judge": "numeric",
            "expected": float((4 / 3) * math.pi * 3 ** 3),
            "tolerance": 1e-3,
            "tags": ["geometry", "medium"],
        },
        {
            "id": "fast-geo-008",
            "problem": "What is the slope of the line through (2,3) and (5,11)?",
            "judge": "numeric",
            "expected": float((11 - 3) / (5 - 2)),
            "tags": ["geometry", "easy"],
        },
        {
            "id": "fast-geo-009",
            "problem": "A rectangle has diagonal length 10 and one side length 6. What is its area?",
            "judge": "numeric",
            "expected": 48.0,
            "tags": ["geometry", "medium"],
        },
        {
            "id": "fast-geo-010",
            "problem": "In a triangle with sides 3, 4, and 5, what is the measure (in degrees) of the angle opposite the side of length 5?",
            "judge": "numeric",
            "expected": 90.0,
            "tags": ["geometry", "easy"],
        },
        # -- hard --
        {
            "id": "fast-geo-011",
            "problem": "Find the radius of the circle inscribed in a right triangle with legs 3 and 4. Answer with only the number.",
            "judge": "numeric",
            "expected": float((3 + 4 - 5) / 2),
            "tags": ["geometry", "hard"],
        },
        {
            "id": "fast-geo-012",
            "problem": "Using the shoelace formula, compute the area of the triangle with vertices (0,0), (4,1), and (1,5). Answer as a decimal.",
            "judge": "numeric",
            "expected": float(sp.Rational(abs(4 * 5 - 1 * 1), 2)),
            "tolerance": 1e-3,
            "tags": ["geometry", "hard"],
        },
        {
            "id": "fast-geo-013",
            "problem": "A regular hexagon has side length 2. Compute its area as a decimal.",
            "judge": "numeric",
            "expected": float(sp.Rational(3, 2) * sp.sqrt(3) * 2 ** 2),
            "tolerance": 1e-2,
            "tags": ["geometry", "hard"],
        },
        {
            "id": "fast-geo-014",
            "problem": "Find the acute angle, in degrees, between the lines y = x and y = 2x. Answer as a decimal rounded to 2 decimal places.",
            "judge": "numeric",
            "expected": float(sp.deg(sp.atan(sp.Rational(1, 3)))),
            "tolerance": 1e-1,
            "tags": ["geometry", "hard"],
        },
    ]
)

# ---------------------------------------------------------------------------
# Formal tier: 20 miniF2F-style informal statements, easy algebra/NT subset.
# ---------------------------------------------------------------------------
formal_cases: list[dict] = [
    {
        "id": "miniF2F-alg-001",
        "problem": "Formalize and prove in Lean 4 that for every natural number n, n + 0 = n.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "algebra"],
    },
    {
        "id": "miniF2F-alg-002",
        "problem": "Formalize and prove in Lean 4 that for every integer a, a * 1 = a.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "algebra"],
    },
    {
        "id": "miniF2F-alg-003",
        "problem": "Formalize and prove in Lean 4 that for every real number x, x^2 ≥ 0.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "algebra"],
    },
    {
        "id": "miniF2F-nt-001",
        "problem": "Formalize and prove in Lean 4 that if an integer n is even, then n^2 is even.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "number_theory"],
    },
    {
        "id": "miniF2F-nt-002",
        "problem": "Formalize and prove in Lean 4 that 2 is a prime number.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "number_theory"],
    },
    {
        "id": "miniF2F-nt-003",
        "problem": "Formalize and prove in Lean 4 that for all natural numbers n, n ≤ n^2.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "number_theory"],
    },
    {
        "id": "miniF2F-alg-004",
        "problem": "Formalize and prove in Lean 4 that for all real numbers a and b, (a + b)^2 = a^2 + 2ab + b^2.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "algebra"],
    },
    {
        "id": "miniF2F-set-001",
        "problem": "Formalize and prove in Lean 4 that for any sets A and B, A ∩ B ⊆ A.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "set_theory"],
    },
    {
        "id": "miniF2F-nt-004",
        "problem": "Formalize and prove in Lean 4 that the sum of the first n natural numbers equals n(n+1)/2 for every natural number n.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "number_theory"],
    },
    {
        "id": "miniF2F-set-002",
        "problem": "Formalize and prove in Lean 4 that the empty set is a subset of every set A.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "set_theory"],
    },
    {
        "id": "miniF2F-alg-005",
        "problem": "Formalize and prove in Lean 4 that for every integer a, -(-a) = a.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "algebra"],
    },
    {
        "id": "miniF2F-alg-006",
        "problem": "Formalize and prove in Lean 4 that real-number addition is commutative: for all real a and b, a + b = b + a.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "algebra"],
    },
    {
        "id": "miniF2F-nt-005",
        "problem": "Formalize and prove in Lean 4 that the sum of two even integers is even.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "number_theory"],
    },
    {
        "id": "miniF2F-nt-006",
        "problem": "Formalize and prove in Lean 4 that the square of an odd integer is odd.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "number_theory"],
    },
    {
        "id": "miniF2F-nt-007",
        "problem": "Formalize and prove in Lean 4 that every natural number divides itself.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "number_theory"],
    },
    {
        "id": "miniF2F-set-003",
        "problem": "Formalize and prove in Lean 4 that for any sets A and B, A is a subset of A union B.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "set_theory"],
    },
    {
        "id": "miniF2F-set-004",
        "problem": "Formalize and prove in Lean 4 that set intersection is commutative.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "set_theory"],
    },
    {
        "id": "miniF2F-fn-001",
        "problem": "Formalize and prove in Lean 4 that composing any function with the identity function on its domain leaves the function unchanged.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "functions"],
    },
    {
        "id": "miniF2F-list-001",
        "problem": "Formalize and prove in Lean 4 that the length of xs ++ ys equals length xs + length ys for all lists xs and ys.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "lists"],
    },
    {
        "id": "miniF2F-logic-001",
        "problem": "Formalize and prove in Lean 4 that for every Boolean value b, not (not b) = b.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "miniF2F", "logic"],
    },
]


# Hard formal tier: research-grade statements from docs/hard-problems-with-answers.md.
# These target the ceiling of the solve chain (retrieval -> formalization ->
# proof search); several are famous theorems that may be retrievable from
# mathlib, which is an intended part of what is being measured.
formal_hard_cases: list[dict] = [
    {
        "id": "hard-nt-001",
        "problem": "Formalize and prove in Lean 4 that there are infinitely many primes p with p ≡ 1 (mod 4).",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "hard", "number_theory"],
    },
    {
        "id": "hard-an-001",
        "problem": "Formalize and prove in Lean 4 that the series ∑_{n=1}^∞ 1/n^2 converges to π^2/6 (the Basel problem).",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "hard", "analysis"],
    },
    {
        "id": "hard-an-002",
        "problem": "Formalize and prove in Lean 4 the Bolzano–Weierstrass theorem: every bounded sequence of real numbers has a convergent subsequence.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "hard", "analysis"],
    },
    {
        "id": "hard-comb-001",
        "problem": "Formalize and prove in Lean 4 Cayley's formula: the number of trees on n distinct labeled vertices is n^(n-2).",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "hard", "combinatorics"],
    },
    {
        "id": "hard-alg-001",
        "problem": "Formalize and prove in Lean 4 the first isomorphism theorem for groups: for a group homomorphism φ : G → H, the quotient G/ker(φ) is isomorphic to the image of φ.",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "hard", "algebra"],
    },
    {
        "id": "hard-prob-001",
        "problem": "Formalize and prove in Lean 4 a weakened Talagrand concentration inequality: for a convex 1-Lipschitz function f on the discrete cube {-1,1}^n with X uniform, there exists a constant C such that Pr[|f(X) - E[f(X)]| ≥ t] ≤ 2·exp(-t^2/C).",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "hard", "probability"],
    },
    {
        "id": "hard-nt-002",
        "problem": "Formalize and prove in Lean 4 that the equation x^4 + y^4 = z^4 has no solution in positive integers (the n = 4 case of Fermat's Last Theorem).",
        "judge": "formal",
        "require_formal_verification": True,
        "tags": ["formal", "hard", "number_theory"],
    },
]


def _fast_answer_format_policy(cases: list[dict]) -> list[dict]:
    """Append a uniform answer-format instruction to numeric fast cases.

    The strict numeric judge measures math only when answers are terse;
    without an explicit instruction the model's verbosity (worked steps full
    of intermediate numbers) dominates the score. Cases that already carry a
    format instruction are left untouched.
    """
    normalized = []
    for case in cases:
        problem = case["problem"]
        lowered = problem.lower()
        if (
            case["judge"] == "numeric"
            and "answer with only" not in lowered
            and "give a numerical value" not in lowered
        ):
            case = {**case, "problem": problem + " Answer with only the number."}
        normalized.append(case)
    return normalized


def main() -> None:
    assert len(fast_cases) >= 40, len(fast_cases)
    assert len(formal_cases) >= 20, len(formal_cases)
    assert len(formal_hard_cases) >= 7, len(formal_hard_cases)
    write_jsonl(EVAL_DIR / "fast.jsonl", _fast_answer_format_policy(fast_cases))
    write_jsonl(EVAL_DIR / "formal.jsonl", formal_cases)
    write_jsonl(EVAL_DIR / "formal_hard.jsonl", formal_hard_cases)
    print(f"Wrote {len(fast_cases)} fast cases to {EVAL_DIR / 'fast.jsonl'}")
    print(f"Wrote {len(formal_cases)} formal cases to {EVAL_DIR / 'formal.jsonl'}")
    print(f"Wrote {len(formal_hard_cases)} hard formal cases to {EVAL_DIR / 'formal_hard.jsonl'}")


if __name__ == "__main__":
    main()
