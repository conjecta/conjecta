"""Semantic Scholar paper search for the math agent.

Works without an API key (lower rate limit); set SEMANTIC_SCHOLAR_API_KEY
for higher throughput.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("math_agent.search.semantic_scholar")

S2_API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
_DEFAULT_MAX_RESULTS = 5
_ABSTRACT_MAX_CHARS = 400
_FIELDS = "title,abstract,authors,year,url,citationCount,externalIds"


def semantic_scholar_api_key() -> str | None:
    key = (os.getenv("SEMANTIC_SCHOLAR_API_KEY") or "").strip()
    return key or None


def is_scholar_failure_message(text: str) -> bool:
    return text.startswith(
        (
            "Search query cannot be empty.",
            "Semantic Scholar search timed out",
            "Semantic Scholar search failed",
            "Semantic Scholar rate limit",
            "No Semantic Scholar results",
        )
    )


def _clip(text: str, max_chars: int) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _format_paper(idx: int, paper: dict) -> str | None:
    title = (paper.get("title") or "").strip()
    if not title:
        return None
    block = f"{idx}. {title}"
    authors = ", ".join(
        (a.get("name") or "").strip()
        for a in paper.get("authors") or []
        if (a.get("name") or "").strip()
    )
    if authors:
        block += f"\nAuthors: {authors}"
    meta: list[str] = []
    if paper.get("year"):
        meta.append(f"Year: {paper['year']}")
    citations = paper.get("citationCount")
    if isinstance(citations, int):
        meta.append(f"Citations: {citations}")
    if meta:
        block += f" | {' | '.join(meta)}"
    abstract = (paper.get("abstract") or "").strip()
    if abstract:
        block += f"\n{_clip(abstract, _ABSTRACT_MAX_CHARS)}"
    arxiv_id = (paper.get("externalIds") or {}).get("ArXiv")
    if arxiv_id:
        block += f"\n(arXiv:{arxiv_id} | https://arxiv.org/abs/{arxiv_id})"
    elif paper.get("url"):
        block += f"\n({paper['url']})"
    return block


async def scholar_search(
    query: str,
    *,
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> str:
    """Search Semantic Scholar and return a plain-text briefing for the agent."""
    import httpx

    q = query.strip()
    if not q:
        return "Search query cannot be empty."

    params = {
        "query": q,
        "limit": max(1, int(max_results)),
        "fields": _FIELDS,
    }
    headers: dict[str, str] = {}
    key = semantic_scholar_api_key()
    if key:
        headers["x-api-key"] = key
    timeout = httpx.Timeout(30.0, connect=10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(S2_API_URL, params=params, headers=headers)
            if resp.status_code == 429:
                return "Semantic Scholar rate limit exceeded, try again later."
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return "Semantic Scholar search timed out after 30s"
    except httpx.HTTPError as exc:
        log.warning("Semantic Scholar search failed for %r: %s", q[:120], exc)
        return f"Semantic Scholar search failed: {exc}"
    except ValueError as exc:
        log.warning("Semantic Scholar returned invalid JSON for %r: %s", q[:120], exc)
        return f"Semantic Scholar search failed: invalid response ({exc})"

    lines: list[str] = []
    for idx, paper in enumerate(data.get("data") or [], start=1):
        block = _format_paper(idx, paper)
        if block:
            lines.append(block)

    if not lines:
        return f"No Semantic Scholar results found for: {q}"
    return "\n\n".join(lines)
