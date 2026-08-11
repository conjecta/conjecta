"""Query-based arXiv search for the math agent (no API key required)."""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus

log = logging.getLogger("math_agent.search.arxiv")

ARXIV_API_URL = "https://export.arxiv.org/api/query"
_DEFAULT_MAX_RESULTS = 5
_SUMMARY_MAX_CHARS = 400

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


def is_arxiv_failure_message(text: str) -> bool:
    return text.startswith(
        (
            "Search query cannot be empty.",
            "arXiv search timed out",
            "arXiv search failed",
            "No arXiv results",
        )
    )


def _clip(text: str, max_chars: int) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "..."


def _parse_entry(entry: ET.Element) -> dict[str, str]:
    ns = _ATOM_NS
    title = _clip(entry.findtext("atom:title", default="", namespaces=ns) or "", 300)
    summary = _clip(
        entry.findtext("atom:summary", default="", namespaces=ns) or "",
        _SUMMARY_MAX_CHARS,
    )
    published = (
        entry.findtext("atom:published", default="", namespaces=ns) or ""
    )[:10]
    entry_id = (entry.findtext("atom:id", default="", namespaces=ns) or "").strip()
    authors = [
        (author.findtext("atom:name", default="", namespaces=ns) or "").strip()
        for author in entry.findall("atom:author", ns)
    ]
    return {
        "title": title,
        "summary": summary,
        "published": published,
        "id": entry_id,
        "authors": ", ".join(a for a in authors if a),
    }


async def arxiv_search(
    query: str,
    *,
    max_results: int = _DEFAULT_MAX_RESULTS,
) -> str:
    """Search arXiv by keywords and return a plain-text briefing for the agent."""
    import httpx

    q = query.strip()
    if not q:
        return "Search query cannot be empty."

    params = {
        "search_query": f"all:{q}",
        "start": 0,
        "max_results": max(1, int(max_results)),
        "sortBy": "relevance",
    }
    url = f"{ARXIV_API_URL}?search_query={quote_plus(params['search_query'])}&start=0&max_results={params['max_results']}&sortBy=relevance"
    timeout = httpx.Timeout(30.0, connect=10.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            root = ET.fromstring(resp.text)
    except httpx.TimeoutException:
        return "arXiv search timed out after 30s"
    except httpx.HTTPError as exc:
        log.warning("arXiv search failed for %r: %s", q[:120], exc)
        return f"arXiv search failed: {exc}"
    except ET.ParseError as exc:
        log.warning("arXiv search returned invalid XML for %r: %s", q[:120], exc)
        return f"arXiv search failed: invalid response ({exc})"

    lines: list[str] = []
    for idx, entry in enumerate(root.findall("atom:entry", _ATOM_NS), start=1):
        paper = _parse_entry(entry)
        if not paper["title"]:
            continue
        block = f"{idx}. {paper['title']}"
        if paper["authors"]:
            block += f"\nAuthors: {paper['authors']}"
        if paper["published"]:
            block += f" | Published: {paper['published']}"
        if paper["summary"]:
            block += f"\n{paper['summary']}"
        if paper["id"]:
            block += f"\n({paper['id']})"
        lines.append(block)

    if not lines:
        return f"No arXiv results found for: {q}"
    return "\n\n".join(lines)
