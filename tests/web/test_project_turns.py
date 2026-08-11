import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from math_agent.web import app as web_app
from math_agent.web.project_store import ProjectStore, project_store_for_user
from math_agent.web.security import LOCAL_DEV_USER_ID


MATH_ARTIFACT_KEYS = (
    "verification_status",
    "strategy",
    "session_id",
    "lean_proofs",
    "verification_issues",
    "tool_evidence",
)


def test_add_turn_stores_math_artifacts(tmp_path):
    store = ProjectStore(root=tmp_path)
    turn = store.add_turn(
        "p1",
        {
            "problem": "prove sqrt(2) irrational",
            "answer": "suppose not...",
            "attachments": [],
            "verification_status": "verified",
            "strategy": "formal",
            "session_id": "sess-1",
            "lean_proofs": ["theorem t : True := trivial"],
            "verification_issues": ["minor gap"],
        },
    )
    assert turn["verification_status"] == "verified"
    assert turn["strategy"] == "formal"
    assert turn["session_id"] == "sess-1"
    assert turn["lean_proofs"] == ["theorem t : True := trivial"]
    assert turn["verification_issues"] == ["minor gap"]

    listed = store.list_turns("p1")
    assert listed[0]["verification_status"] == "verified"
    assert listed[0]["lean_proofs"] == ["theorem t : True := trivial"]

    store.save_project("p1", {"name": "P"})
    response = store.get_project("p1")
    assert response["turns"][0]["verification_status"] == "verified"
    assert response["turns"][0]["session_id"] == "sess-1"


def test_add_turn_truncates_math_artifacts(tmp_path):
    store = ProjectStore(root=tmp_path)
    turn = store.add_turn(
        "p1",
        {
            "problem": "q",
            "answer": "a",
            "attachments": [],
            "verification_status": "x" * 64,
            "strategy": "y" * 64,
            "session_id": "z" * 256,
            "lean_proofs": ["p" * 30000] * 25,
            "verification_issues": ["i" * 3000] * 25,
        },
    )
    assert turn["verification_status"] == "x" * 32
    assert turn["strategy"] == "y" * 32
    assert turn["session_id"] == "z" * 128
    assert len(turn["lean_proofs"]) == 20
    assert all(len(proof) == 20000 for proof in turn["lean_proofs"])
    assert len(turn["verification_issues"]) == 20
    assert all(len(issue) == 2000 for issue in turn["verification_issues"])


def test_add_turn_stores_and_truncates_tool_evidence(tmp_path):
    store = ProjectStore(root=tmp_path)
    evidence = [
        {
            "tool": "compute",
            "step_num": index,
            "output_preview": "o" * 600,
            "success": True,
        }
        for index in range(60)
    ]
    evidence.insert(0, "not-a-dict")
    turn = store.add_turn(
        "p1",
        {"problem": "q", "answer": "a", "attachments": [], "tool_evidence": evidence},
    )
    stored = turn["tool_evidence"]
    assert len(stored) == 49
    assert stored[0]["step_num"] == 0
    # Preview fields keep a larger budget so compute code/output stays readable.
    assert stored[0]["output_preview"] == "o" * 600
    assert stored[0]["success"] is True
    assert store.list_turns("p1")[0]["tool_evidence"] == stored


def test_tool_evidence_preview_fields_cap_at_2000(tmp_path):
    store = ProjectStore(root=tmp_path)
    turn = store.add_turn(
        "p1",
        {
            "problem": "q",
            "answer": "a",
            "attachments": [],
            "tool_evidence": [
                {"tool": "compute", "args_preview": "a" * 3000, "other": "b" * 600}
            ],
        },
    )
    (entry,) = turn["tool_evidence"]
    assert entry["args_preview"] == "a" * 2000
    assert entry["other"] == "b" * 500


