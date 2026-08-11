from __future__ import annotations

import pytest

from math_agent.lean.lemma_executor import sanitize_lemma_statement


@pytest.mark.parametrize(
    "statement,expected",
    [
        # Bare propositions pass through untouched.
        ("n + 0 = n", "n + 0 = n"),
        ("P n", "P n"),
        ("  spaced = out  ", "spaced = out"),
        ("", ""),
        # A repeated declaration header is what produced
        # "unexpected token 'lemma'; expected term" in the 2026-08-09 eval.
        ("lemma foo : P n", "P n"),
        ("theorem foo : P n", "P n"),
        ("example : 1 = 1", "1 = 1"),
        # Trailing proof assignments are not part of the proposition.
        ("theorem foo : P n := by", "P n"),
        ("lemma foo : P := by simp", "P"),
        ("lemma foo : P :=", "P"),
        # Binders contain their own colons, so the split must be depth-aware.
        ("lemma foo (n : Nat) : n + 0 = n", "n + 0 = n"),
        ("theorem t (h : a < b) : a <= b := by linarith", "a <= b"),
        (
            "lemma foo {a : Type*} [Ring a] (x : a) : x * 1 = x",
            "x * 1 = x",
        ),
        # Attributes and modifiers precede the keyword.
        ("@[simp] lemma foo : P", "P"),
        ("private theorem foo : P", "P"),
    ],
)
def test_sanitize_lemma_statement(statement: str, expected: str) -> None:
    assert sanitize_lemma_statement(statement) == expected


@pytest.mark.parametrize(
    "statement",
    [
        # `in` here is ordinary Lean syntax, not a stray declaration token.
        "∑ i in Finset.range n, i = n",
        "let k := 2 in k = 2",
        # A quantifier's colon is not a declaration's type ascription.
        "∀ n : Nat, n + 0 = n",
        # Nothing follows the header, so gutting the statement would be worse
        # than leaving it alone for Lean to report.
        "lemma broken",
    ],
)
def test_sanitize_leaves_non_declarations_intact(statement: str) -> None:
    assert sanitize_lemma_statement(statement) == statement.strip()


def test_sanitize_is_idempotent() -> None:
    once = sanitize_lemma_statement("lemma foo (n : Nat) : n + 0 = n := by simp")
    assert once == "n + 0 = n"
    assert sanitize_lemma_statement(once) == once
