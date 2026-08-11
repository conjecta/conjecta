from math_agent.web.app import persist_turn
from math_agent.web.project_store import ProjectStore


def test_persist_turn_stores_answer_and_attachment_metadata(tmp_path):
    store = ProjectStore(root=tmp_path)
    files = [{"kind": "image", "data_url": "data:image/png;base64,AAAA", "name": "q.png"}]
    rec = persist_turn(store, "p1", "read this", "answer=4", files)
    assert rec["answer"] == "answer=4"
    turns = store.list_turns("p1")
    assert turns[0]["attachments"] == [{"kind": "image", "name": "q.png"}]  # no base64 stored


def test_persist_turn_stores_math_artifacts(tmp_path):
    store = ProjectStore(root=tmp_path)
    rec = persist_turn(
        store,
        "p1",
        "prove it",
        "answer=4",
        [],
        verification_status="verified",
        strategy="formal",
        session_id="sess-1",
        lean_proofs=["theorem t : True := trivial"],
        verification_issues=["minor gap"],
    )
    assert rec["verification_status"] == "verified"
    turn = store.list_turns("p1")[0]
    assert turn["strategy"] == "formal"
    assert turn["session_id"] == "sess-1"
    assert turn["lean_proofs"] == ["theorem t : True := trivial"]
    assert turn["verification_issues"] == ["minor gap"]


def test_persist_turn_updates_pending_turn_with_math_artifacts(tmp_path):
    store = ProjectStore(root=tmp_path)
    pending = persist_turn(store, "p1", "prove it", "", [])
    persist_turn(
        store,
        "p1",
        "prove it",
        "answer=4",
        [],
        turn_id=pending["id"],
        verification_status="verified",
        lean_proofs=["theorem t : True := trivial"],
    )
    turns = store.list_turns("p1")
    assert len(turns) == 1
    assert turns[0]["answer"] == "answer=4"
    assert turns[0]["verification_status"] == "verified"
    assert turns[0]["lean_proofs"] == ["theorem t : True := trivial"]


def test_persist_turn_stores_tool_evidence(tmp_path):
    store = ProjectStore(root=tmp_path)
    evidence = [
        {
            "tool": "compute",
            "step_num": 1,
            "args_preview": "print(2+2)",
            "success": True,
            "output_preview": "4",
            "duration_seconds": 0.01,
        }
    ]
    rec = persist_turn(store, "p1", "prove it", "answer=4", [], tool_evidence=evidence)
    assert rec["tool_evidence"] == evidence
    assert store.list_turns("p1")[0]["tool_evidence"] == evidence


def test_persist_turn_updates_pending_turn_with_tool_evidence(tmp_path):
    store = ProjectStore(root=tmp_path)
    pending = persist_turn(store, "p1", "prove it", "", [])
    persist_turn(
        store,
        "p1",
        "prove it",
        "answer=4",
        [],
        turn_id=pending["id"],
        tool_evidence=[{"tool": "compute", "success": True}],
    )
    turns = store.list_turns("p1")
    assert len(turns) == 1
    assert turns[0]["tool_evidence"] == [{"tool": "compute", "success": True}]
