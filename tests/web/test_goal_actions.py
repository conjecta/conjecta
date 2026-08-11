"""API tests for node-level goal actions (subgoal-level HITL)."""
from __future__ import annotations

from fastapi.testclient import TestClient

import math_agent.web.agent_factory as agent_factory
import math_agent.web.app as app_module
import math_agent.web.solve_routes as solve_routes
from math_agent.web.project_store import ProjectStore
from math_agent.web.security import LOCAL_DEV_USER_ID
from math_agent.web.solve_session import _apply_goal_action

client = TestClient(app_module.app)


def _checkpoint(**overrides):
    base = {
        "session_id": "sess-1",
        "project_id": "default",
        "strategy": "research",
        "problem": "证明 √2 是无理数",
        "proof_graph": {
            "root_id": "root",
            "active_goal_id": "g1",
            "goals": [
                {"id": "root", "statement": "原命题", "status": "failed", "depends_on": ["g1"]},
                {"id": "g1", "statement": "引理一", "status": "failed", "depends_on": []},
            ],
        },
        "research_artifacts": [],
        "research_failures": [],
        "budget_consumption": {},
        "pending_interaction": None,
        "project_context": {"user_id": LOCAL_DEV_USER_ID, "project_id": "default"},
    }
    base.update(overrides)
    return base


async def _no_user_api_key(_uid):
    return None


async def _fake_stream_solve_events(_msg, *, user_id=None):
    yield {"type": "done", "final_answer": "ok"}


def _wire(monkeypatch, tmp_path):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("CONJECTA_DISABLE_QUOTA", "1")
    store = ProjectStore(root=tmp_path / "user")
    monkeypatch.setattr(solve_routes, "_project_store", lambda _uid: store)
    monkeypatch.setattr(agent_factory, "_load_user_api_key", _no_user_api_key)
    monkeypatch.setattr(
        solve_routes, "stream_solve_events", _fake_stream_solve_events
    )
    return store


def test_goal_action_retry_resumes_run(monkeypatch, tmp_path):
    store = _wire(monkeypatch, tmp_path)
    store.write_checkpoint(_checkpoint())

    response = client.post(
        "/api/solve/sess-1/goals/g1/actions", json={"action": "retry"}
    )

    assert response.status_code == 200
    assert '"done"' in response.text
    claimed = store.get_checkpoint("sess-1")
    assert claimed["submitted_goal_action"]["action"] == "retry"
    assert claimed["submitted_goal_action"]["goal_id"] == "g1"


def test_goal_action_validates_payload(monkeypatch, tmp_path):
    store = _wire(monkeypatch, tmp_path)
    store.write_checkpoint(_checkpoint())

    assert (
        client.post("/api/solve/sess-1/goals/g1/actions", json={"action": "bogus"}).status_code
        == 400
    )
    assert (
        client.post("/api/solve/sess-1/goals/unknown/actions", json={"action": "retry"}).status_code
        == 400
    )
    assert (
        client.post("/api/solve/sess-1/goals/g1/actions", json={"action": "edit"}).status_code
        == 400
    )
    assert (
        client.post("/api/solve/missing/goals/g1/actions", json={"action": "retry"}).status_code
        == 404
    )


def test_goal_action_claim_is_once_only(monkeypatch, tmp_path):
    store = _wire(monkeypatch, tmp_path)
    store.write_checkpoint(_checkpoint())

    action = {"goal_id": "g1", "action": "retry"}
    claimed = store.claim_goal_action("sess-1", action)

    assert claimed is not None
    assert claimed["goal_action_claim_id"]
    assert claimed["submitted_goal_action"] == action
    assert store.claim_goal_action("sess-1", action) is None
    assert store.claim_goal_action("missing", action) is None


def test_apply_goal_action_retry_resets_and_activates():
    trace = _checkpoint()
    trace["context_preamble"] = "先前上下文"

    updated = _apply_goal_action(
        trace, {"goal_id": "g1", "action": "retry", "guidance": "试试反证法"}
    )

    goals = {g["id"]: g for g in updated["proof_graph"]["goals"]}
    assert goals["g1"]["status"] == "in_progress"
    # Cascade reset touched the dependent root.
    assert goals["root"]["status"] == "pending"
    assert updated["proof_graph"]["active_goal_id"] == "g1"
    assert updated["current_goal"] == "引理一"
    assert "试试反证法" in updated["context_preamble"]
    assert "先前上下文" in updated["context_preamble"]


def test_apply_goal_action_edit_rewrites_statement():
    trace = _checkpoint()
    updated = _apply_goal_action(
        trace,
        {"goal_id": "g1", "action": "edit", "statement": "引理一（修订版）"},
    )
    goals = {g["id"]: g for g in updated["proof_graph"]["goals"]}
    assert goals["g1"]["statement"] == "引理一（修订版）"
    assert updated["current_goal"] == "引理一（修订版）"


def test_apply_goal_action_unknown_goal_is_noop():
    trace = _checkpoint()
    updated = _apply_goal_action(trace, {"goal_id": "nope", "action": "retry"})
    assert updated["proof_graph"] == trace["proof_graph"]
