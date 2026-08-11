"""Schema validation for the fixed evaluation datasets.

This is the only benchmark-related test that runs in GitHub CI: it is
LLM-free, deterministic, and catches malformed rows or duplicate ids.
"""

from __future__ import annotations

from pathlib import Path

from math_agent.evaluation import load_cases


def _dataset_path(name: str) -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "eval" / name


def test_fast_eval_dataset_loads_with_unique_ids():
    cases = load_cases(_dataset_path("fast.jsonl"))
    assert len(cases) >= 40
    ids = [case.id for case in cases]
    assert len(ids) == len(set(ids))


def test_formal_eval_dataset_loads_with_unique_ids():
    cases = load_cases(_dataset_path("formal.jsonl"))
    assert len(cases) >= 20
    ids = [case.id for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case.judge == "formal"
        assert case.require_formal_verification


def test_formal_hard_eval_dataset_loads_with_unique_ids():
    cases = load_cases(_dataset_path("formal_hard.jsonl"))
    assert len(cases) >= 7
    ids = [case.id for case in cases]
    assert len(ids) == len(set(ids))
    for case in cases:
        assert case.judge == "formal"
        assert case.require_formal_verification


def test_research_eval_dataset_covers_all_research_tiers():
    cases = load_cases(_dataset_path("research.jsonl"))
    assert 20 <= len(cases) <= 30
    tags = {tag for case in cases for tag in case.tags}
    assert {"decompose", "tool_heavy", "formal"} <= tags
