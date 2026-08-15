"""Web/literature search and source-fetching tools."""
from __future__ import annotations

import logging

from math_agent.net_safety import UnsafeFetchURL, fetch_public_url
from math_agent.tools.context import ToolContext
from math_agent.tools.results import ToolResult

log = logging.getLogger("math_agent.tools")


def _search_max_results(ctx: ToolContext | None) -> int | None:
    if ctx is not None and ctx.search_config is not None:
        return ctx.search_config.max_results
    return None


def _search_fallback_enabled(ctx: ToolContext | None) -> bool:
    if ctx is not None and ctx.search_config is not None:
        return ctx.search_config.fallback_provider != "none"
    return True


async def _search(
    query: str,
    *,
    max_results: int | None = None,
    use_fallback: bool = True,
) -> str:
    """Web search: Tavily first, DuckDuckGo as fallback."""
    from math_agent.search.duckduckgo import (
        duckduckgo_search,
        is_duckduckgo_failure_message,
    )
    from math_agent.search.tavily import is_tavily_failure_message, tavily_search

    kwargs = {"max_results": max_results} if max_results else {}
    output = await tavily_search(query, **kwargs)
    if not use_fallback or not is_tavily_failure_message(output):
        return output
    ddg_output = await duckduckgo_search(query, **kwargs)
    if is_duckduckgo_failure_message(ddg_output):
        # Both providers failed; report the primary provider's error.
        return output
    return f"[web search via DuckDuckGo]\n{ddg_output}"


async def _search_tool(query: str, ctx: ToolContext) -> ToolResult:
    from math_agent.search.tavily import is_tavily_failure_message

    output = await _search(
        query,
        max_results=_search_max_results(ctx),
        use_fallback=_search_fallback_enabled(ctx),
    )
    return ToolResult(
        name="search",
        output=output,
        success=not is_tavily_failure_message(output),
    )


async def _search_arxiv_tool(query: str, ctx: ToolContext) -> ToolResult:
    from math_agent.search.arxiv import arxiv_search, is_arxiv_failure_message

    max_results = _search_max_results(ctx)
    kwargs = {"max_results": max_results} if max_results else {}
    output = await arxiv_search(query, **kwargs)
    return ToolResult(
        name="search_arxiv",
        output=output,
        success=not is_arxiv_failure_message(output),
    )


async def _search_scholar_tool(query: str, ctx: ToolContext) -> ToolResult:
    from math_agent.search.semantic_scholar import (
        is_scholar_failure_message,
        scholar_search,
    )

    max_results = _search_max_results(ctx)
    kwargs = {"max_results": max_results} if max_results else {}
    output = await scholar_search(query, **kwargs)
    return ToolResult(
        name="search_scholar",
        output=output,
        success=not is_scholar_failure_message(output),
    )


async def _fetch_url_tool(url: str, _ctx: ToolContext) -> ToolResult:
    """Fetch URL content and return a readable text snippet."""
    from math_agent.source_fetch import extract_html_text

    raw_url = url.strip()
    headers = {"User-Agent": "ConjectaMathAgent/0.1 (+tool fetch_url)"}
    try:
        resp = await fetch_public_url(
            raw_url,
            timeout_seconds=12.0,
            headers=headers,
            max_bytes=2 * 1024 * 1024,
        )
    except UnsafeFetchURL as exc:
        return ToolResult(name="fetch_url", output=str(exc), success=False)
    except Exception as exc:
        return ToolResult(
            name="fetch_url", output=f"Fetch failed: {exc}", success=False
        )

    content_type = (resp.headers.get("content-type") or "").lower()
    text = (
        extract_html_text(resp.text)
        if "text/html" in content_type
        else resp.text.strip()
    )

    if not text:
        return ToolResult(
            name="fetch_url",
            output=f"Fetched {resp.url}, but content was empty.",
            success=False,
        )

    max_chars = 3500
    snippet = text[:max_chars]
    if len(text) > max_chars:
        snippet += " ... [truncated]"
    return ToolResult(
        name="fetch_url", output=f"Source: {resp.url}\n{snippet}", success=True
    )


async def _read_sources_tool(prompt: str, ctx: ToolContext) -> ToolResult:
    from math_agent.source_fetch import fetch_sources_from_prompt

    project_id = (
        ctx.project_context.project_id if ctx.project_context else None
    ) or "default"
    store = ctx.material_store
    if store is None:
        return ToolResult(
            name="read_sources", output="Material store not available.", success=False
        )

    sources = await fetch_sources_from_prompt(prompt, max_chars=60_000)
    if not sources:
        return ToolResult(
            name="read_sources",
            output="No sources found or fetched from the prompt.",
            success=True,
        )

    added: list[str] = []
    for src in sources:
        kind = "arxiv" if "arxiv" in src.url.lower() else "url"
        m = store.add(project_id, kind, src.label, src.text, src.url)
        added.append(m.id)

    summary = f"Fetched {len(sources)} source(s). Material IDs: {', '.join(added)}."
    return ToolResult(name="read_sources", output=summary, success=True)


async def _searching_tool(query: str, ctx: ToolContext) -> ToolResult:
    from math_agent.search.duckduckgo import (
        duckduckgo_search,
        is_duckduckgo_failure_message,
    )
    from math_agent.search.tavily import is_tavily_failure_message, tavily_search

    max_results = _search_max_results(ctx)
    kwargs = {"max_results": max_results} if max_results else {}
    tavily_output = await tavily_search(query, **kwargs)
    if not is_tavily_failure_message(tavily_output):
        return ToolResult(name="searching", output=tavily_output, success=True)
    if _search_fallback_enabled(ctx):
        ddg_output = await duckduckgo_search(query, **kwargs)
        if not is_duckduckgo_failure_message(ddg_output):
            return ToolResult(
                name="searching",
                output=f"[web search via DuckDuckGo]\n{ddg_output}",
                success=True,
            )
    if ctx.llm is None:
        return ToolResult(name="searching", output=tavily_output, success=False)
    output = await _llm_search_content(query, ctx.llm)
    return ToolResult(
        name="searching",
        output=f"[model knowledge, not from live search]\n{output}",
        success=True,
    )


async def _llm_search_content(query: str, llm: object) -> str:
    from math_agent.agent.prompts import LLM_SEARCH_SYSTEM, with_time_context
    from math_agent.llm.base import Message

    q = query.strip()
    if not q:
        return "Search query cannot be empty."
    try:
        response = await llm.complete(  # type: ignore[union-attr]
            [Message(role="user", content=q)],
            system=with_time_context(LLM_SEARCH_SYSTEM),
            temperature=0.0,
        )
        return response.text
    except Exception as exc:
        log.warning("LLM search fallback failed: %s", exc)
        return f"LLM search failed: {exc}"
