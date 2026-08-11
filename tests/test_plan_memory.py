from __future__ import annotations

import json

import pytest

from math_agent.agent.plan_memory import PlanMemory, _validated_entry
from math_agent.agent.planner import FormalizationPlan


LEMMA_STRING_FIELDS = (
    "name",
    "statement",
    "formal_statement",
    "proof_hint",
    "proof_sketch",
    "recommended_theorem",
    "recommended_module",
)


def _plan(strategy: str = "Reduce the parity goal modulo two.") -> FormalizationPlan:
    return FormalizationPlan(
        problem="Prove every even square is divisible by four",
        goal_type="theorem",
        proof_strategy=strategy,
        lemmas=[{"statement": "An even number is twice an integer."}],
    )


def test_recorded_plan_is_retrievable(tmp_path):
    memory = PlanMemory(tmp_path / "runtime.jsonl", seed_path=None)
    memory.record(
        "Prove every even square is divisible by four",
        "theorem",
        _plan(),
        verified_code="theorem even_square : True := by trivial",
    )

    matches = memory.retrieve(
        "Show that the square of an even integer is divisible by four",
        k=1,
        min_score=0.1,
    )

    assert len(matches) == 1
    assert matches[0].proof_strategy == "Reduce the parity goal modulo two."
    assert matches[0].memory_id.startswith("plan-")
    assert matches[0].verification_status == "verified"

    stored = json.loads((tmp_path / "runtime.jsonl").read_text(encoding="utf-8"))
    assert stored["id"] == matches[0].memory_id
    assert stored["verification_status"] == "verified"


def test_recorded_chinese_plan_is_retrievable_from_paraphrase(tmp_path):
    memory = PlanMemory(tmp_path / "runtime.jsonl", seed_path=None)
    memory.record(
        "证明任意偶数的平方都能被四整除",
        "theorem",
        FormalizationPlan(
            problem="证明任意偶数的平方都能被四整除",
            goal_type="theorem",
            proof_strategy="把偶数写成二倍整数，然后展开平方。",
            lemmas=[{"statement": "偶数可以写成二倍整数"}],
        ),
    )

    matches = memory.retrieve(
        "请证明：若一个整数是偶数，则它的平方可被 4 整除",
        k=1,
        min_score=0.1,
    )

    assert len(matches) == 1
    assert matches[0].proof_strategy == "把偶数写成二倍整数，然后展开平方。"
    assert matches[0].verification_status == "reviewed"


def test_unverified_plan_cannot_claim_verified_status(tmp_path):
    runtime = tmp_path / "runtime.jsonl"
    memory = PlanMemory(runtime, seed_path=None)
    memory.record(
        "Prove a parity statement",
        "theorem",
        _plan(),
        verification_status="verified",
    )

    stored = json.loads(runtime.read_text(encoding="utf-8"))
    matches = memory.retrieve("parity statement", k=1, min_score=0.1)

    assert stored["verification_status"] == "reviewed"
    assert matches[0].verification_status == "reviewed"


def test_recording_never_mutates_shared_seed_file(tmp_path):
    seed = tmp_path / "seed.jsonl"
    seed.write_text("", encoding="utf-8")
    runtime = tmp_path / "tenant" / "runtime.jsonl"
    before = seed.read_bytes()

    memory = PlanMemory(runtime, seed_path=seed)
    memory.record("Parity", "theorem", _plan())

    assert seed.read_bytes() == before
    assert runtime.read_text(encoding="utf-8").strip()


