from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from math_agent.web import app as web_app
from math_agent.web.jwt_auth import issue_access_token, user_id_for_phone
from math_agent.web.project_store import project_store_for_user, project_store_root_for_user


def _config_for(tmp_path):
    return SimpleNamespace(
        logging=SimpleNamespace(dir=str(tmp_path), enabled=False, level="INFO"),
        lean=SimpleNamespace(enabled=False, mathlib_dep=False),
    )


@pytest.fixture
def tenant_client(monkeypatch, tmp_path):
    monkeypatch.delenv("CONJECTA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_APP_TOKEN", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    monkeypatch.setenv(
        "CONJECTA_JWT_SECRET", "test-jwt-secret-must-be-at-least-32-bytes"
    )
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path / "projects"))
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "0")
    monkeypatch.setattr(web_app, "load_config", lambda: _config_for(tmp_path))
    return TestClient(web_app.app)


def _auth_header(phone: str) -> dict[str, str]:
    token, _, _ = issue_access_token(phone)
    return {"Authorization": f"Bearer {token}"}


def test_project_store_root_is_per_user(tmp_path, monkeypatch):
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
    root_a = project_store_root_for_user("u_aaa")
    root_b = project_store_root_for_user("u_bbb")
    assert root_a != root_b
    assert root_a.name == "u_aaa"
    store = project_store_for_user("u_aaa")
    store.save_project("demo", {"id": "demo", "name": "Demo"})
    assert (root_a / "events.jsonl").exists()
    assert not (root_b / "events.jsonl").exists()


def test_sanitized_user_roots_do_not_collapse_distinct_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))

    slash = project_store_root_for_user("tenant/a")
    question = project_store_root_for_user("tenant?a")

    assert slash != question
    assert slash.parent == question.parent == tmp_path.resolve()


def test_users_cannot_see_each_others_local_projects(tenant_client, tmp_path, monkeypatch):
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path / "projects"))
    phone_a = "13812345678"
    phone_b = "13900001111"
    ha = _auth_header(phone_a)
    hb = _auth_header(phone_b)

    save = tenant_client.put(
        "/api/projects/secret-a",
        json={"id": "secret-a", "name": "A only"},
        headers=ha,
    )
    assert save.status_code == 200

    listed_b = tenant_client.get("/api/projects", headers=hb)
    assert listed_b.status_code == 200
    ids_b = {p["id"] for p in listed_b.json()["projects"]}
    assert "secret-a" not in ids_b

    get_b = tenant_client.get("/api/projects/secret-a", headers=hb)
    assert get_b.status_code == 404

    listed_a = tenant_client.get("/api/projects", headers=ha)
    assert listed_a.status_code == 200
    ids_a = {p["id"] for p in listed_a.json()["projects"]}
    assert "secret-a" in ids_a
    assert user_id_for_phone(phone_a) != user_id_for_phone(phone_b)


def test_projects_require_auth_when_jwt_enabled(tenant_client):
    resp = tenant_client.get("/api/projects")
    assert resp.status_code == 401
