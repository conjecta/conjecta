from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


from math_agent.math_news.store import MathNewsItem, MathNewsStore


def test_store_loads_empty_when_missing(tmp_path: Path):
    store = MathNewsStore(path=tmp_path / "news.jsonl")
    assert store.load() == []
    assert store.updated_at() is None


def test_store_round_trip(tmp_path: Path):
    store = MathNewsStore(path=tmp_path / "news.jsonl")
    items = [
        MathNewsItem(
            id="abc123",
            source="quanta",
            title_zh="标题",
            summary_zh="摘要。",
            url="https://example.com/a",
            published_at=datetime(2026, 7, 14, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 7, 15, 6, tzinfo=timezone.utc),
        )
    ]
    store.save(items)
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].id == "abc123"
    assert loaded[0].title_zh == "标题"
    assert store.updated_at() == datetime(2026, 7, 15, 6, tzinfo=timezone.utc)


def test_store_atomic_write(tmp_path: Path):
    store = MathNewsStore(path=tmp_path / "news.jsonl")
    store.save([MathNewsItem(id="x", source="arxiv", title_zh="t", summary_zh="s", url="https://x", published_at=datetime.now(timezone.utc), fetched_at=datetime.now(timezone.utc))])
    # Original file should exist; temp file should not remain.
    assert (tmp_path / "news.jsonl").exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_store_seed_fallback(tmp_path: Path):
    seed_path = tmp_path / "seed.jsonl"
    seed = [
        MathNewsItem(
            id="seed1",
            source="quanta",
            title_zh="种子标题",
            summary_zh="种子摘要。",
            url="https://seed.example",
            published_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            fetched_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
        )
    ]
    seed_path.write_text(json.dumps(seed[0].to_dict(), ensure_ascii=False) + "\n", encoding="utf-8")
    store = MathNewsStore(path=tmp_path / "news.jsonl", seed_path=seed_path)
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].id == "seed1"


def test_store_save_overrides_seed(tmp_path: Path):
    seed_path = tmp_path / "seed.jsonl"
    seed_path.write_text(json.dumps({"id": "seed1", "source": "quanta", "title_zh": "种子", "summary_zh": "摘要", "url": "https://seed", "published_at": "2026-07-01T00:00:00+00:00", "fetched_at": "2026-07-01T00:00:00+00:00"}, ensure_ascii=False) + "\n", encoding="utf-8")
    store = MathNewsStore(path=tmp_path / "news.jsonl", seed_path=seed_path)
    store.save([MathNewsItem(id="new1", source="arxiv", title_zh="新", summary_zh="新摘要", url="https://new", published_at=datetime.now(timezone.utc), fetched_at=datetime.now(timezone.utc))])
    assert store.load()[0].id == "new1"
