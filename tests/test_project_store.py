from __future__ import annotations

import json
import threading

import pytest

from math_agent.web import project_store as project_store_module
from math_agent.web.project_store import ProjectStore


def _events(root):
    path = root / "events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_project_store_saves_project_and_review_queue(tmp_path):
    store = ProjectStore(tmp_path)

    saved = store.save_project("proj-1", {"name": "Demo"})
    assert saved["project"]["id"] == "proj-1"
    assert (tmp_path / "events.jsonl").exists()
    assert not (tmp_path / "proj-1.json").exists()
    assert store.list_projects()[0]["name"] == "Demo"

    item = store.add_review_item("proj-1", {"kind": "fact", "title": "Candidate"})
    assert item["status"] == "open"
    assert store.list_review_items("proj-1", status="open")[0]["title"] == "Candidate"

    resolved = store.resolve_review_item("proj-1", item["id"], status="approved", reason="Useful")
    assert resolved["status"] == "approved"
    assert resolved["reason"] == "Useful"
    assert [event["type"] for event in _events(tmp_path)] == [
        "project_saved",
        "review_item_added",
        "review_item_resolved",
    ]


def test_project_store_keeps_conversation_id_on_turns(tmp_path):
    store = ProjectStore(tmp_path)

    first = store.add_turn(
        "proj-1",
        {"conversation_id": "conversation-1", "problem": "First", "answer": "A"},
    )
    second = store.add_turn(
        "proj-1",
        {"conversation_id": "conversation-1", "problem": "Follow-up", "answer": "B"},
    )

    assert first["conversation_id"] == "conversation-1"
    assert second["conversation_id"] == "conversation-1"
    assert [turn["conversation_id"] for turn in store.list_turns("proj-1")] == [
        "conversation-1",
        "conversation-1",
    ]


def test_project_store_persists_knowledge_to_jsonl(tmp_path):
    store = ProjectStore(tmp_path)

    inserted = store.add_many(
        "proj-1",
        [{"statement": "A -> A", "why": "Identity", "source": "note", "status": "approved"}],
        [{"title": "Use identity", "body": "Close trivial goals directly.", "kind": "heuristic", "status": "approved"}],
        [{"title": "Contradiction", "body": "Assume the negation.", "category": "contradiction", "status": "approved"}],
    )

    assert inserted["facts"][0]["project_id"] == "proj-1"
    assert store.list_facts("proj-1")[0]["statement"] == "A -> A"
    assert store.list_intuitions("proj-1")[0]["title"] == "Use identity"
    assert store.list_tricks("proj-1")[0]["category"] == "contradiction"
    assert store.search_facts("proj-1", "identity")[0]["statement"] == "A -> A"
    assert _events(tmp_path)[0]["type"] == "knowledge_added"


def test_project_store_preserves_knowledge_metadata(tmp_path):
    store = ProjectStore(tmp_path)

    inserted = store.add_many(
        "proj-1",
        [
            {
                "statement": "Prime divisibility",
                "why": "Useful in gcd proofs.",
                "formal_status": "lean_verified",
                "lean_name": "Nat.Prime.dvd_mul",
                "source_type": "lean_verified",
                "source_ref": "Nat.Prime.dvd_mul",
                "evidence": "theorem Nat.Prime.dvd_mul ...",
                "confidence": "1.0",
                "status": "verified",
                "domain": "number_theory",
                "tags": "prime,divisibility",
                "created_by": "lean_promotion",
            }
        ],
        [
            {
                "title": "Use modular residues",
                "body": "Check residues modulo a small base.",
                "kind": "heuristic",
                "source_type": "agent_trace",
                "confidence": "0.72",
                "status": "candidate",
            }
        ],
        [
            {
                "title": "Infinite descent",
                "body": "Derive a smaller solution.",
                "category": "descent",
                "applicability": "Diophantine equations.",
                "failure_mode": "No smaller solution.",
                "source_type": "pdf",
            }
        ],
    )

    assert inserted["facts"][0]["formal_status"] == "lean_verified"
    assert store.list_facts("proj-1")[0]["lean_name"] == "Nat.Prime.dvd_mul"
    assert store.list_intuitions("proj-1")[0]["confidence"] == "0.72"
    assert store.list_tricks("proj-1")[0]["applicability"] == "Diophantine equations."


