from __future__ import annotations

import pytest

from math_agent.lean.premise_retriever import PremiseRetriever


@pytest.mark.integration
def test_premise_retriever_finds_irrational_sqrt2() -> None:
    retriever = PremiseRetriever()
    results = retriever.retrieve("Irrational (Real.sqrt 2)", top_k=5)
    names = {r.name for r in results}
    assert "Irrational" in names or "Real.sqrt" in names or "irrational_sqrt_two" in names
