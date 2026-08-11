"""Tests for the miniF2F dataset converter (math_agent.evaluation.minif2f)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from math_agent.evaluation import load_cases
from math_agent.evaluation import minif2f

REPO_ROOT = Path(__file__).resolve().parents[2]
VALID_PATH = REPO_ROOT / "data/eval/minif2f_valid.jsonl"
TEST_PATH = REPO_ROOT / "data/eval/minif2f_test.jsonl"

_SAMPLE_LEAN = """import Mathlib

set_option maxHeartbeats 0

open BigOperators Real Nat Topology Rat

theorem mathd_algebra_109 (a b : ℝ) (h₀ : 3 * a + 2 * b = 12) (h₁ : a = 4) : b = 0 := by sorry
"""

_SAMPLE_LEAN_MULTILINE = """import Mathlib

set_option maxHeartbeats 0

open BigOperators Real Nat Topology Rat

theorem aime_1999_p11
  (m : ℚ)
  (h₀ : 0 < m)
  (h₁ : ∑ k ∈ Finset.Icc (1 : ℕ) 35, Real.sin (5 * k * π / 180) = Real.tan (m * π / 180))
  (h₂ : (m.num:ℝ) / m.den < 90) :
  ↑m.den + m.num = 177 := by sorry
"""


def _fake_mathlib(tmp_path: Path, modules: list[str] | tuple[str, ...]) -> Path:
    """Create only the Mathlib source files needed by one unit test."""
    root = tmp_path / "mathlib4"
    for module in modules:
        path = root / Path(*module.split(".")).with_suffix(".lean")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("-- unit-test fixture\n", encoding="utf-8")
    return root


def test_extract_statement_single_line():
    name, signature = minif2f.extract_statement(_SAMPLE_LEAN)
    assert name == "mathd_algebra_109"
    assert signature.startswith("theorem mathd_algebra_109 (a b : ℝ)")
    assert signature.endswith(": b = 0")
    assert "sorry" not in signature


def test_extract_statement_multiline():
    name, signature = minif2f.extract_statement(_SAMPLE_LEAN_MULTILINE)
    assert name == "aime_1999_p11"
    assert "Real.sin" in signature
    assert ":= by" not in signature


def test_extract_statement_rejects_garbage():
    with pytest.raises(ValueError, match="sorry"):
        minif2f.extract_statement("theorem foo : True := by trivial")


def test_existing_imports_filters_missing_modules(tmp_path):
    mathlib_dir = _fake_mathlib(tmp_path, ["Mathlib.Data.Real.Basic"])
    kept = minif2f.existing_imports(
        ["Mathlib.Data.Real.Basic", "Mathlib.No.Such.Module", "Batteries.Whatever"],
        package_dir=mathlib_dir,
    )
    assert kept == ["Mathlib.Data.Real.Basic"]


def test_standard_imports_exist_and_are_not_umbrella(tmp_path):
    mathlib_dir = _fake_mathlib(tmp_path, minif2f.STANDARD_IMPORTS)
    standard = minif2f.existing_imports(
        list(minif2f.STANDARD_IMPORTS), package_dir=mathlib_dir
    )
    assert standard == list(minif2f.STANDARD_IMPORTS)
    assert "Mathlib" not in standard  # umbrella import is forbidden
    assert "Mathlib.Tactic.Common" in standard


def test_render_problem_embeds_statement_with_precise_imports():
    imports = list(minif2f.STANDARD_IMPORTS)
    problem = minif2f.render_problem(
        "Formalize and prove in Lean 4: ... Show that it is 0.",
        "mathd_algebra_109",
        "theorem mathd_algebra_109 (a b : ℝ) : b = 0",
        imports,
    )
    assert "import Mathlib.Tactic.Common" in problem
    assert not re.search(r"^import Mathlib$", problem, re.MULTILINE)
    assert "theorem mathd_algebra_109 (a b : ℝ) : b = 0 := by" in problem
    assert "sorry" in problem
    assert "mathlib4 v4.30.0" in problem


def test_render_problem_without_nl_text():
    problem = minif2f.render_problem(None, "imo_2006_p3", "theorem imo_2006_p3 : True", [])
    assert "imo_2006_p3" in problem


def test_build_rows_schema():
    statements = [
        minif2f.Statement("mathd_algebra_109", "valid", "theorem mathd_algebra_109 : True"),
        minif2f.Statement("imo_2006_p3", "valid", "theorem imo_2006_p3 : True"),
    ]
    imports = {s.name: ["Mathlib.Tactic.Common"] for s in statements}
    rows = minif2f.build_rows(statements, imports, {"mathd_algebra_109": "NL text"})
    assert [row["id"] for row in rows] == ["minif2f-valid-001", "minif2f-valid-002"]
    for row, stmt in zip(rows, statements):
        assert row["judge"] == "formal"
        assert row["require_formal_verification"] is True
        # The verified proof must contain the target theorem name.
        assert row["expected"] == stmt.name
        assert row["split"] == "valid"
        assert row["minif2f_name"] == stmt.name
        assert "formal" in row["tags"] and "minif2f" in row["tags"]
    assert rows[0]["tags"][-1] == "algebra"
    assert rows[1]["tags"][-1] == "competition"


def test_verify_statements_batching_and_fallback(monkeypatch, tmp_path):
    """Elaboration outcomes drive import assignment without running Lean."""
    statements = [
        minif2f.Statement("good_one", "valid", "theorem good_one : True"),
        minif2f.Statement("bad_one", "valid", "theorem bad_one : True"),
        minif2f.Statement("good_two", "valid", "theorem good_two : True"),
    ]

    def fake_run_lean(path, library_path):
        text = Path(path).read_text()
        # bad_one only elaborates once the fallback import is present.
        if "bad_one" in text and "Mathlib.Data.NNReal.Basic" not in text:
            return "error: unknown constant"
        return ""

    monkeypatch.setattr(minif2f, "_run_lean", fake_run_lean)
    mathlib_dir = _fake_mathlib(
        tmp_path, minif2f.STANDARD_IMPORTS + minif2f.FALLBACK_IMPORTS
    )
    imports_by_name, errors = minif2f.verify_statements(
        statements,
        jobs=1,
        batch_size=25,
        library_path="unused",
        mathlib_dir=mathlib_dir,
    )
    standard = minif2f.existing_imports(
        list(minif2f.STANDARD_IMPORTS), package_dir=mathlib_dir
    )
    assert errors == {}
    assert imports_by_name["good_one"] == standard
    assert imports_by_name["good_two"] == standard
    assert imports_by_name["bad_one"] == standard + minif2f.existing_imports(
        list(minif2f.FALLBACK_IMPORTS), package_dir=mathlib_dir
    )


def test_verify_statements_reports_unrecoverable_errors(monkeypatch):
    statements = [minif2f.Statement("doomed", "test", "theorem doomed : True")]
    monkeypatch.setattr(minif2f, "_run_lean", lambda path, lp: "error: nope")
    imports_by_name, errors = minif2f.verify_statements(
        statements, jobs=1, batch_size=10, library_path="unused"
    )
    assert imports_by_name == {}
    assert "doomed" in errors


def test_source_tree_is_complete():
    if not minif2f.SOURCE_DIR.is_dir():
        pytest.skip("miniF2F source tree not present")
    statements = minif2f.load_statements()
    assert len(statements) == 488
    assert sum(s.split == "valid" for s in statements) == 244
    assert sum(s.split == "test" for s in statements) == 244
    names = [s.name for s in statements]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("path", [VALID_PATH, TEST_PATH])
def test_generated_dataset_schema(path):
    if not path.exists():
        pytest.skip(f"{path} not generated yet")
    cases = load_cases(path)  # validates schema + id uniqueness
    assert cases, "dataset must not be empty"
    for case in cases:
        assert case.judge == "formal"
        assert case.require_formal_verification is True
        assert "minif2f" in case.tags
        assert "theorem " in case.problem
        assert "import Mathlib.Tactic.Common" in case.problem
        assert not re.search(r"^import Mathlib$", case.problem, re.MULTILINE)
        # The formal judge checks the target theorem name appears in proofs.
        assert case.expected and str(case.expected) in case.problem
    split = "valid" if "valid" in path.name else "test"
    assert all(case.id.startswith(f"minif2f-{split}-") for case in cases)


def test_generated_datasets_do_not_overlap():
    if not (VALID_PATH.exists() and TEST_PATH.exists()):
        pytest.skip("datasets not generated yet")
    valid_names = {
        json.loads(line)["minif2f_name"]
        for line in VALID_PATH.read_text().splitlines()
        if line.strip()
    }
    test_names = {
        json.loads(line)["minif2f_name"]
        for line in TEST_PATH.read_text().splitlines()
        if line.strip()
    }
    assert valid_names.isdisjoint(test_names)
