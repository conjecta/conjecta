"""API tests for the research command deck read endpoints."""
from __future__ import annotations

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient

import math_agent.web.app as app_module
import math_agent.web.research_routes as research_routes
from math_agent.web.project_store import ProjectStore
from math_agent.web.research_runs import ShareStore

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
                {"id": "root", "statement": "原命题", "status": "in_progress", "depends_on": ["g1"]},
                {"id": "g1", "statement": "引理一", "status": "in_progress", "depends_on": []},
            ],
        },
        "research_artifacts": [],
        "research_failures": [],
        "budget_consumption": {},
        "pending_interaction": None,
        "project_context": {"user_id": "u_secret", "project_id": "default"},
        "at": "2026-07-16T08:00:00+00:00",
    }
    base.update(overrides)
    return base


def _wire(monkeypatch, tmp_path):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    store = ProjectStore(root=tmp_path / "user")
    shares = ShareStore(root=tmp_path / "shares")
    monkeypatch.setattr(research_routes, "_project_store", lambda _uid: store)
    monkeypatch.setattr(research_routes, "project_store_for_user", lambda _uid: store)
    monkeypatch.setattr(research_routes, "_share_store", lambda: shares)
    monkeypatch.setattr(
        research_routes,
        "load_config",
        lambda: SimpleNamespace(agent=SimpleNamespace(artifact_root=str(tmp_path / "artifacts"))),
    )
    return store, shares


def test_list_runs_filters_research(monkeypatch, tmp_path):
    store, _ = _wire(monkeypatch, tmp_path)
    store.write_checkpoint(_checkpoint())
    store.write_checkpoint(_checkpoint(session_id="sess-2", strategy="react"))
    response = client.get("/api/research/runs")
    assert response.status_code == 200
    runs = response.json()["runs"]
    assert [run["session_id"] for run in runs] == ["sess-1"]
    assert runs[0]["status"] == "best_effort"


def test_run_detail_and_404(monkeypatch, tmp_path):
    store, _ = _wire(monkeypatch, tmp_path)
    store.write_checkpoint(_checkpoint())
    assert client.get("/api/research/runs/sess-1").status_code == 200
    assert client.get("/api/research/runs/nope").status_code == 404
    detail = client.get("/api/research/runs/sess-1").json()["run"]
    assert detail["problem"] == "证明 √2 是无理数"
    assert "project_context" not in detail


def test_artifact_endpoint_serves_file_and_rejects_unknown(monkeypatch, tmp_path):
    store, _ = _wire(monkeypatch, tmp_path)
    artifacts_root = tmp_path / "artifacts"
    session_dir = artifacts_root / "sess-1"
    session_dir.mkdir(parents=True)
    payload = {"id": "a1", "answer": "证明全文"}
    (session_dir / "g1-1-abc.json").write_text(json.dumps(payload), encoding="utf-8")
    artifact_ref = {"id": "a1", "goal_id": "g1", "path": str(session_dir / "g1-1-abc.json")}
    store.write_checkpoint(_checkpoint(research_artifacts=[artifact_ref]))
    ok = client.get("/api/research/runs/sess-1/artifacts/a1")
    assert ok.status_code == 200
    assert ok.json()["artifact"]["answer"] == "证明全文"
    assert client.get("/api/research/runs/sess-1/artifacts/nope").status_code == 404


def test_artifact_endpoint_rejects_path_traversal(monkeypatch, tmp_path):
    store, _ = _wire(monkeypatch, tmp_path)
    artifacts_root = tmp_path / "artifacts"
    session_dir = artifacts_root / "sess-1"
    session_dir.mkdir(parents=True)
    escape_path = tmp_path / "escape.txt"
    escape_path.write_text("secret", encoding="utf-8")
    artifact_ref = {"id": "a1", "goal_id": "g1", "path": "../escape.txt"}
    store.write_checkpoint(_checkpoint(research_artifacts=[artifact_ref]))
    assert client.get("/api/research/runs/sess-1/artifacts/a1").status_code == 404


def test_inbox_lists_pending_only(monkeypatch, tmp_path):
    store, _ = _wire(monkeypatch, tmp_path)
    pending = {
        "request_id": "r1",
        "kind": "budget_extend",
        "question": "延长预算?",
        "allowed_decisions": ["approve", "reject"],
    }
    store.write_checkpoint(_checkpoint(pending_interaction=pending))
    store.write_checkpoint(_checkpoint(session_id="sess-2"))
    response = client.get("/api/inbox")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 1
    assert items[0]["request_id"] == "r1"
    assert items[0]["kind"] == "budget_extend"


def test_share_flow_and_sanitization(monkeypatch, tmp_path):
    store, _ = _wire(monkeypatch, tmp_path)
    store.write_checkpoint(_checkpoint())
    created = client.post("/api/research/runs/sess-1/share")
    assert created.status_code == 200
    token = created.json()["token"]
    shared = client.get(f"/api/share/research/{token}")
    assert shared.status_code == 200
    body = shared.text
    assert "u_secret" not in body
    run = shared.json()["run"]
    assert run["problem"] == "证明 √2 是无理数"
    # creating again returns the same token while the link is active
    assert client.post("/api/research/runs/sess-1/share").json()["token"] == token
    # revoke, then the anonymous read is gone
    assert client.delete(f"/api/share/research/{token}").status_code == 200
    assert client.get(f"/api/share/research/{token}").status_code == 404
    # unknown tokens are 404 on both read and revoke
    assert client.get("/api/share/research/nope").status_code == 404
    assert client.delete("/api/share/research/nope").status_code == 404


def test_removed_research_spa_pages_are_not_served(monkeypatch, tmp_path):
    """The research command deck / inbox / share SPA pages were removed; only
    the JSON API under /api/research remains."""
    _wire(monkeypatch, tmp_path)
    for path in (
        "/app/research",
        "/app/research/sess-1",
        "/app/research/sess-1/",
        "/app/inbox",
        "/share/research/token-1",
    ):
        assert client.get(path).status_code == 404, path
