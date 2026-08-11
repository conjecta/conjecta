from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from math_agent.web import app as web_app
from math_agent.web.jwt_auth import issue_access_token, user_id_for_phone
from math_agent.web.user_memory_store import MemoryStatus, UserMemoryStore


def _auth_header(phone: str) -> dict[str, str]:
    token, _, _ = issue_access_token(phone)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def memory_client(monkeypatch, tmp_path):
    monkeypatch.delenv("CONJECTA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_APP_TOKEN", raising=False)
    monkeypatch.setenv(
        "CONJECTA_JWT_SECRET", "test-jwt-secret-must-be-at-least-32-bytes"
    )
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "0")
    monkeypatch.setenv("CONJECTA_USER_MEMORY_DIR", str(tmp_path / "memories"))
    return TestClient(web_app.app), tmp_path / "memories"


def test_user_memory_routes_require_auth(memory_client):
    client, _ = memory_client

    assert client.get("/api/me/memories").status_code == 401


def test_user_can_list_update_and_delete_own_memory(memory_client):
    client, root = memory_client
    phone = "13812345678"
    user_id = user_id_for_phone(phone)
    store = UserMemoryStore(user_id=user_id, root=root)
    memory = store.add(content="用中文回答", why="explicit preference", weight=0.9)
    store.save_profile("prefers concise Chinese answers")
    headers = _auth_header(phone)

    listed = client.get("/api/me/memories", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["memories"][0]["content"] == "用中文回答"
    assert listed.json()["profile"]["summary"] == "prefers concise Chinese answers"

    cleared_profile = client.delete("/api/me/memories/profile", headers=headers)
    assert cleared_profile.status_code == 200
    after_clear = client.get("/api/me/memories", headers=headers)
    assert after_clear.json()["profile"] is None

    updated = client.patch(
        f"/api/me/memories/{memory.id}",
        json={"status": "snoozed"},
        headers=headers,
    )
    assert updated.status_code == 200
    assert updated.json()["memory"]["status"] == "snoozed"

    deleted = client.delete(f"/api/me/memories/{memory.id}", headers=headers)
    assert deleted.status_code == 200
    assert UserMemoryStore(user_id=user_id, root=root).list() == []
    rejected = UserMemoryStore(user_id=user_id, root=root).list_rejected()
    assert rejected[0].status == MemoryStatus.REJECTED


def test_users_cannot_access_each_others_memories(memory_client):
    client, root = memory_client
    phone_a = "13812345678"
    phone_b = "13900001111"
    store_a = UserMemoryStore(user_id=user_id_for_phone(phone_a), root=root)
    memory = store_a.add(content="private preference")

    listed_b = client.get("/api/me/memories", headers=_auth_header(phone_b))
    assert listed_b.status_code == 200
    assert listed_b.json()["memories"] == []

    update_b = client.patch(
        f"/api/me/memories/{memory.id}",
        json={"status": "snoozed"},
        headers=_auth_header(phone_b),
    )
    assert update_b.status_code == 404


def test_memory_patch_validates_status_and_content(memory_client):
    client, root = memory_client
    phone = "13812345678"
    memory = UserMemoryStore(user_id=user_id_for_phone(phone), root=root).add(content="keep")
    headers = _auth_header(phone)

    invalid_status = client.patch(
        f"/api/me/memories/{memory.id}",
        json={"status": "rejected"},
        headers=headers,
    )
    assert invalid_status.status_code == 422

    empty_content = client.patch(
        f"/api/me/memories/{memory.id}",
        json={"content": "   "},
        headers=headers,
    )
    assert empty_content.status_code == 400
