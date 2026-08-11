"""Tavily web search for the math agent."""
from __future__ import annotations

import logging
import os

log = logging.getLogger("math_agent.search.tavily")

TAVILY_API_URL = "https://api.tavily.com/search"
_DEFAULT_MAX_RESULTS = 5


def tavily_api_key() -> str | None:
    key = (os.getenv("TAVILY_API_KEY") or "").strip()
    return key or None


def is_tavily_failure_message(text: str) -> bool:
    return text.startswith(
        (
            "Search query cannot be empty.",
            "Search timed out",
            "Search failed",
            "No web search results",
            "Tavily search unavailable",
        )
    )


async def tavily_search(
    query: str,
    *,
    api_key: str | None = None,
    max_results: int = _DEFAULT_MAX_RESULTS,
    search_depth: str = "basic",
) -> str:
    """Run a Tavily search and return a plain-text briefing for the agent."""
    import httpx

    q = query.strip()
    if not q:
        return "Search query cannot be empty."

    key = api_key or tavily_api_key()
    if not key:
        return "Tavily search unavailable (set TAVILY_API_KEY)."

    payload = {
        "api_key": key,
        "query": q,
        "max_results": max_results,
        "search_depth": search_depth,
        "include_answer": True,
    }
    timeout = httpx.Timeout(60.0, connect=10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(TAVILY_API_URL, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except httpx.TimeoutException:
        return "Search timed out after 60s"
    except httpx.HTTPError as exc:
        log.warning("Tavily search failed for %r: %s", q[:120], exc)
        return f"Search failed: {exc}"

    lines: list[str] = []
    answer = (data.get("answer") or "").strip()
    if answer:
        lines.append(f"Summary: {answer}")

    for idx, item in enumerate(data.get("results") or [], start=1):
        title = (item.get("title") or "Untitled").strip()
        content = (item.get("content") or "").strip()
        url = (item.get("url") or "").strip()
        snippet = content[:800] + ("..." if len(content) > 800 else "")
        block = f"{idx}. {title}\n{snippet}"
        if url:
            block += f"\n({url})"
        lines.append(block)
        if idx >= max_results:
            break

    if not lines:
        return f"No web search results found for: {q}"
    return "\n\n".join(lines)
