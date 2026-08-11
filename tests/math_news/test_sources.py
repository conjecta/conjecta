from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from math_agent.math_news.sources import fetch_arxiv_news, fetch_quanta_news


@pytest.mark.asyncio
async def test_fetch_quanta_parses_rss(monkeypatch):
    rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<item>
<title>  A New Proof  </title>
<link>https://www.quantamagazine.org/a-new-proof-20260714/</link>
<description>A surprising result in number theory.</description>
<pubDate>Mon, 14 Jul 2026 00:00:00 GMT</pubDate>
</item>
</channel>
</rss>"""

    async def fake_fetch(url, *, timeout_seconds, max_bytes, headers=None):
        return SimpleNamespace(text=rss, url=url, headers={"content-type": "application/rss+xml"})

    monkeypatch.setattr("math_agent.math_news.sources.fetch_public_url", fake_fetch)
    items = await fetch_quanta_news()
    assert len(items) == 1
    assert items[0].title == "A New Proof"
    assert items[0].url == "https://www.quantamagazine.org/a-new-proof-20260714/"
    assert items[0].published_at == datetime(2026, 7, 14, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_fetch_arxiv_parses_atom(monkeypatch):
    atom = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
<title>A proof of the Riemann hypothesis\n </title>
<id>http://arxiv.org/abs/2607.00001</id>
<summary>We prove the Riemann hypothesis.</summary>
<published>2026-07-14T00:00:00Z</published>
</entry>
</feed>"""

    async def fake_fetch(url, *, timeout_seconds, max_bytes, headers=None):
        return SimpleNamespace(text=atom, url=url, headers={"content-type": "application/atom+xml"})

    monkeypatch.setattr("math_agent.math_news.sources.fetch_public_url", fake_fetch)
    items = await fetch_arxiv_news()
    assert len(items) == 1
    assert items[0].title == "A proof of the Riemann hypothesis"
    assert items[0].url == "http://arxiv.org/abs/2607.00001"
    assert items[0].published_at == datetime(2026, 7, 14, tzinfo=timezone.utc)
