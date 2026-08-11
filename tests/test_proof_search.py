from __future__ import annotations

import pytest

from math_agent.lean.proof_search import (
    ProofSearch,
    ProofSearchResult,
    ProofState,
    TacticGenerator,
    _extract_unsolved_goal,
    _format_state_for_prompt,
)
from math_agent.billing.models import LLMResponse
from math_agent.lean.result import LeanResult
from math_agent.llm.base import Message


@pytest.mark.asyncio
async def test_tactic_generator_prepends_premises():
    from math_agent.lean.premise_retriever import PremiseEntry, PremiseRetriever

    llm = FakeLLM("1. rfl")
    retriever = PremiseRetriever(entries=[
        PremiseEntry(name="Nat.Prime", module="Mathlib.Data.Nat.Prime", type="Nat -> Prop"),
    ])
    gen = TacticGenerator(llm, premise_retriever=retriever)
    state = ProofState(theorem_statement="theorem ex (n : ℕ) : Nat.Prime n := by")
    tactics = await gen.generate(state)
    assert tactics == ["rfl"]

    assert len(llm.calls) == 1
    messages, system, _temperature = llm.calls[0]
    assert len(messages) == 1
    prompt = messages[0].content
    assert "Relevant mathlib4 declarations" in prompt
    assert "Nat.Prime" in prompt
    assert "Mathlib.Data.Nat.Prime" in prompt


def test_extract_goal_from_classic_lean_output():
    output = """
error: unsolved goals
case h
n : ℕ
⊢ n + 0 = n
"""
    assert _extract_unsolved_goal(output) == "n + 0 = n"


def test_extract_goal_joins_indented_continuation_lines():
    output = """
error: unsolved goals
case h
n : ℕ
⊢ ∀ (k : ℕ),
    k + 0 = k
"""
    assert _extract_unsolved_goal(output) == "∀ (k : ℕ), k + 0 = k"


def test_extract_goal_stops_at_non_indented_continuation():
    output = """
error: unsolved goals
case h
n : ℕ
⊢ first goal line
second context line
⊢ other goal
"""
    assert _extract_unsolved_goal(output) == "first goal line"


def test_premise_retriever_repairs_imports():
    from math_agent.lean.premise_retriever import PremiseEntry, PremiseRetriever

    retriever = PremiseRetriever(entries=[
        PremiseEntry(name="Nat.Prime", module="Mathlib.Data.Nat.Prime", type="Nat -> Prop"),
    ])
    code = "theorem ex (n : ℕ) : Nat.Prime n := by sorry"
    repaired = retriever.repair_imports_for_errors(
        code, ["Unknown constant 'Nat.Prime'"]
    )
    assert "import Mathlib.Data.Nat.Prime" in repaired


def test_proof_state_tracks_partial_proof():
    state = ProofState(
        theorem_statement="theorem ex (n : ℕ) : n + 0 = n := by",
        partial_proof="  induction n with\n  | zero => rfl\n  | succ n ih =>",
        imports="",
        depth=2,
    )
    assert state.depth == 2
    assert "n + 0 = n" in state.goal
    assert state.full_code == (
        "theorem ex (n : ℕ) : n + 0 = n := by\n"
        "  induction n with\n  | zero => rfl\n  | succ n ih =>"
    )


def test_format_state_for_prompt():
    state = ProofState(
        theorem_statement="theorem ex (n : ℕ) : n + 0 = n := by",
        partial_proof="  induction n with",
        parent_tactic="induction n",
    )
    prompt = _format_state_for_prompt(state)
    assert "Theorem:" in prompt
    assert "```lean" in prompt
    assert "Previous tactic: induction n" in prompt
    assert "Current goal:" in prompt


class FakeLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[tuple[list[Message], str | None, float | None]] = []

    async def complete(self, messages, system=None, temperature=None):
        self.calls.append((messages, system, temperature))
        return LLMResponse(
            text=self.response,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
        )


@pytest.mark.asyncio
async def test_tactic_generator_parses_numbered_list():
    llm = FakeLLM("1. rfl\n2. simp\n3. induction n")
    gen = TacticGenerator(llm)
    state = ProofState(theorem_statement="theorem ex (n : ℕ) : n + 0 = n := by")
    tactics = await gen.generate(state)
    assert tactics == ["rfl", "simp", "induction n"]


