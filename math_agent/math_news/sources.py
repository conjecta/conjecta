from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import feedparser

from math_agent.net_safety import fetch_public_url

log = logging.getLogger("math_agent.math_news.sources")

USER_AGENT = "ConjectaBot/0.1 (+https://conjecta.ai)"
QUANTA_FEED_URL = "https://www.quantamagazine.org/feed/"
ARXIV_API_URL = (
    "https://export.arxiv.org/api/query?"
    "search_query=cat:math.*&sortBy=submittedDate&sortOrder=descending&max_results=10"
)


@dataclass(frozen=True)
class RawNewsItem:
    source: str
    title: str
    summary: str
    url: str
    published_at: datetime


def _parse_date(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, tuple):
        # feedparser's published_parsed
        try:
            return datetime(*value[:6], tzinfo=timezone.utc)
        except Exception:
            pass
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            pass
    return datetime.now(timezone.utc)


def _clean_title(title: str) -> str:
    return " ".join(title.split())


async def fetch_quanta_news() -> list[RawNewsItem]:
    try:
        response = await fetch_public_url(
            QUANTA_FEED_URL,
            timeout_seconds=30.0,
            max_bytes=2 * 1024 * 1024,
            headers={"User-Agent": USER_AGENT},
        )
        parsed = feedparser.parse(response.text)
        items: list[RawNewsItem] = []
        for entry in parsed.entries:
            title = _clean_title(entry.get("title", ""))
            link = entry.get("link", "").strip()
            summary = entry.get("description", "").strip()
            if not title or not link:
                continue
            published = _parse_date(entry.get("published_parsed") or entry.get("updated_parsed") or entry.get("published"))
            items.append(RawNewsItem(source="quanta", title=title, summary=summary, url=link, published_at=published))
        return items
    except Exception as exc:
        log.warning("Quanta fetch failed: %s", exc)
        return []


async def fetch_arxiv_news() -> list[RawNewsItem]:
    try:
        response = await fetch_public_url(
            ARXIV_API_URL,
            timeout_seconds=30.0,
            max_bytes=2 * 1024 * 1024,
            headers={"User-Agent": USER_AGENT},
        )
        parsed = feedparser.parse(response.text)
        items: list[RawNewsItem] = []
        for entry in parsed.entries:
            title = _clean_title(entry.get("title", ""))
            link = entry.get("id", "").strip()
            summary = entry.get("summary", "").strip()
            if not title or not link:
                continue
            published = _parse_date(entry.get("published") or entry.get("updated"))
            items.append(RawNewsItem(source="arxiv", title=title, summary=summary, url=link, published_at=published))
        return items
    except Exception as exc:
        log.warning("arXiv fetch failed: %s", exc)
        return []
