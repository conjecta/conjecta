"""Tests for solve feedback store, validation helpers, and HTTP routes."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from math_agent.web import admin_routes
from math_agent.web.app import app
from math_agent.web.feedback import (
    CLOUD_STORAGE_REQUIRED,
    FeedbackStore,
    enrich_feedback_rows,
    normalize_feedback_payload,
)
from math_agent.web.security import LOCAL_DEV_USER_ID


@pytest.fixture
def client():
    return TestClient(app)


class FakeUniqueViolation(Exception):
    code = "23505"


class FakeSupabaseClient:
    def __init__(self):
        self.tables: dict[str, list] = {}

    def table(self, name):
        return FakeTable(self.tables.setdefault(name, []))


class FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self._filters = []
        self._insert = None
        self._update = None
        self._limit = None
        self._order = None
        self._desc = False
        self._neq = []

    def select(self, *_a):
        return self

    def insert(self, row):
        self._insert = row
        return self

    def update(self, updates):
        self._update = updates
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, col, desc=False):
        self._order = col
        self._desc = desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def execute(self):
        if self._insert is not None:
            row = dict(self._insert)
            session_id = row.get("session_id")
            if session_id is not None:
                for existing in self.rows:
                    if (
                        existing.get("user_id") == row.get("user_id")
                        and existing.get("session_id") == session_id
                    ):
                        raise FakeUniqueViolation(
                            "duplicate key value violates unique constraint"
                        )
            if "id" not in row:
                row["id"] = f"fb-{len(self.rows)+1}"
            self.rows.append(row)
            return FakeResponse([row])
        matched = [r for r in self.rows if all(r.get(c) == v for c, v in self._filters)]
        if self._update is not None:
            for r in matched:
                r.update(self._update)
            return FakeResponse(matched)
        if self._order:
            matched = sorted(matched, key=lambda r: r.get(self._order) or "", reverse=self._desc)
        if self._limit is not None:
            matched = matched[: self._limit]
        return FakeResponse(matched)


class FakeResponse:
    def __init__(self, data):
        self.data = data


def test_normalize_rejects_bad_rating():
    with pytest.raises(HTTPException) as exc:
        normalize_feedback_payload({"rating": "meh", "outcome": "completed"})
    assert exc.value.status_code == 400


def test_normalize_rejects_wrong_case_enums():
    with pytest.raises(HTTPException) as exc:
        normalize_feedback_payload({"rating": "SATISFIED", "outcome": "Completed"})
    assert exc.value.status_code == 400


def test_upsert_insert_then_update_same_session():
    client = FakeSupabaseClient()
    store = FeedbackStore(client=client)
    first = store.upsert(
        "u_a",
        rating="satisfied",
        outcome="completed",
        comment="good",
        session_id="sess-1",
        problem_preview="2+2",
    )
    assert first["rating"] == "satisfied"
    second = store.upsert(
        "u_a",
        rating="unsatisfied",
        outcome="completed",
        comment="changed",
        session_id="sess-1",
        problem_preview="2+2",
    )
    assert second["rating"] == "unsatisfied"
    assert second["comment"] == "changed"
    assert len(client.tables["conjecta_solve_feedback"]) == 1


def test_list_feedback_rejects_wrong_case_rating():
    store = FeedbackStore(client=FakeSupabaseClient())
    with pytest.raises(HTTPException) as exc:
        store.list_feedback(rating="SATISFIED")
    assert exc.value.status_code == 400


def test_enrich_feedback_rows_adds_user_label():
    rows = [{"user_id": "u_a", "rating": "satisfied", "comment": "", "outcome": "completed"}]
    profiles = {"u_a": {"display_name": "Ada", "phone_masked": "138****0000"}}
    out = enrich_feedback_rows(rows, profiles)
    assert out[0]["label"] == "Ada"
    assert out[0]["phone_masked"] == "138****0000"


def test_list_feedback_filters_rating():
    client = FakeSupabaseClient()
    store = FeedbackStore(client=client)
    store.upsert("u_a", rating="satisfied", outcome="completed", session_id="s1")
    store.upsert("u_b", rating="unsatisfied", outcome="failed", session_id="s2")
    rows = store.list_feedback(limit=50, rating="unsatisfied")
    assert len(rows) == 1
    assert rows[0]["user_id"] == "u_b"


def test_post_feedback_unauthenticated_returns_401(client, monkeypatch):
    monkeypatch.delenv("CONJECTA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_APP_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.setenv(
        "CONJECTA_JWT_SECRET", "test-jwt-secret-must-be-at-least-32-bytes"
    )

    resp = client.post(
        "/api/feedback",
        json={"rating": "satisfied", "outcome": "completed"},
    )
    assert resp.status_code in (401, 403)


def test_admin_list_feedback_non_admin_returns_403(client, monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    resp = client.get("/api/admin/feedback")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Administrator access required."


def _auth_as_admin(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("CONJECTA_ADMIN_PHONES", "13800000002")
    monkeypatch.setattr(
        "math_agent.web.security.require_auth_user",
        lambda _request: SimpleNamespace(user_id="admin", phone="13800000002"),
    )


def test_post_feedback_cloud_storage_required(client, monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setattr(admin_routes, "feedback_store_or_none", lambda: None)

    resp = client.post(
        "/api/feedback",
        json={"rating": "satisfied", "outcome": "completed"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == CLOUD_STORAGE_REQUIRED


def test_admin_list_feedback_cloud_storage_required(client, monkeypatch):
    _auth_as_admin(monkeypatch)
    monkeypatch.setattr(admin_routes, "feedback_store_or_none", lambda: None)

    resp = client.get("/api/admin/feedback")
    assert resp.status_code == 503
    assert resp.json()["detail"] == CLOUD_STORAGE_REQUIRED


def test_post_feedback_happy_path_calls_store_upsert(client, monkeypatch):
    calls: list[tuple[str, dict]] = []

    class FakeStore:
        def upsert(self, user_id, **data):
            calls.append((user_id, data))
            return {"id": "fb-1", "user_id": user_id, **data}

    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setattr(admin_routes, "feedback_store_or_none", lambda: FakeStore())

    resp = client.post(
        "/api/feedback",
        json={
            "rating": "satisfied",
            "outcome": "completed",
            "comment": "nice",
            "session_id": "sess-1",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["feedback"]["rating"] == "satisfied"
    assert len(calls) == 1
    assert calls[0][0] == LOCAL_DEV_USER_ID
    assert calls[0][1]["session_id"] == "sess-1"


def test_admin_list_feedback_returns_enriched_rows(client, monkeypatch):
    rows = [
        {
            "user_id": "u_a",
            "rating": "satisfied",
            "comment": "",
            "outcome": "completed",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    ]

    class FakeFeedbackStore:
        def list_feedback(self, *, limit=100, rating=None):
            return list(rows)

    class FakeUserStore:
        def get_profile(self, uid):
            if uid == "u_a":
                return {"display_name": "Ada", "phone_masked": "138****0000"}
            return {}

    _auth_as_admin(monkeypatch)
    monkeypatch.setattr(admin_routes, "feedback_store_or_none", lambda: FakeFeedbackStore())
    monkeypatch.setattr(admin_routes, "UserStore", FakeUserStore)
    monkeypatch.setattr(admin_routes, "service_role_configured", lambda: True)

    resp = client.get("/api/admin/feedback")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert len(body["feedback"]) == 1
    assert body["feedback"][0]["label"] == "Ada"
    assert body["feedback"][0]["phone_masked"] == "138****0000"
