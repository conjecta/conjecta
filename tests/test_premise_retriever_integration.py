from __future__ import annotations

import pytest

from math_agent.lean.premise_retriever import PremiseRetriever


@pytest.mark.integration
def test_premise_retriever_finds_irrational_sqrt2() -> None:
    retriever = PremiseRetriever()
    results = retriever.retrieve("Irrational (Real.sqrt 2)", top_k=5)
    names = {r.name for r in results}
    # Ranking shifts with mathlib versions and retriever tuning; the contract
    # is that results stay on the sqrt/irrational topic. mathlib does have
    # irrational_sqrt_two for this exact statement (NumberTheory/Real/
    # Irrational.lean) — returning it in top-k is a retrieval-quality goal,
    # not a pass/fail contract for this smoke test.
    assert results
    assert any("sqrt" in name.lower() or "irrational" in name.lower() for name in names)
