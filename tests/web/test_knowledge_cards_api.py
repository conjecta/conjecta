import pytest
from fastapi.testclient import TestClient

from math_agent.web.app import app
from math_agent.web.project_store import project_store_for_user
from math_agent.web.security import LOCAL_DEV_USER_ID


@pytest.fixture
def client():
    return TestClient(app)


def test_public_gallery_returns_ok(client, monkeypatch):
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    resp = client.get("/api/knowledge-cards/public")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert isinstance(body["cards"], list)


def test_list_my_knowledge_cards_requires_auth(client, monkeypatch):
    """GET /api/knowledge-cards must not be reachable without authentication."""
    monkeypatch.delenv("CONJECTA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_APP_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.setenv(
        "CONJECTA_JWT_SECRET", "test-jwt-secret-must-be-at-least-32-bytes"
    )

    resp = client.get("/api/knowledge-cards")
    assert resp.status_code in (401, 403)


def test_get_public_knowledge_card_without_auth(client, monkeypatch, tmp_path):
    """GET /api/knowledge-cards/{card_id} should read a public card without auth."""
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))

    store = project_store_for_user("anonymous")
    store.save_project("proj-public", {"name": "Public source"})
    fact = store.add_fact("proj-public", "2 + 2 = 4", "Arithmetic", "Math")
    from math_agent.web.knowledge_cards import KnowledgeCardService

    svc = KnowledgeCardService(user_id="anonymous", project_store=store)
    result = svc.publish_from_project_item(
        "proj-public", fact["id"], "fact", {"title": "Basic arithmetic", "visibility": "public"}
    )
    card_id = result["card"]["id"]

    resp = client.get(f"/api/knowledge-cards/{card_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["card"]["card"]["id"] == card_id


def _unauth_client(monkeypatch, tmp_path):
    """Return a TestClient configured for unauthenticated local-dev access."""
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("CONJECTA_PROJECT_STORE_DIR", str(tmp_path))
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    from math_agent.web.app import app
    return TestClient(app)


def test_import_missing_card_returns_404(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    store = project_store_for_user(LOCAL_DEV_USER_ID)
    store.save_project("proj-target", {"name": "Target"})
    resp = client.post(
        "/api/knowledge-cards/nonexistent-card/import",
        json={"target_project_id": "proj-target"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Card not found"


def test_import_missing_target_project_returns_404(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    store = project_store_for_user(LOCAL_DEV_USER_ID)
    store.save_project("proj-source", {"name": "Source"})
    fact = store.add_fact("proj-source", "2 + 2 = 4", "Arithmetic", "Math")
    from math_agent.web.knowledge_cards import KnowledgeCardService
    svc = KnowledgeCardService(user_id=LOCAL_DEV_USER_ID, project_store=store)
    card = svc.publish_from_project_item("proj-source", fact["id"], "fact", {"title": "Basic arithmetic"})

    resp = client.post(
        f"/api/knowledge-cards/{card['card']['id']}/import",
        json={"target_project_id": "missing-project"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Target project not found"


def test_export_unsupported_format_returns_400(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    store = project_store_for_user("anonymous")
    store.save_project("proj-source", {"name": "Source"})
    fact = store.add_fact("proj-source", "2 + 2 = 4", "Arithmetic", "Math")
    from math_agent.web.knowledge_cards import KnowledgeCardService
    svc = KnowledgeCardService(user_id="anonymous", project_store=store)
    card = svc.publish_from_project_item(
        "proj-source", fact["id"], "fact",
        {"title": "Basic arithmetic", "visibility": "public"},
    )

    resp = client.get(f"/api/knowledge-cards/{card['card']['id']}/export/docx")
    assert resp.status_code == 400
    assert "Unsupported export format" in resp.json()["detail"]


def test_export_missing_card_returns_404(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    resp = client.get("/api/knowledge-cards/missing-card/export/markdown")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Card not found"


def test_react_to_card_returns_501(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    resp = client.post(
        "/api/knowledge-cards/some-card/reactions",
        json={"kind": "star"},
    )
    assert resp.status_code == 501


def test_list_comments_returns_501(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    resp = client.get("/api/knowledge-cards/some-card/comments")
    assert resp.status_code == 501


def test_add_comment_returns_501(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    resp = client.post(
        "/api/knowledge-cards/some-card/comments",
        json={"body": "Nice card!"},
    )
    assert resp.status_code == 501



def test_create_revision(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    store = project_store_for_user(LOCAL_DEV_USER_ID)
    store.save_project("proj-source", {"name": "Source"})
    fact = store.add_fact("proj-source", "2 + 2 = 4", "Arithmetic", "Math")
    from math_agent.web.knowledge_cards import KnowledgeCardService

    svc = KnowledgeCardService(user_id=LOCAL_DEV_USER_ID, project_store=store)
    card = svc.publish_from_project_item(
        "proj-source", fact["id"], "fact",
        {"title": "Basic arithmetic", "visibility": "private"},
    )
    card_id = card["card"]["id"]
    resp = client.post(
        f"/api/knowledge-cards/{card_id}/revisions",
        json={"title": "Updated title", "body": "Updated body"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["revision"]["revision_number"] == 2
    assert body["card"]["latest_revision_id"] == body["revision"]["id"]


def test_create_revision_missing_card_returns_404(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    resp = client.post(
        "/api/knowledge-cards/missing-card/revisions",
        json={"title": "Updated title"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Card not found"


def test_create_revision_non_owner_returns_403(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    store = project_store_for_user(LOCAL_DEV_USER_ID)
    store.save_project("proj-source", {"name": "Source"})
    fact = store.add_fact("proj-source", "2 + 2 = 4", "Arithmetic", "Math")
    from math_agent.web.knowledge_cards import KnowledgeCardService

    svc = KnowledgeCardService(user_id="other-user", project_store=store)
    card = svc.publish_from_project_item(
        "proj-source", fact["id"], "fact",
        {"title": "Basic arithmetic", "visibility": "public"},
    )
    card_id = card["card"]["id"]
    resp = client.post(
        f"/api/knowledge-cards/{card_id}/revisions",
        json={"title": "Hacked"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Not authorized to edit this card"


def test_publish_card(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    store = project_store_for_user(LOCAL_DEV_USER_ID)
    store.save_project("proj-source", {"name": "Source"})
    fact = store.add_fact("proj-source", "2 + 2 = 4", "Arithmetic", "Math")
    from math_agent.web.knowledge_cards import KnowledgeCardService

    svc = KnowledgeCardService(user_id=LOCAL_DEV_USER_ID, project_store=store)
    card = svc.publish_from_project_item(
        "proj-source", fact["id"], "fact",
        {"title": "Basic arithmetic", "visibility": "private"},
    )
    card_id = card["card"]["id"]
    resp = client.post(
        f"/api/knowledge-cards/{card_id}/publish",
        json={"visibility": "public"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["card"]["visibility"] == "public"
    assert body["card"]["status"] == "published"


def test_publish_card_invalid_visibility_returns_400(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    store = project_store_for_user(LOCAL_DEV_USER_ID)
    store.save_project("proj-source", {"name": "Source"})
    fact = store.add_fact("proj-source", "2 + 2 = 4", "Arithmetic", "Math")
    from math_agent.web.knowledge_cards import KnowledgeCardService

    svc = KnowledgeCardService(user_id=LOCAL_DEV_USER_ID, project_store=store)
    card = svc.publish_from_project_item(
        "proj-source", fact["id"], "fact",
        {"title": "Basic arithmetic", "visibility": "private"},
    )
    resp = client.post(
        f"/api/knowledge-cards/{card['card']['id']}/publish",
        json={"visibility": "unlisted"},
    )
    assert resp.status_code == 400
    assert "Invalid visibility" in resp.json()["detail"]


def test_publish_card_missing_card_returns_404(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    resp = client.post(
        "/api/knowledge-cards/missing-card/publish",
        json={"visibility": "public"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Card not found"


def test_publish_card_non_owner_returns_403(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    store = project_store_for_user(LOCAL_DEV_USER_ID)
    store.save_project("proj-source", {"name": "Source"})
    fact = store.add_fact("proj-source", "2 + 2 = 4", "Arithmetic", "Math")
    from math_agent.web.knowledge_cards import KnowledgeCardService

    svc = KnowledgeCardService(user_id="other-user", project_store=store)
    card = svc.publish_from_project_item(
        "proj-source", fact["id"], "fact",
        {"title": "Basic arithmetic", "visibility": "public"},
    )
    resp = client.post(
        f"/api/knowledge-cards/{card['card']['id']}/publish",
        json={"visibility": "private"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Not authorized to publish this card"


def test_publish_card_from_turn_local(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    store = project_store_for_user(LOCAL_DEV_USER_ID)
    store.save_project("proj-turn", {"name": "Turn source"})
    turn = store.add_turn(
        "proj-turn",
        {
            "problem": "Prove sqrt(2) is irrational",
            "answer": "Suppose not, then sqrt(2) = p/q ...",
            "attachments": [],
            "verification_status": "verified",
            "lean_proofs": ["theorem sqrt_two_irrational : True := trivial"],
        },
    )

    resp = client.post(
        f"/api/projects/proj-turn/turns/{turn['id']}/publish-card",
        json={"visibility": "public"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["card"]["source_item_kind"] == "turn"
    assert body["card"]["source_item_id"] == turn["id"]
    assert body["card"]["visibility"] == "public"
    assert body["revision"]["title"] == "Prove sqrt(2) is irrational"
    assert body["revision"]["statement"] == "Suppose not, then sqrt(2) = p/q ..."
    assert body["revision"]["formal_status"] == "verified"
    assert body["revision"]["lean_code"] == "theorem sqrt_two_irrational : True := trivial"


def test_publish_card_from_turn_payload_overrides(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    store = project_store_for_user(LOCAL_DEV_USER_ID)
    store.save_project("proj-turn", {"name": "Turn source"})
    turn = store.add_turn(
        "proj-turn",
        {"problem": "q", "answer": "a", "attachments": []},
    )

    resp = client.post(
        f"/api/projects/proj-turn/turns/{turn['id']}/publish-card",
        json={"title": "Custom title", "statement": "Custom statement", "body": "Notes"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["revision"]["title"] == "Custom title"
    assert body["revision"]["statement"] == "Custom statement"
    assert body["revision"]["body"] == "Notes"
    assert body["revision"]["formal_status"] == ""
    assert body["revision"]["lean_code"] == ""


def test_publish_card_from_missing_turn_returns_404(client, monkeypatch, tmp_path):
    client = _unauth_client(monkeypatch, tmp_path)
    store = project_store_for_user(LOCAL_DEV_USER_ID)
    store.save_project("proj-turn", {"name": "Turn source"})

    resp = client.post(
        "/api/projects/proj-turn/turns/missing-turn/publish-card",
        json={"visibility": "public"},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Source turn not found"
