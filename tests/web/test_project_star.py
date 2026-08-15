from fastapi.testclient import TestClient

from math_agent.web import app as web_app
from math_agent.web.project_store import ProjectStore


def test_star_toggles_and_persists(tmp_path):
    store = ProjectStore(root=tmp_path)
    store.add_turn("p1", {"problem": "q", "answer": "a", "attachments": []})
    store.set_starred("p1", True)
    assert store.get_project("p1")["starred"] is True
    store.set_starred("p1", False)
    assert store.get_project("p1")["starred"] is False


def test_star_defaults_false(tmp_path):
    store = ProjectStore(root=tmp_path)
    store.add_turn("p1", {"problem": "q", "answer": "a", "attachments": []})
    assert store.get_project("p1")["starred"] is False


def test_star_persists_across_store_reload(tmp_path):
    store = ProjectStore(root=tmp_path)
    store.add_turn("p1", {"problem": "q", "answer": "a", "attachments": []})
    store.set_starred("p1", True)

    reloaded = ProjectStore(root=tmp_path)
    assert reloaded.get_project("p1")["starred"] is True


def test_star_reflected_in_list_projects(tmp_path):
    store = ProjectStore(root=tmp_path)
    store.add_turn("p1", {"problem": "q", "answer": "a", "attachments": []})
    store.add_turn("p2", {"problem": "q2", "answer": "a2", "attachments": []})
    store.set_starred("p2", True)

    projects = {p["id"]: p for p in store.list_projects()}
    assert projects["p1"]["starred"] is False
    assert projects["p2"]["starred"] is True


def test_star_endpoint_toggles_and_returns_project(monkeypatch, tmp_path):
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    from math_agent.web.security import LOCAL_DEV_USER_ID
    from math_agent.web.project_store import project_store_for_user

    store = project_store_for_user(LOCAL_DEV_USER_ID)
    store.add_turn("p1", {"problem": "q", "answer": "a", "attachments": []})

    client = TestClient(web_app.app)
    resp = client.post("/api/projects/p1/star", json={"starred": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["starred"] is True

    resp2 = client.post("/api/projects/p1/star", json={"starred": False})
    assert resp2.json()["starred"] is False

