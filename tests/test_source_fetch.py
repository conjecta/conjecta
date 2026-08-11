from __future__ import annotations

from types import SimpleNamespace

import pytest

from math_agent.source_fetch import (
    FetchedSource,
    combine_source_text,
    fetch_source_for_url,
)


@pytest.mark.asyncio
async def test_url_fetch_uses_explicit_four_mib_limit_and_caps_extracted_text(monkeypatch):
    calls: list[dict] = []

    async def fake_fetch(url: str, **kwargs):
        calls.append({"url": url, **kwargs})
        return SimpleNamespace(
            url=url,
            headers={"content-type": "text/plain; charset=utf-8"},
            content=("x" * 50_000).encode(),
            text="x" * 50_000,
        )

    monkeypatch.setattr("math_agent.source_fetch.fetch_public_url", fake_fetch)

    source = await fetch_source_for_url("https://example.com/paper.txt")

    assert source is not None
    assert calls == [
        {
            "url": "https://example.com/paper.txt",
            "timeout_seconds": 120.0,
            "max_bytes": 4 * 1024 * 1024,
        }
    ]
    assert len(source.text) <= 40_000


def test_combined_source_text_has_a_hard_40k_character_limit():
    sources = [
        FetchedSource(url="https://example.com/a", label="A", text="a" * 30_000),
        FetchedSource(url="https://example.com/b", label="B", text="b" * 30_000),
    ]

    _label, combined = combine_source_text(sources)

    assert len(combined) <= 40_000
    assert "a" * 1_000 in combined
