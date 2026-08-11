from fastapi.testclient import TestClient

from math_agent.web import app as web_app
from math_agent.web.project_store import project_store_for_user
from math_agent.web.security import LOCAL_DEV_USER_ID


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    web_app._PROJECT_STORE_CACHE.clear()
    return TestClient(web_app.app), project_store_for_user(LOCAL_DEV_USER_ID)


def test_patch_and_delete_knowledge_item(monkeypatch, tmp_path):
    client, store = _client(monkeypatch, tmp_path)
    fact = store.add_fact("default", "sqrt(2) is irrational", why="classic proof")
    intuition = store.add_intuition("default", "Try contradiction", "Assume rational.")
    trick = store.add_trick("default", "Clear denominators", "Multiply through.")

    patched = client.patch(
        f"/api/knowledge/fact/{fact['id']}",
        params={"project_id": "default"},
        json={"statement": "sqrt(2) is irrational over Q", "why": "updated"},
    )
    assert patched.status_code == 200
    assert patched.json()["item"]["statement"] == "sqrt(2) is irrational over Q"
    assert patched.json()["item"]["why"] == "updated"

    patched_intuition = client.patch(
        f"/api/knowledge/intuition/{intuition['id']}",
        params={"project_id": "default"},
        json={"title": "Contradiction first", "body": "Assume a/b."},
    )
    assert patched_intuition.status_code == 200
    assert patched_intuition.json()["item"]["title"] == "Contradiction first"

    deleted = client.delete(
        f"/api/knowledge/trick/{trick['id']}",
        params={"project_id": "default"},
    )
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert store.list_tricks("default") == []


def test_knowledge_mutation_validates_kind_and_existence(monkeypatch, tmp_path):
    client, store = _client(monkeypatch, tmp_path)
    fact = store.add_fact("default", "pi is transcendental")

    bad_kind = client.patch(
        f"/api/knowledge/material/{fact['id']}",
        params={"project_id": "default"},
        json={"statement": "x"},
    )
    assert bad_kind.status_code == 400

    missing = client.delete(
        "/api/knowledge/fact/missing-id",
        params={"project_id": "default"},
    )
    assert missing.status_code == 404

    empty = client.patch(
        f"/api/knowledge/fact/{fact['id']}",
        params={"project_id": "default"},
        json={"confidence": 0.9},
    )
    assert empty.status_code == 400
