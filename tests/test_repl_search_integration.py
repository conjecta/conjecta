"""Real-REPL integration test: full best-first search loop against Lean.

Runs the REPL-backed proof search with a scripted tactic oracle (no LLM) to
validate session handling, structured goals, failure recovery, completion
detection, and the final batch re-verification against the real toolchain.
"""
from __future__ import annotations

import pytest

from math_agent.lean.proof_search import ProofSearch, ProofState
from math_agent.lean.repl_session import LeanReplPool

IMPORTS = "import Mathlib.Tactic.NormNum\nimport Mathlib.Algebra.Order.Ring.Nat"
THEOREM = "theorem repl_search_smoke (a b : Nat) : a + b = b + a := by"


class ScriptedOracle:
    """Deterministic tactic generator: first a failing tactic, then omega."""

    def __init__(self):
        self.calls = 0

    async def generate(self, state: ProofState) -> list[str]:
        self.calls += 1
        if self.calls == 1:
            return ["rfl", "omega"]  # rfl fails (not syntactic), omega closes
        return ["omega"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_repl_best_first_search_closes_real_goal():
    from math_agent.config import load_config
    from math_agent.lean.runner import LeanRunner

    config = load_config().lean
    if not LeanReplPool.available(config):
        pytest.skip("REPL binary not built")
    runner = LeanRunner(config)
    pool = LeanReplPool(config)
    oracle = ScriptedOracle()
    search = ProofSearch(
        generator=oracle,
        runner=runner,
        max_attempts=8,
        max_depth=4,
        repl_pool=pool,
        imports=IMPORTS,
    )
    try:
        result = await search.search(THEOREM)
    finally:
        await pool.aclose()
    assert result.success, f"search failed: {result.error}"
    assert result.attempts >= 2  # rfl failure + omega success
    assert "omega" in result.proof
    assert "sorry" not in result.proof
    # The returned proof carries the precise imports and passed the batch
    # checker inside the search (success is only returned after check_proof).
    assert "import Mathlib.Tactic.NormNum" in result.proof
