from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from math_agent.config import MathNewsConfig
from math_agent.math_news.refresh import refresh_math_news, select_news
from math_agent.math_news.sources import RawNewsItem
from math_agent.math_news.store import MathNewsItem, MathNewsStore


def raw(source: str, title: str, url: str, days_ago: int) -> RawNewsItem:
    return RawNewsItem(
        source=source,
        title=title,
        summary=f"summary of {title}",
        url=url,
        published_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )


def test_select_news_prefers_two_quanta_three_arxiv():
    quanta = [raw("quanta", f"Q{i}", f"https://quanta.org/q{i}", i) for i in range(3)]
    arxiv = [raw("arxiv", f"A{i}", f"https://arxiv.org/a{i}", i) for i in range(5)]
    selected = select_news(quanta, arxiv, limit=5)
    sources = [item.source for item in selected]
    assert sources.count("quanta") == 2
    assert sources.count("arxiv") == 3


def test_select_news_falls_back_when_quanta_short():
    quanta = [raw("quanta", "Q0", "https://quanta.org/q0", 0)]
    arxiv = [raw("arxiv", f"A{i}", f"https://arxiv.org/a{i}", i) for i in range(5)]
    selected = select_news(quanta, arxiv, limit=5)
    assert len(selected) == 5
    assert selected[0].source == "quanta"


def test_select_news_dedupes_by_url():
    q = raw("quanta", "Dup", "https://x", 0)
    a = raw("arxiv", "Dup", "https://x", 1)
    selected = select_news([q], [a], limit=5)
    assert len(selected) == 1
    assert selected[0].source == "quanta"


@pytest.mark.asyncio
async def test_refresh_keeps_old_store_on_failure(monkeypatch, tmp_path: Path):
    store = MathNewsStore(path=tmp_path / "news.jsonl")
    old = MathNewsItem(
        id="old1",
        source="quanta",
        title_zh="旧",
        summary_zh="旧摘要",
        url="https://old",
        published_at=datetime.now(timezone.utc),
        fetched_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    store.save([old])

    async def failing_fetch():
        raise RuntimeError("boom")

    monkeypatch.setattr("math_agent.math_news.refresh.fetch_quanta_news", failing_fetch)
    monkeypatch.setattr("math_agent.math_news.refresh.fetch_arxiv_news", failing_fetch)

    config = MathNewsConfig(refresh_seconds=3600, min_interval_seconds=0)
    result = await refresh_math_news(store, config)
    assert result == [old]
    assert store.load()[0].id == "old1"


@pytest.mark.asyncio
async def test_refresh_skips_when_recently_fetched(monkeypatch, tmp_path: Path):
    store = MathNewsStore(path=tmp_path / "news.jsonl")
    store.save([
        MathNewsItem(
            id="x", source="quanta", title_zh="t", summary_zh="s", url="https://x",
            published_at=datetime.now(timezone.utc),
            fetched_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
    ])

    calls = []

    async def fake_fetch():
        calls.append(1)
        return []

    monkeypatch.setattr("math_agent.math_news.refresh.fetch_quanta_news", fake_fetch)
    monkeypatch.setattr("math_agent.math_news.refresh.fetch_arxiv_news", fake_fetch)

    config = MathNewsConfig(refresh_seconds=3600, min_interval_seconds=3600)
    await refresh_math_news(store, config)
    assert len(calls) == 0


@pytest.mark.asyncio
async def test_refresh_merges_when_one_source_is_short(monkeypatch, tmp_path: Path):
    """Partial source degradation must not wipe still-valid older items."""
    store = MathNewsStore(path=tmp_path / "news.jsonl")
    now = datetime.now(timezone.utc)
    old_items = [
        MathNewsItem(
            id=f"old-q{i}",
            source="quanta",
            title_zh=f"旧Q{i}",
            summary_zh="旧",
            url=f"https://quanta.org/old{i}",
            published_at=now - timedelta(days=i + 1),
            fetched_at=now - timedelta(hours=2),
        )
        for i in range(2)
    ] + [
        MathNewsItem(
            id=f"old-a{i}",
            source="arxiv",
            title_zh=f"旧A{i}",
            summary_zh="旧",
            url=f"https://arxiv.org/old{i}",
            published_at=now - timedelta(days=i + 3),
            fetched_at=now - timedelta(hours=2),
        )
        for i in range(3)
    ]
    store.save(old_items)

    async def empty_quanta():
        return []

    async def short_arxiv():
        return [
            raw("arxiv", "NewA0", "https://arxiv.org/new0", 0),
            raw("arxiv", "NewA1", "https://arxiv.org/new1", 0),
        ]

    class FakeLLM:
        async def complete(self, messages, system=None, temperature=None):
            from math_agent.billing.models import LLMResponse

            return LLMResponse(
                text='{"title_zh":"新标题","summary_zh":"新摘要。"}',
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
            )

    monkeypatch.setattr("math_agent.math_news.refresh.fetch_quanta_news", empty_quanta)
    monkeypatch.setattr("math_agent.math_news.refresh.fetch_arxiv_news", short_arxiv)

    config = MathNewsConfig(refresh_seconds=3600, min_interval_seconds=0)
    result = await refresh_math_news(store, config, llm=FakeLLM())
    assert len(result) == 7  # 5 old + 2 new
    assert len(store.load()) == 7
    urls = {item.url for item in store.load()}
    assert "https://arxiv.org/new0" in urls
    assert "https://quanta.org/old0" in urls


def test_select_news_scales_to_limit_ten():
    quanta = [raw("quanta", f"Q{i}", f"https://quanta.org/q{i}", i) for i in range(6)]
    arxiv = [raw("arxiv", f"A{i}", f"https://arxiv.org/a{i}", i) for i in range(8)]
    selected = select_news(quanta, arxiv, limit=10)
    sources = [item.source for item in selected]
    assert len(selected) == 10
    assert sources.count("quanta") == 4
    assert sources.count("arxiv") == 6