def test_project_store_reads_embedded_project_knowledge(tmp_path):
    store = ProjectStore(tmp_path)

    store.save_project(
        "proj-1",
        {
            "name": "Demo",
            "facts": [{"id": "f1", "statement": "Embedded fact"}],
            "intuitions": [{"id": "i1", "title": "Embedded intuition", "body": "Body"}],
            "tricks": [{"id": "t1", "title": "Embedded trick", "body": "Body"}],
        },
    )

    assert store.list_facts("proj-1")[0]["statement"] == "Embedded fact"
    assert store.list_intuitions("proj-1")[0]["title"] == "Embedded intuition"
    assert store.list_tricks("proj-1")[0]["title"] == "Embedded trick"


def test_project_store_reads_legacy_snapshot(tmp_path):
    legacy = {
        "project": {
            "id": "legacy",
            "name": "Legacy",
            "facts": [{"id": "f1", "statement": "Legacy fact"}],
        },
        "reviewQueue": [{"id": "r1", "status": "open", "title": "Legacy review"}],
        "updatedAt": "2026-01-01T00:00:00+00:00",
    }
    (tmp_path / "legacy.json").write_text(json.dumps(legacy), encoding="utf-8")
    store = ProjectStore(tmp_path)

    assert store.get_project("legacy")["project"]["name"] == "Legacy"
    assert store.list_review_items("legacy")[0]["title"] == "Legacy review"
    assert store.list_facts("legacy")[0]["statement"] == "Legacy fact"


def test_project_store_rejects_path_traversal(tmp_path):
    store = ProjectStore(tmp_path)
    with pytest.raises(Exception) as exc:
        store.save_project("../bad", {"name": "Bad"})
    assert exc.value.status_code == 400


def test_solve_search_only_returns_explicitly_trusted_items(tmp_path):
    store = ProjectStore(tmp_path)
    store.add_many(
        "proj-1",
        [
            {"statement": "shared candidate", "status": "candidate"},
            {"statement": "shared rejected", "status": "rejected"},
            {"statement": "shared approved", "status": "approved"},
            {"statement": "shared verified", "status": "verified"},
        ],
        [],
        [],
    )

    assert {row["status"] for row in store.list_facts("proj-1")} == {
        "candidate",
        "rejected",
        "approved",
        "verified",
    }
    assert [row["status"] for row in store.search_facts("proj-1", "shared")] == [
        "approved",
        "verified",
    ]


def test_manual_knowledge_is_explicitly_approved(tmp_path):
    store = ProjectStore(tmp_path)

    item = store.add_fact("proj-1", "manual searchable fact")

    assert item["status"] == "approved"
    assert store.search_facts("proj-1", "searchable") == [item]


def test_project_search_ranks_partial_multilingual_problem_query(tmp_path):
    store = ProjectStore(tmp_path)
    store.add_many(
        "proj-1",
        [
            {
                "statement": "偶数的平方可以被四整除",
                "why": "把偶数写成 2k 后展开",
                "status": "approved",
            },
            {
                "statement": "素数有无穷多个",
                "why": "欧几里得证明",
                "status": "approved",
            },
        ],
        [],
        [],
    )

    matches = store.search_facts(
        "proj-1",
        "请证明任意偶数的平方都能够被 4 整除，并写出详细过程",
        limit=1,
    )

    assert [row["statement"] for row in matches] == ["偶数的平方可以被四整除"]


