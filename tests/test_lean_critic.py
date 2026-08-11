from __future__ import annotations

from math_agent.lean.critic import LeanCritic


class FakeMathlibSearch:
    existing_names = {
        "Irrational",
        "Mathlib",
        "Mathlib.Data.Nat.Basic",
        "Mathlib.Data.Rat.Defs",
        "Mathlib.Data.Real.Irrational",
        "Nat",
        "Real",
        "Real.sqrt",
        "Real.sqrt_pos",
        "Real.sqrt_pos.mpr",
        "sqrt_pos",
    }

    def search_by_name(self, name: str, *, max_results: int = 10):
        if name in self.existing_names or name.split(".")[-1] in self.existing_names:
            return [{"name": name}]
        return []

    def _rg(self, pattern: str, max_results: int = 50):
        import re

        for name in self.existing_names:
            if re.search(pattern, name):
                return [{"file": "Fake.lean", "line": 1, "text": name}]
        return []


def critic() -> LeanCritic:
    return LeanCritic(mathlib_search=FakeMathlibSearch())


def test_critic_flags_invented_projection():
    code = """
import Mathlib.Data.Rat.Defs

theorem ex (r : ℚ) : r.num > 0 := by
  exact r.cop rfl
"""
    result = critic().critique(code)
    names = {i.name for i in result.issues}
    assert "r.cop" in names


def test_critic_flags_unknown_qualified_constant():
    code = """
import Mathlib.Data.Nat.Basic

theorem ex (n : ℕ) : n = n := by
  exact Nat.thisTheoremDoesNotExist.rfl
"""
    result = critic().critique(code)
    names = {i.name for i in result.issues}
    assert "Nat.thisTheoremDoesNotExist.rfl" in names


def test_critic_allows_legitimate_rat_fields():
    code = """
import Mathlib.Data.Rat.Defs

theorem ex (r : ℚ) : r.num = r.num := by
  rfl
"""
    result = critic().critique(code)
    assert not result.has_issues


def test_critic_allows_existing_mathlib_names():
    code = """
import Mathlib.Data.Real.Irrational

theorem ex : Irrational (Real.sqrt 2) := by
  have h : Real.sqrt 2 > 0 := Real.sqrt_pos.mpr (by norm_num)
  exact h
"""
    result = critic().critique(code)
    assert not result.has_issues


def test_critic_prompt_block_formatting():
    code = """
import Mathlib.Data.Nat.Basic

theorem ex : 1 = 1 := by
  exact Nat.does_not_exist
"""
    result = critic().critique(code)
    block = result.to_prompt_block()
    assert "Critic pre-check" in block
    assert "exact?" in block


def test_critic_flags_umbrella_mathlib_import():
    code = """
import Mathlib

theorem ex : True := by
  trivial
"""
    result = critic().critique(code)
    names = {i.name for i in result.issues}
    assert "import Mathlib" in names
