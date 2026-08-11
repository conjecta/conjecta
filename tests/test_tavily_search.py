from __future__ import annotations

from math_agent.agent.supervisor_intake import _normalize_intake
from math_agent.search.tavily import is_tavily_failure_message


def test_normalize_intake_with_search():
    result = _normalize_intake({
        "strategy": "react",
        "source_digest": "",
        "source_label": "",
        "needs_search": True,
        "search_query": "Vershynin random tensors open problems",
    })
    assert result.needs_search is True
    assert "Vershynin" in result.search_query


def test_is_tavily_failure_message():
    assert is_tavily_failure_message("No web search results found for: foo")
    assert is_tavily_failure_message("Tavily search unavailable (set TAVILY_API_KEY).")
    assert not is_tavily_failure_message("Summary: Paper about tensors")