def test_repeated_state_search_and_checkpoint_reads_do_not_reparse(tmp_path, monkeypatch):
    event = {
        "type": "knowledge_added",
        "project_id": "proj-1",
        "facts": [{"id": "f1", "statement": "cached fact", "status": "approved"}],
        "intuitions": [],
        "tricks": [],
    }
    checkpoint = {"session_id": "s1", "problem": "cached checkpoint"}
    (tmp_path / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    (tmp_path / "checkpoints.jsonl").write_text(
        json.dumps(checkpoint) + "\n", encoding="utf-8"
    )
    store = ProjectStore(tmp_path)
    real_loads = json.loads
    parse_calls = 0

    def counting_loads(raw):
        nonlocal parse_calls
        parse_calls += 1
        return real_loads(raw)

    monkeypatch.setattr(project_store_module.json, "loads", counting_loads)

    assert store.list_facts("proj-1")[0]["statement"] == "cached fact"
    assert store.search_facts("proj-1", "cached")[0]["id"] == "f1"
    assert store.get_checkpoint("s1")["problem"] == "cached checkpoint"
    first_read_parse_calls = parse_calls
    assert first_read_parse_calls == 2

    for _ in range(3):
        store.list_facts("proj-1")
        store.search_facts("proj-1", "cached")
        store.get_checkpoint("s1")

    assert parse_calls == first_read_parse_calls


def test_external_file_changes_invalidate_cached_indexes(tmp_path):
    store = ProjectStore(tmp_path)
    store.add_fact("proj-1", "first approved fact")
    store.write_checkpoint({"session_id": "s1", "problem": "first"})
    assert len(store.list_facts("proj-1")) == 1
    assert store.get_checkpoint("s1")["problem"] == "first"

    external_event = {
        "type": "knowledge_added",
        "project_id": "proj-1",
        "facts": [
            {
                "id": "external",
                "statement": "externally appended approved fact",
                "status": "approved",
            }
        ],
        "intuitions": [],
        "tricks": [],
    }
    with store.event_log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(external_event) + "\n")
    with store.checkpoint_log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"session_id": "s1", "problem": "external"}) + "\n")

    assert {row["id"] for row in store.list_facts("proj-1")} >= {"external"}
    assert store.get_checkpoint("s1")["problem"] == "external"


def test_local_appends_update_indexes_without_reparse(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path)
    store.list_facts("proj-1")
    store.get_checkpoint("s1")

    def unexpected_parse(_raw):
        raise AssertionError("locally appended records must update the parsed indexes")

    monkeypatch.setattr(project_store_module.json, "loads", unexpected_parse)

    fact = store.add_fact("proj-1", "locally indexed fact")
    store.write_checkpoint({"session_id": "s1", "problem": "locally indexed"})

    assert store.list_facts("proj-1") == [fact]
    assert store.get_checkpoint("s1")["problem"] == "locally indexed"


def test_checkpoint_write_deep_copies_nested_input(tmp_path):
    store = ProjectStore(tmp_path)
    checkpoint = {
        "session_id": "s1",
        "project_context": {"facts": [{"statement": "original"}]},
        "turns": [{"observation": {"output": "original"}}],
    }

    store.write_checkpoint(checkpoint)
    checkpoint["project_context"]["facts"][0]["statement"] = "mutated"
    checkpoint["turns"][0]["observation"]["output"] = "mutated"

    saved = store.get_checkpoint("s1")
    assert saved["project_context"]["facts"][0]["statement"] == "original"
    assert saved["turns"][0]["observation"]["output"] == "original"


def test_event_rebuild_holds_append_lock_until_snapshot_is_published(tmp_path, monkeypatch):
    initial = {
        "type": "knowledge_added",
        "project_id": "proj-1",
        "facts": [{"id": "first", "statement": "first", "status": "approved"}],
        "intuitions": [],
        "tricks": [],
    }
    (tmp_path / "events.jsonl").write_text(json.dumps(initial) + "\n", encoding="utf-8")
    store = ProjectStore(tmp_path)
    real_loads = json.loads
    parse_started = threading.Event()
    release_parse = threading.Event()
    writer_done = threading.Event()

    def blocking_loads(raw):
        if threading.current_thread().name == "event-cache-reader" and not parse_started.is_set():
            parse_started.set()
            release_parse.wait(timeout=2)
        return real_loads(raw)

    monkeypatch.setattr(project_store_module.json, "loads", blocking_loads)
    reader = threading.Thread(
        target=lambda: store.list_facts("proj-1"), name="event-cache-reader"
    )
    writer = threading.Thread(
        target=lambda: (store.add_fact("proj-1", "appended"), writer_done.set()),
        name="event-cache-writer",
    )
    reader.start()
    assert parse_started.wait(timeout=1)
    writer.start()
    writer_was_blocked = not writer_done.wait(timeout=0.1)
    release_parse.set()
    reader.join(timeout=2)
    writer.join(timeout=2)

    assert writer_was_blocked
    assert {row["statement"] for row in store.list_facts("proj-1")} == {
        "first",
        "appended",
    }


