from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from math_agent.web import app as web_app


def test_healthz_is_public_and_contains_no_configuration(monkeypatch):
    monkeypatch.setenv("CONJECTA_AUTH_TOKEN", "do-not-expose-this")
    response = TestClient(web_app.app).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "status": "healthy"}
    assert "do-not-expose-this" not in response.text


def test_homepage_stylesheet_is_served():
    client = TestClient(web_app.app)

    homepage = client.get("/")
    stylesheet = client.get("/styles.css")

    assert homepage.status_code == 200
    assert 'href="styles.css?v=20260723-nofootermail"' in homepage.text
    assert stylesheet.status_code == 200
    assert ".home-hero" in stylesheet.text


def test_openapi_docs_are_disabled_by_default_and_explicitly_opt_in(monkeypatch):
    monkeypatch.delenv("CONJECTA_ENABLE_DOCS", raising=False)
    assert web_app._fastapi_docs_kwargs() == {
        "docs_url": None,
        "redoc_url": None,
        "openapi_url": None,
    }
    assert web_app.app.docs_url is None
    assert web_app.app.redoc_url is None
    assert web_app.app.openapi_url is None

    monkeypatch.setenv("CONJECTA_ENABLE_DOCS", "1")
    assert web_app._fastapi_docs_kwargs() == {
        "docs_url": "/docs",
        "redoc_url": "/redoc",
        "openapi_url": "/openapi.json",
    }


@pytest.mark.asyncio
async def test_lifespan_fails_closed_before_starting_with_weak_jwt(monkeypatch):
    monkeypatch.setenv("CONJECTA_JWT_SECRET", "weak-secret")

    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        async with web_app.lifespan(web_app.app):
            pass