def test_update_turn_patches_tool_evidence(tmp_path):
    store = ProjectStore(root=tmp_path)
    turn = store.add_turn("p1", {"problem": "q", "answer": "", "attachments": []})
    store.update_turn(
        "p1",
        turn["id"],
        answer="a",
        tool_evidence=[{"tool": "compute", "success": True, "output_preview": "4"}],
    )
    stored = store.list_turns("p1")[0]
    assert stored["tool_evidence"] == [
        {"tool": "compute", "success": True, "output_preview": "4"}
    ]


def test_add_turn_omits_math_artifact_keys_by_default(tmp_path):
    store = ProjectStore(root=tmp_path)
    turn = store.add_turn("p1", {"problem": "q", "answer": "a", "attachments": []})
    for key in MATH_ARTIFACT_KEYS:
        assert key not in turn
    assert all(key not in store.list_turns("p1")[0] for key in MATH_ARTIFACT_KEYS)


def test_update_turn_patches_math_artifacts(tmp_path):
    store = ProjectStore(root=tmp_path)
    turn = store.add_turn("p1", {"problem": "q", "answer": "", "attachments": []})
    store.update_turn(
        "p1",
        turn["id"],
        answer="a",
        verification_status="verified",
        strategy="formal",
        session_id="sess-1",
        lean_proofs=["theorem t : True := trivial"],
        verification_issues=["minor gap"],
    )
    stored = store.list_turns("p1")[0]
    assert stored["answer"] == "a"
    assert stored["verification_status"] == "verified"
    assert stored["strategy"] == "formal"
    assert stored["session_id"] == "sess-1"
    assert stored["lean_proofs"] == ["theorem t : True := trivial"]
    assert stored["verification_issues"] == ["minor gap"]


def test_update_turn_without_fields_still_rejected(tmp_path):
    store = ProjectStore(root=tmp_path)
    turn = store.add_turn("p1", {"problem": "q", "answer": "", "attachments": []})
    with pytest.raises(HTTPException) as excinfo:
        store.update_turn("p1", turn["id"])
    assert excinfo.value.status_code == 400


def test_delete_conversation_removes_matching_turns(tmp_path):
    store = ProjectStore(root=tmp_path)
    store.add_turn(
        "p1",
        {"conversation_id": "c1", "problem": "q1", "answer": "a1", "attachments": []},
    )
    store.add_turn(
        "p1",
        {"conversation_id": "c1", "problem": "q2", "answer": "a2", "attachments": []},
    )
    store.add_turn(
        "p1",
        {"conversation_id": "c2", "problem": "other", "answer": "x", "attachments": []},
    )

    deleted = store.delete_conversation("p1", "c1")
    assert deleted == 2
    remaining = store.list_turns("p1")
    assert [turn["conversation_id"] for turn in remaining] == ["c2"]


def test_delete_conversation_supports_legacy_turn_id_grouping(tmp_path):
    store = ProjectStore(root=tmp_path)
    turn = store.add_turn("p1", {"problem": "legacy", "answer": "a", "attachments": []})
    assert turn["conversation_id"] == ""

    deleted = store.delete_conversation("p1", turn["id"])
    assert deleted == 1
    assert store.list_turns("p1") == []


def test_delete_conversation_persists_across_reload(tmp_path):
    store = ProjectStore(root=tmp_path)
    store.add_turn(
        "p1",
        {"conversation_id": "c1", "problem": "q", "answer": "a", "attachments": []},
    )
    store.delete_conversation("p1", "c1")

    reloaded = ProjectStore(root=tmp_path)
    assert reloaded.list_turns("p1") == []


def test_delete_conversation_endpoint(monkeypatch, tmp_path):
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    web_app._PROJECT_STORE_CACHE.clear()

    store = project_store_for_user(LOCAL_DEV_USER_ID)
    store.add_turn(
        "p1",
        {"conversation_id": "c1", "problem": "q", "answer": "a", "attachments": []},
    )

    client = TestClient(web_app.app)
    missing = client.delete("/api/projects/p1/conversations/missing")
    assert missing.status_code == 404

    resp = client.delete("/api/projects/p1/conversations/c1")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "deleted": 1}
    assert project_store_for_user(LOCAL_DEV_USER_ID).list_turns("p1") == []