class FakeRunner:
    def __init__(
        self,
        results: dict[str, LeanResult],
        default: LeanResult | None = None,
    ):
        self.results = results
        self.default = default or LeanResult(success=False, errors=["unknown"])

    async def check_proof(self, code: str) -> LeanResult:
        return self.results.get(code, self.default)


class ImportRepairRunner:
    """Runner that succeeds once the expected import is present."""

    def __init__(self, import_marker: str) -> None:
        self.import_marker = import_marker

    async def check_proof(self, code: str) -> LeanResult:
        if self.import_marker in code:
            return LeanResult(success=True)
        return LeanResult(
            success=False,
            errors=["Unknown constant 'Nat.Prime'\n⊢ Nat.Prime n"],
        )


@pytest.mark.asyncio
async def test_proof_search_repairs_imports_on_unknown_constant():
    from math_agent.lean.premise_retriever import PremiseEntry, PremiseRetriever

    llm = FakeLLM("1. rfl")
    gen = TacticGenerator(llm)
    retriever = PremiseRetriever(entries=[
        PremiseEntry(name="Nat.Prime", module="Mathlib.Data.Nat.Prime", type="Nat -> Prop"),
    ])
    runner = ImportRepairRunner(import_marker="import Mathlib.Data.Nat.Prime")
    search = ProofSearch(
        generator=gen,
        runner=runner,
        premise_retriever=retriever,
        max_attempts=4,
    )
    result: ProofSearchResult = await search.search("theorem ex (n : ℕ) : Nat.Prime n := by")
    assert result.success
    assert "import Mathlib.Data.Nat.Prime" in result.proof


class MultiImportRepairRunner:
    """Runner that fails with a different unknown constant until all imports are present."""

    def __init__(self, import_to_constant: dict[str, str]) -> None:
        self.import_to_constant = import_to_constant

    async def check_proof(self, code: str) -> LeanResult:
        for import_line, constant in self.import_to_constant.items():
            if import_line not in code:
                return LeanResult(
                    success=False,
                    errors=[f"Unknown constant '{constant}'\n⊢ {constant} n"],
                )
        return LeanResult(success=True)


@pytest.mark.asyncio
async def test_proof_search_accumulates_multiple_import_repairs():
    from math_agent.lean.premise_retriever import PremiseEntry, PremiseRetriever

    llm = FakeLLM("1. rfl")
    gen = TacticGenerator(llm)
    retriever = PremiseRetriever(entries=[
        PremiseEntry(name="Nat.Prime", module="Mathlib.Data.Nat.Prime", type="Nat -> Prop", file="Mathlib/Data/Nat/Prime.lean", line=10),
        PremiseEntry(name="Nat.Fib", module="Mathlib.Data.Nat.Fib", type="Nat -> Nat", file="Mathlib/Data/Nat/Fib.lean", line=20),
    ])
    runner = MultiImportRepairRunner({
        "import Mathlib.Data.Nat.Prime": "Nat.Prime",
        "import Mathlib.Data.Nat.Fib": "Nat.Fib",
    })
    search = ProofSearch(
        generator=gen,
        runner=runner,
        premise_retriever=retriever,
        max_attempts=8,
    )
    result: ProofSearchResult = await search.search(
        "theorem ex (n : ℕ) : Nat.Prime n ∧ Nat.Fib n > 0 := by"
    )
    assert result.success
    assert "import Mathlib.Data.Nat.Prime" in result.proof
    assert "import Mathlib.Data.Nat.Fib" in result.proof


@pytest.mark.asyncio
async def test_proof_search_finds_closed_proof():
    llm = FakeLLM("1. rfl")
    gen = TacticGenerator(llm)
    # The runner succeeds once the tactic 'rfl' is appended.
    runner = FakeRunner(
        {
            "theorem ex : 1 = 1 := by\n  rfl": LeanResult(success=True),
        }
    )
    search = ProofSearch(generator=gen, runner=runner, max_attempts=4)
    result: ProofSearchResult = await search.search("theorem ex : 1 = 1 := by")
    assert result.success
    assert "rfl" in result.proof


@pytest.mark.asyncio
async def test_proof_search_returns_false_when_exhausted():
    llm = FakeLLM("1. simp")
    gen = TacticGenerator(llm)
    # Preserve the goal in errors so each generated child state is enqueued.
    runner = FakeRunner(
        {},
        default=LeanResult(success=False, errors=["error: unsolved goals\n⊢ 1 = 1"]),
    )
    search = ProofSearch(generator=gen, runner=runner, max_attempts=3, max_depth=8)
    result: ProofSearchResult = await search.search("theorem ex : 1 = 1 := by")
    assert not result.success
    assert result.attempts == 3


