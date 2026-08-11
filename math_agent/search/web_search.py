"""Shared web search with Tavily → DuckDuckGo fallback."""
from __future__ import annotations

import logging

from math_agent.search.duckduckgo import (
    duckduckgo_search,
    is_duckduckgo_failure_message,
)
from math_agent.search.tavily import is_tavily_failure_message, tavily_search

log = logging.getLogger("math_agent.search.web_search")


async def web_search_with_fallback(
    query: str,
    *,
    max_results: int | None = None,
    use_fallback: bool = True,
) -> tuple[str, str]:
    """Search the web; return (provider, text).

    provider is one of: "tavily", "duckduckgo", "none".
    """
    kwargs = {"max_results": max_results} if max_results else {}
    tavily_output = await tavily_search(query, **kwargs)
    if not is_tavily_failure_message(tavily_output):
        return "tavily", tavily_output
    if use_fallback:
        ddg_output = await duckduckgo_search(query, **kwargs)
        if not is_duckduckgo_failure_message(ddg_output):
            log.info("Web search fell back to DuckDuckGo for %r", query[:120])
            return "duckduckgo", ddg_output
        log.warning(
            "Web search failed (Tavily + DuckDuckGo): query=%r tavily=%s ddg=%s",
            query[:120],
            tavily_output[:160],
            ddg_output[:160],
        )
        return "none", tavily_output
    return "none", tavily_output
