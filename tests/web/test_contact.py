"""Tests for public contact-support form."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from math_agent.web import admin_routes
from math_agent.web import app as web_app
from math_agent.web.contact import ContactStore, normalize_contact_payload


def test_normalize_contact_payload_requires_fields():
    with pytest.raises(Exception):
        normalize_contact_payload({"name": "", "email": "a@b.com", "message": "hi"})
    with pytest.raises(Exception):
        normalize_contact_payload({"name": "Ada", "email": "bad", "message": "hi"})
    row = normalize_contact_payload(
        {"name": " Ada ", "email": "Ada@Example.COM", "message": " Hello "}
    )
    assert row == {
        "name": "Ada",
        "email": "ada@example.com",
        "message": "Hello",
    }


def test_contact_store_appends_jsonl(tmp_path: Path):
    store = ContactStore(tmp_path / "contact_messages.jsonl")
    row = store.add({"name": "Ada", "email": "ada@example.com", "message": "Help"})
    assert row["id"]
    lines = (tmp_path / "contact_messages.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["email"] == "ada@example.com"


def test_post_contact_public_endpoint(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        admin_routes,
        "load_config",
        lambda: SimpleNamespace(logging=SimpleNamespace(dir=str(tmp_path), enabled=False, level="INFO")),
    )
    client = TestClient(web_app.app)
    resp = client.post(
        "/api/contact",
        json={"name": "Ada", "email": "ada@example.com", "message": "Need help"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["id"]
    path = tmp_path / "contact_messages.jsonl"
    assert path.exists()
    saved = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert saved["message"] == "Need help"
