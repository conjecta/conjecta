from fastapi.testclient import TestClient

from math_agent.web.app import app

client = TestClient(app)


def test_version_returns_deployment_identifier(monkeypatch):
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setattr("math_agent.web.pages_routes.DEPLOYMENT_VERSION", "deploy-abc-123")

    resp = client.get("/api/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == "deploy-abc-123"