def test_checkpoint_rebuild_holds_write_lock_until_snapshot_is_published(tmp_path, monkeypatch):
    (tmp_path / "checkpoints.jsonl").write_text(
        json.dumps({"session_id": "s1", "problem": "first"}) + "\n",
        encoding="utf-8",
    )
    store = ProjectStore(tmp_path)
    real_loads = json.loads
    parse_started = threading.Event()
    release_parse = threading.Event()
    writer_done = threading.Event()

    def blocking_loads(raw):
        if threading.current_thread().name == "checkpoint-cache-reader" and not parse_started.is_set():
            parse_started.set()
            release_parse.wait(timeout=2)
        return real_loads(raw)

    monkeypatch.setattr(project_store_module.json, "loads", blocking_loads)
    reader = threading.Thread(
        target=lambda: store.get_checkpoint("s1"), name="checkpoint-cache-reader"
    )
    writer = threading.Thread(
        target=lambda: (
            store.write_checkpoint({"session_id": "s1", "problem": "appended"}),
            writer_done.set(),
        ),
        name="checkpoint-cache-writer",
    )
    reader.start()
    assert parse_started.wait(timeout=1)
    writer.start()
    writer_was_blocked = not writer_done.wait(timeout=0.1)
    release_parse.set()
    reader.join(timeout=2)
    writer.join(timeout=2)

    assert writer_was_blocked
    assert store.get_checkpoint("s1")["problem"] == "appended"


def test_event_snapshots_do_not_share_nested_cache_state(tmp_path):
    store = ProjectStore(tmp_path)
    store.add_knowledge_graph_edges(
        "proj-1",
        [{"source": "a", "target": "b", "metadata": {"origin": "original"}}],
    )

    first = store.list_knowledge_graph_edges("proj-1")
    first[0]["metadata"]["origin"] = "mutated"

    assert store.list_knowledge_graph_edges("proj-1")[0]["metadata"]["origin"] == "original"


def test_external_event_append_during_local_signature_capture_is_reindexed(
    tmp_path, monkeypatch
):
    store = ProjectStore(tmp_path)
    store.list_facts("proj-1")
    original_signature = store._file_signature
    injected = False
    external = {
        "type": "knowledge_added",
        "project_id": "proj-1",
        "facts": [{"id": "external", "statement": "external", "status": "approved"}],
        "intuitions": [],
        "tricks": [],
    }

    def append_before_signature(path):
        nonlocal injected
        if path == store.event_log_path and path.exists() and not injected:
            injected = True
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(external) + "\n")
        return original_signature(path)

    monkeypatch.setattr(store, "_file_signature", append_before_signature)

    store.add_fact("proj-1", "local")

    assert {row["statement"] for row in store.list_facts("proj-1")} == {
        "local",
        "external",
    }


def test_external_checkpoint_append_during_local_signature_capture_is_reindexed(
    tmp_path, monkeypatch
):
    store = ProjectStore(tmp_path)
    store.get_checkpoint("external")
    original_signature = store._file_signature
    injected = False

    def append_before_signature(path):
        nonlocal injected
        if path == store.checkpoint_log_path and path.exists() and not injected:
            injected = True
            with path.open("a", encoding="utf-8") as handle:
                handle.write(
                    json.dumps({"session_id": "external", "problem": "external"}) + "\n"
                )
        return original_signature(path)

    monkeypatch.setattr(store, "_file_signature", append_before_signature)

    store.write_checkpoint({"session_id": "local", "problem": "local"})

    assert store.get_checkpoint("external")["problem"] == "external"