@pytest.mark.asyncio
async def test_proof_search_override_max_attempts_in_error_and_count():
    llm = FakeLLM("1. simp")
    gen = TacticGenerator(llm)
    runner = FakeRunner(
        {},
        default=LeanResult(success=False, errors=["error: unsolved goals\n⊢ 1 = 1"]),
    )
    # Instance default is high, but the call overrides it.
    search = ProofSearch(generator=gen, runner=runner, max_attempts=10, max_depth=8)
    result: ProofSearchResult = await search.search(
        "theorem ex : 1 = 1 := by", max_attempts=2
    )
    assert not result.success
    assert result.attempts == 2
    assert "max 2" in result.error
    assert "max 10" not in result.error


@pytest.mark.asyncio
async def test_proof_search_respects_max_depth():
    llm = FakeLLM("1. apply h")
    gen = TacticGenerator(llm)
    # The proof only closes at depth 2, but max_depth=1 prevents reaching it.
    runner = FakeRunner(
        {
            "theorem ex : 1 = 1 := by\n  apply h\n  apply h": LeanResult(success=True),
        },
        default=LeanResult(success=False, errors=["error: unsolved goals\n⊢ 1 = 1"]),
    )
    search = ProofSearch(generator=gen, runner=runner, max_attempts=8, max_depth=1)
    result: ProofSearchResult = await search.search("theorem ex : 1 = 1 := by")
    assert not result.success


class CountingRunner:
    """Runner that tracks how many times each proof code is checked."""

    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    async def check_proof(self, code: str) -> LeanResult:
        self.calls[code] = self.calls.get(code, 0) + 1
        if self.calls[code] >= 2:
            return LeanResult(success=True)
        return LeanResult(success=False, errors=["unknown"])


@pytest.mark.asyncio
async def test_proof_search_prunes_duplicate_states():
    llm = FakeLLM("")
    gen = TacticGenerator(llm)

    # Bypass the generator's own deduplication so duplicate tactics are
    # delivered to the search loop.
    async def _generate(_state: ProofState) -> list[str]:
        return ["simp", "simp"]

    gen.generate = _generate
    runner = CountingRunner()
    search = ProofSearch(generator=gen, runner=runner, max_attempts=4, max_depth=8)
    result: ProofSearchResult = await search.search("theorem ex : 1 = 1 := by")
    assert not result.success
    # Duplicate states should be skipped before a second check, so no code
    # should ever reach a second check_proof call.
    assert all(count == 1 for count in runner.calls.values())


class ImmediateSuccessAfterImportRunner:
    """Runner that succeeds as soon as the required import is present."""

    def __init__(self, import_marker: str) -> None:
        self.import_marker = import_marker
        self.calls: list[str] = []

    async def check_proof(self, code: str) -> LeanResult:
        self.calls.append(code)
        if self.import_marker in code:
            return LeanResult(success=True)
        return LeanResult(
            success=False,
            errors=["Unknown constant 'Nat.Prime'\n⊢ Nat.Prime n"],
        )


@pytest.mark.asyncio
async def test_proof_search_rechecks_repaired_state_immediately():
    from math_agent.lean.premise_retriever import PremiseEntry, PremiseRetriever

    llm = FakeLLM("1. rfl")
    gen = TacticGenerator(llm)
    retriever = PremiseRetriever(entries=[
        PremiseEntry(name="Nat.Prime", module="Mathlib.Data.Nat.Prime", type="Nat -> Prop"),
    ])
    runner = ImmediateSuccessAfterImportRunner(
        import_marker="import Mathlib.Data.Nat.Prime"
    )
    # With the old behavior the repaired state would be re-enqueued for a
    # second tactic-generation step, so max_attempts=1 would exhaust the
    # budget before success. The fix re-checks immediately and returns
    # success in a single attempt.
    search = ProofSearch(
        generator=gen,
        runner=runner,
        premise_retriever=retriever,
        max_attempts=1,
    )
    result: ProofSearchResult = await search.search(
        "theorem ex (n : ℕ) : Nat.Prime n := by"
    )
    assert result.success
    assert "import Mathlib.Data.Nat.Prime" in result.proof
    assert result.attempts == 1
