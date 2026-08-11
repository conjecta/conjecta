"""Schema validation for the built benchmark suite under data/benchmarks/.

The benchmark JSONL files are build artifacts produced by
``scripts/build_benchmark_suite.py`` and may be absent on a fresh checkout;
files that do not exist are skipped rather than failing the suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from math_agent.evaluation import load_cases

BENCHMARKS_DIR = Path(__file__).resolve().parent.parent / "data" / "benchmarks"

EXPECTED_FILES = [
    "competition/aime_1983_2024.jsonl",
    "competition/aime_2025.jsonl",
    "competition/hmmt_feb_2025.jsonl",
    "olympiad/omni_math.jsonl",
    "olympiad/olympiadbench_text.jsonl",
    "formal/minif2f_valid.jsonl",
    "formal/minif2f_test.jsonl",
    "formal/putnam.jsonl",
    "formal/compfiles_imo.jsonl",
    "formal/combibench.jsonl",
]


def _existing_benchmark_files() -> list[Path]:
    return sorted(
        path
        for path in BENCHMARKS_DIR.rglob("*.jsonl")
        # _src: raw build inputs; sampled: subsets of the canonical files, so
        # their IDs intentionally duplicate the parent suite.
        if "_src" not in path.parts and "sampled" not in path.parts
    )


@pytest.mark.parametrize("relpath", EXPECTED_FILES)
def test_benchmark_file_loads(relpath: str):
    path = BENCHMARKS_DIR / relpath
    if not path.exists():
        pytest.skip(f"benchmark artifact not built: {relpath}")
    cases = load_cases(path)
    assert len(cases) > 0
    ids = [case.id for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case.tags, f"{case.id}: expected at least one tag"
        tier_tags = [tag for tag in case.tags if tag.startswith("tier")]
        assert tier_tags, f"{case.id}: missing tierN tag"
        if case.judge not in {"formal", "formal_reject"}:
            assert case.expected is not None and case.expected != "", (
                f"{case.id}: non-formal case needs a non-empty expected"
            )
        else:
            assert case.require_formal_verification, (
                f"{case.id}: formal case must require formal verification"
            )


def test_benchmark_suite_ids_unique_across_files():
    files = _existing_benchmark_files()
    if not files:
        pytest.skip("no benchmark artifacts built yet")
    seen: dict[str, str] = {}
    for path in files:
        for case in load_cases(path):
            assert case.id not in seen, (
                f"duplicate id {case.id!r} in {path.name} and {seen[case.id]}"
            )
            seen[case.id] = path.name
