from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from math_agent.web import agent_factory
from math_agent.web.app import app
from math_agent.math_news.store import MathNewsItem, MathNewsStore


def test_math_news_requires_auth():
    # Default middleware rejects unauthenticated API calls.
    client = TestClient(app)
    resp = client.get("/api/math-news")
    assert resp.status_code in (401, 403)


def test_math_news_returns_items_when_authenticated(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    store = MathNewsStore(path=tmp_path / "news.jsonl")
    store.save([
        MathNewsItem(
            id="abc",
            source="quanta",
            title_zh="中文标题",
            summary_zh="中文摘要。",
            url="https://example.com",
            published_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 7, 15, 6, tzinfo=timezone.utc),
        )
    ])
    monkeypatch.setattr(agent_factory, "math_news_store", store)

    client = TestClient(app)
    resp = client.get("/api/math-news")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["title_zh"] == "中文标题"
    assert body["updated_at"] == "2026-07-15T06:00:00+00:00"
