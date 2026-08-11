"""DuckDuckGo web search fallback (no API key, no extra dependencies).

Parses the lightweight HTML endpoint; used as a fallback when Tavily is
unavailable or fails.
"""
from __future__ import annotations

import logging
import re
from html import unescape
from urllib.parse import parse_qs, quote_plus, urlparse

log = logging.getLogger("math_agent.search.duckduckgo")

DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_DEFAULT_MAX_RESULTS = 5
_SNIPPET_MAX_CHARS = 400
_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

_RESULT_BLOCK_RE = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_SNIPPET_RE = re.compile(
    r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def is_duckduckgo_failure_message(text: str) -> bool:
    return text.startswith(
        (
            "Search query cannot be empty.",
            "DuckDuckGo search timed out",
            "DuckDuckGo search failed",
            "No web search results",
        )
    )


def _clean_html(text: str) -> str:
    return unescape(_TAG_RE.sub("", text)).strip()


def _resolve_ddg_url(href: str) -> str:
    """DuckDuckGo wraps result URLs in a redirect; unwrap the real target."""
    href = unescape(href)
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc:
        uddg = parse_qs(parsed.query).get("uddg")
        if uddg and uddg[0]:
            return uddg[0]
    return href


def _parse_results(html: str, max_results: int) -> list[dict[str, str]]:
    results: list[dict[str, str]] = []
    blocks = list(_RESULT_BLOCK_RE.finditer(html))
    snippets = [m.group("snippet") for m in _SNIPPET_RE.finditer(html)]
    for idx, match in enumerate(blocks[:max_results]):
        title = _clean_html(match.group("title"))
        if not title:
            continue
        snippet = _clean_html(snippets[idx]) if idx < len(snippets) else ""
        if len(snippet) > _SNIPPET_MAX_CHARS:
            snippet = snippet[:_SNIPPET_MAX_CHARS].rstrip() + "..."
        results.append(
            {
                "title": title,
                "snippet": snippet,
                "url": _resolve_ddg_url(match.group("href")),
            }
        )
    return results


async def duckduckgo_search(
    query: str,
    *,
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> str:
    """Search DuckDuckGo and return a plain-text briefing for the agent."""
    import httpx

    q = query.strip()
    if not q:
        return "Search query cannot be empty."

    url = f"{DDG_HTML_URL}?q={quote_plus(q)}"
    headers = {"User-Agent": _USER_AGENT}
    timeout = httpx.Timeout(20.0, connect=10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            html = resp.text
    except httpx.TimeoutException:
        return "DuckDuckGo search timed out after 20s"
    except httpx.HTTPError as exc:
        log.warning("DuckDuckGo search failed for %r: %s", q[:120], exc)
        return f"DuckDuckGo search failed: {exc}"

    results = _parse_results(html, max(1, int(max_results)))
    if not results:
        return f"No web search results found for: {q}"

    lines: list[str] = []
    for idx, item in enumerate(results, start=1):
        block = f"{idx}. {item['title']}"
        if item["snippet"]:
            block += f"\n{item['snippet']}"
        if item["url"]:
            block += f"\n({item['url']})"
        lines.append(block)
    return "\n\n".join(lines)