def test_malformed_jsonl_entries_are_skipped_without_breaking_index(tmp_path):
    runtime = tmp_path / "runtime.jsonl"
    valid = {
        "problem": "Prove a parity statement",
        "goal_type": "theorem",
        "plan": {
            "problem": "Prove a parity statement",
            "goal_type": "theorem",
            "proof_strategy": "Reduce modulo two.",
            "lemmas": [{"statement": "Every even integer is twice an integer."}],
        },
        "verified_code": "theorem parity : True := by trivial",
    }
    runtime.write_text(
        "\n".join(
            [
                json.dumps(["not", "a", "mapping"]),
                json.dumps({"problem": "bad plan", "plan": []}),
                json.dumps({"problem": ["bad problem"], "plan": {}}),
                json.dumps(valid),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    memory = PlanMemory(runtime, seed_path=None)

    matches = memory.retrieve("Show a parity statement", k=2, min_score=0.1)
    assert [match.proof_strategy for match in matches] == ["Reduce modulo two."]


def _memory_entry_with_lemma(lemma):
    return {
        "problem": "Prove a parity statement",
        "goal_type": "theorem",
        "plan": {
            "problem": "Prove a parity statement",
            "goal_type": "theorem",
            "proof_strategy": "Reduce modulo two.",
            "lemmas": [lemma],
        },
        "verified_code": "theorem parity : True := by trivial",
    }


@pytest.mark.parametrize("lemma_field", LEMMA_STRING_FIELDS)
def test_validated_entry_rejects_null_lemma_string_fields(lemma_field):
    entry = _memory_entry_with_lemma({"statement": "True", lemma_field: None})

    assert _validated_entry(entry) is None


@pytest.mark.parametrize("lemma_field", LEMMA_STRING_FIELDS)
def test_jsonl_entries_with_null_lemma_string_fields_are_skipped(tmp_path, lemma_field):
    runtime = tmp_path / "runtime.jsonl"
    malformed = _memory_entry_with_lemma({"statement": "True", lemma_field: None})
    valid = _memory_entry_with_lemma({"statement": "True", "proof_hint": "trivial"})
    runtime.write_text(
        json.dumps(malformed) + "\n" + json.dumps(valid) + "\n",
        encoding="utf-8",
    )

    memory = PlanMemory(runtime, seed_path=None)

    assert [entry["problem"] for entry in memory._entries] == [valid["problem"]]


@pytest.mark.parametrize("lemma_field", LEMMA_STRING_FIELDS)
def test_record_appends_zero_bytes_for_null_lemma_string_fields(tmp_path, lemma_field):
    runtime = tmp_path / "runtime.jsonl"
    runtime.write_bytes(b"")
    memory = PlanMemory(runtime, seed_path=None)
    plan = _plan()
    plan.lemmas = [{"statement": "True", lemma_field: None}]

    memory.record("Parity", "theorem", plan)

    assert runtime.read_bytes() == b""


def test_instances_sharing_tenant_file_refresh_after_external_record(tmp_path):
    runtime = tmp_path / "tenant" / "runtime.jsonl"
    reader = PlanMemory(runtime, seed_path=None)
    writer = PlanMemory(runtime, seed_path=None)

    writer.record("A new parity theorem", "theorem", _plan("Use parity witnesses."))

    matches = reader.retrieve("new parity theorem", k=1, min_score=0.1)
    assert matches
    assert matches[0].proof_strategy == "Use parity witnesses."


def test_plan_memory_locks_are_scoped_to_runtime_tenant_file(tmp_path):
    shared_path = tmp_path / "tenant-a" / "runtime.jsonl"
    first = PlanMemory(shared_path, seed_path=None)
    second = PlanMemory(shared_path, seed_path=None)
    other = PlanMemory(tmp_path / "tenant-b" / "runtime.jsonl", seed_path=None)

    assert first._file_lock is second._file_lock
    assert first._file_lock is not other._file_lock


def test_runtime_path_equal_to_seed_is_never_written(tmp_path):
    seed = tmp_path / "seed.jsonl"
    seed.write_text("", encoding="utf-8")
    before = seed.read_bytes()

    memory = PlanMemory(seed, seed_path=seed)
    memory.record("Parity", "theorem", _plan())

    assert seed.read_bytes() == before


def test_record_defers_index_rebuild_until_retrieval(tmp_path, monkeypatch):
    memory = PlanMemory(tmp_path / "runtime.jsonl", seed_path=None)
    rebuild_calls = 0
    original_rebuild = memory._rebuild_index

    def counting_rebuild():
        nonlocal rebuild_calls
        rebuild_calls += 1
        original_rebuild()

    monkeypatch.setattr(memory, "_rebuild_index", counting_rebuild)

    memory.record("Prove a parity statement", "theorem", _plan())
    memory.record("Prove an even square claim", "theorem", _plan("Expand the square."))
    # Recording must not pay for an index rebuild.
    assert rebuild_calls == 0

    matches = memory.retrieve("parity statement", k=1, min_score=0.1)
    # The first retrieval after writes rebuilds exactly once.
    assert rebuild_calls == 1
    assert [match.proof_strategy for match in matches] == [
        "Reduce the parity goal modulo two."
    ]

    memory.retrieve("even square claim", k=1, min_score=0.1)
    # The index is clean again: no further rebuild until the next record.
    assert rebuild_calls == 1

    memory.record("Prove an odd cube claim", "theorem", _plan("Cube both sides."))
    assert memory.retrieve("odd cube claim", k=1, min_score=0.1)
    assert rebuild_calls == 2
