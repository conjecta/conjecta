"""Proof trace memory (data flywheel) and structural premise boost."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from math_agent.lean.premise_retriever import (
    PremiseEntry,
    PremiseRetriever,
    _apply_structural_boost,
)
from math_agent.lean.proof_search import ProofSearch, ProofState, TacticGenerator
from math_agent.lean.proof_trace_memory import ProofTraceMemory


def test_trace_memory_records_and_retrieves(tmp_path):
    memory = ProofTraceMemory(tmp_path / "traces.jsonl")
    memory.record(
        "theorem a : Nat.add_comm 1 2", "theorem a := by omega", attempts=3
    )
    memory.record(
        "theorem b : List.length [] = 0", "theorem b := by rfl", attempts=1
    )
    # Dedup: identical statement+proof is stored once.
    memory.record("theorem a : Nat.add_comm 1 2", "theorem a := by omega")

    similar = memory.similar("theorem c : Nat.add_comm x y", top_k=1)
    assert len(similar) == 1
    assert "Nat.add_comm" in similar[0].statement

    # Persists across instances.
    reloaded = ProofTraceMemory(tmp_path / "traces.jsonl")
    assert len(reloaded.similar("Nat.add_comm m n")) == 1


def test_trace_memory_ignores_garbage(tmp_path):
    path = tmp_path / "traces.jsonl"
    path.write_text('{"statement": 1}\nnot json\n', encoding="utf-8")
    memory = ProofTraceMemory(path)
    assert memory.similar("anything") == []


def test_record_appends_without_rewriting_existing_lines(tmp_path):
    path = tmp_path / "traces.jsonl"
    memory = ProofTraceMemory(path)
    memory.record("theorem a : Nat.add_comm 1 2", "theorem a := by omega")
    first_bytes = path.read_bytes()

    memory.record("theorem b : List.length [] = 0", "theorem b := by rfl")

    data = path.read_bytes()
    # The second record is appended; the first line is left untouched.
    assert data.startswith(first_bytes)
    assert len(data.decode("utf-8").splitlines()) == 2

    reloaded = ProofTraceMemory(path)
    assert [r.statement for r in reloaded._load()] == [
        "theorem a : Nat.add_comm 1 2",
        "theorem b : List.length [] = 0",
    ]


def test_record_compacts_only_when_over_cap(tmp_path, monkeypatch):
    monkeypatch.setattr("math_agent.lean.proof_trace_memory._MAX_RECORDS", 3)
    path = tmp_path / "traces.jsonl"
    memory = ProofTraceMemory(path)
    for index in range(5):
        memory.record(f"theorem t{index} : True", f"proof t{index}")

    lines = path.read_text(encoding="utf-8").splitlines()
    # Only the most recent records survive compaction.
    assert len(lines) == 3
    assert "t2" in lines[0] and "t4" in lines[-1]

    # In-memory state and a fresh instance agree with the file.
    reloaded = ProofTraceMemory(path)
    assert [r.statement for r in reloaded._load()] == [
        f"theorem t{index} : True" for index in (2, 3, 4)
    ]
    assert reloaded.similar("theorem t4 : True", top_k=1)[0].proof == "proof t4"


@pytest.mark.asyncio
async def test_generator_prompt_includes_similar_traces(tmp_path):
    memory = ProofTraceMemory(tmp_path / "traces.jsonl")
    memory.record(
        "theorem prev : Nat.add_comm a b",
        "theorem prev : Nat.add_comm a b := by omega",
    )
    captured: dict = {}

    class FakeLLM:
        async def complete(self, messages, system=None, temperature=0.0):
            captured["prompt"] = messages[0].content
            return SimpleNamespace(text="omega")

    generator = TacticGenerator(llm=FakeLLM(), trace_memory=memory)
    state = ProofState(theorem_statement="theorem t : Nat.add_comm x y := by")
    candidates = await generator.generate(state)
    assert candidates == ["omega"]
    assert "Verified proofs of similar statements" in captured["prompt"]
    assert "theorem prev" in captured["prompt"]


@pytest.mark.asyncio
async def test_search_records_successful_proof(tmp_path):
    memory = ProofTraceMemory(tmp_path / "traces.jsonl")

    class FakeLLM:
        async def complete(self, messages, system=None, temperature=0.0):
            return SimpleNamespace(text="trivial")

    class FakeRunner:
        async def check_proof(self, code, draft=False):
            return SimpleNamespace(success=True, errors=[], output="")

    search = ProofSearch(
        generator=TacticGenerator(llm=FakeLLM()),
        runner=FakeRunner(),
        trace_memory=memory,
    )
    result = await search.search("theorem t : True := by")
    assert result.success
    assert memory.similar("theorem t : True", top_k=1)


def test_structural_boost_prefers_shared_head_constants():
    entries = [
        PremiseEntry(name="foo", module="Mathlib.A", type="Nat -> Nat"),
        PremiseEntry(name="bar", module="Mathlib.B", type="List Nat -> Nat"),
        PremiseEntry(name="baz", module="Mathlib.C", type="String -> String"),
    ]
    ranked = _apply_structural_boost(entries, "⊢ List.length xs = n", top_k=2)
    assert ranked[0].name == "bar"
    assert len(ranked) == 2


def test_retrieve_still_works_with_boost():
    entries = [
        PremiseEntry(
            name="Nat.add_comm", module="Mathlib.Algebra", type="Nat -> Nat -> Nat"
        ),
    ]
    retriever = PremiseRetriever(entries=entries)
    results = retriever.retrieve("Nat.add_comm on Nat", top_k=1)
    assert results and results[0].name == "Nat.add_comm"


@pytest.mark.asyncio
async def test_critic_reranks_candidates():
    class MainLLM:
        async def complete(self, messages, system=None, temperature=0.0):
            return SimpleNamespace(text="simp\nomega\nrfl")

    class CriticLLM:
        async def complete(self, messages, system=None, temperature=0.0):
            # omega most promising, rfl least.
            return SimpleNamespace(text="1 4\n2 9\n3 1")

    generator = TacticGenerator(llm=MainLLM(), critic_llm=CriticLLM())
    state = ProofState(theorem_statement="theorem t : True := by")
    assert await generator.generate(state) == ["omega", "simp", "rfl"]


@pytest.mark.asyncio
async def test_critic_failure_keeps_generator_order():
    class MainLLM:
        async def complete(self, messages, system=None, temperature=0.0):
            return SimpleNamespace(text="simp\nomega\nrfl")

    class BrokenCritic:
        async def complete(self, messages, system=None, temperature=0.0):
            raise RuntimeError("critic down")

    generator = TacticGenerator(llm=MainLLM(), critic_llm=BrokenCritic())
    state = ProofState(theorem_statement="theorem t : True := by")
    assert await generator.generate(state) == ["simp", "omega", "rfl"]


@pytest.mark.asyncio
async def test_critic_garbage_scores_keep_order():
    class MainLLM:
        async def complete(self, messages, system=None, temperature=0.0):
            return SimpleNamespace(text="simp\nomega")

    class LazyCritic:
        async def complete(self, messages, system=None, temperature=0.0):
            return SimpleNamespace(text="I cannot score these.")

    generator = TacticGenerator(llm=MainLLM(), critic_llm=LazyCritic())
    state = ProofState(theorem_statement="theorem t : True := by")
    assert await generator.generate(state) == ["simp", "omega"]
