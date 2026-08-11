from math_agent.web.app import persist_pending_turn, persist_turn
from math_agent.web.project_store import ProjectStore


def test_update_turn_fills_pending_answer(tmp_path):
    store = ProjectStore(root=tmp_path)
    pending = store.add_turn(
        "p1",
        {
            "conversation_id": "c1",
            "problem": "Prove 1+1=2",
            "answer": "",
            "attachments": [],
        },
    )
    updated = store.update_turn("p1", pending["id"], answer="By Peano axioms.")
    assert updated["answer"] == "By Peano axioms."
    assert store.list_turns("p1") == [updated]


def test_persist_pending_then_complete_updates_same_turn(tmp_path):
    store = ProjectStore(root=tmp_path)
    files = [{"kind": "image", "data_url": "data:image/png;base64,AAAA", "name": "q.png"}]
    pending = persist_pending_turn(
        store, "p1", "read this", files, conversation_id="c1"
    )
    assert pending["answer"] == ""
    assert pending["attachments"] == [{"kind": "image", "name": "q.png"}]

    completed = persist_turn(
        store,
        "p1",
        "read this",
        "answer=4",
        files,
        conversation_id="c1",
        turn_id=pending["id"],
    )
    assert completed["id"] == pending["id"]
    assert completed["answer"] == "answer=4"
    assert len(store.list_turns("p1")) == 1


def test_persist_turn_without_turn_id_still_appends(tmp_path):
    store = ProjectStore(root=tmp_path)
    rec = persist_turn(store, "p1", "q", "a", [])
    assert rec["answer"] == "a"
    assert len(store.list_turns("p1")) == 1
