from __future__ import annotations

import pytest

from math_agent.web.solve_mode import resolve_solve_mode


def test_resolve_solve_mode_defaults_to_auto():
    assert resolve_solve_mode({}) == "auto"


def test_resolve_solve_mode_ignores_legacy_geeky_flag():
    # geeky mode was removed; the legacy flag no longer selects a mode.
    assert resolve_solve_mode({"geeky": True}) == "auto"
    assert resolve_solve_mode({"max_effort": True}) == "auto"


def test_resolve_solve_mode_accepts_explicit_modes():
    assert resolve_solve_mode({"mode": "react"}) == "react"
    assert resolve_solve_mode({"mode": "auto"}) == "auto"


def test_resolve_solve_mode_rejects_invalid_mode():
    with pytest.raises(ValueError, match="Invalid solve mode"):
        resolve_solve_mode({"mode": "fast"})

    # Removed engines are no longer valid modes. There is one solve path now:
    # formal escalation is driven by the problem's verification requirement,
    # not by a user-selected mode.
    for gone in ("cot", "staged", "geeky", "research"):
        with pytest.raises(ValueError, match="Invalid solve mode"):
            resolve_solve_mode({"mode": gone})
