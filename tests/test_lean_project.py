from __future__ import annotations

from math_agent.config import LeanConfig
from math_agent.lean.project import config_fingerprint, render_lakefile, render_package_lakefile


def test_render_lakefile_with_mathlib():
    config = LeanConfig(mathlib_dep=True, mathlib_rev="v4.12.0")
    lakefile = render_lakefile(config)
    assert 'require "leanprover-community" / "mathlib4"' in lakefile
    assert "v4.12.0" in lakefile
    assert "ProofCheck" in lakefile


def test_render_lakefile_without_mathlib():
    config = LeanConfig(mathlib_dep=False)
    lakefile = render_lakefile(config)
    assert "require mathlib" not in lakefile


def test_render_package_lakefile_uses_pinned_rev():
    config = LeanConfig(mathlib_rev="v4.12.0")
    lakefile = render_package_lakefile("My Proofs", config)
    assert '@ "v4.12.0"' in lakefile
    assert "MyProofs" in lakefile.replace(" ", "")


def test_config_fingerprint_changes_with_rev():
    a = config_fingerprint(LeanConfig(mathlib_rev="v4.12.0"))
    b = config_fingerprint(LeanConfig(mathlib_rev="v4.13.0"))
    assert a != b
