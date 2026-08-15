"""Built-in tool implementations, grouped by domain.

The registry imports its tool functions from here; these modules depend only
on ``math_agent.tools.context`` / ``math_agent.tools.results`` (plus leaf
modules), never on the registry, so there is no import cycle.
"""
from __future__ import annotations

from math_agent.tools.builtin.compute import _compute_tool
from math_agent.tools.builtin.knowledge import (
    _find_related_tool,
    _format_knowledge_search_results,
    _relate_knowledge_tool,
    _search_knowledge_adapter,
    _search_knowledge_tool,
)
from math_agent.tools.builtin.lean_tools import (
    _formalize_tool,
    _formalize_unavailable_tool,
    _lean_check_tool,
    _lean_check_unavailable_tool,
    _prove_by_lemmas_tool,
    _prove_by_lemmas_unavailable_tool,
    _search_mathlib_tool,
    _tactic_search_tool,
    _tactic_search_unavailable_tool,
)
from math_agent.tools.builtin.materials import (
    _add_material_tool,
    _search_materials_tool,
)
from math_agent.tools.builtin.plotting import _plot_figure_tool
from math_agent.tools.builtin.search import (
    _fetch_url_tool,
    _llm_search_content,
    _read_sources_tool,
    _search,
    _search_arxiv_tool,
    _search_scholar_tool,
    _search_tool,
    _searching_tool,
)

__all__ = [
    "_add_material_tool",
    "_compute_tool",
    "_fetch_url_tool",
    "_find_related_tool",
    "_formalize_tool",
    "_formalize_unavailable_tool",
    "_format_knowledge_search_results",
    "_lean_check_tool",
    "_lean_check_unavailable_tool",
    "_llm_search_content",
    "_plot_figure_tool",
    "_prove_by_lemmas_tool",
    "_prove_by_lemmas_unavailable_tool",
    "_read_sources_tool",
    "_relate_knowledge_tool",
    "_search",
    "_search_arxiv_tool",
    "_search_knowledge_adapter",
    "_search_knowledge_tool",
    "_search_materials_tool",
    "_search_mathlib_tool",
    "_search_scholar_tool",
    "_search_tool",
    "_searching_tool",
    "_tactic_search_tool",
    "_tactic_search_unavailable_tool",
]
