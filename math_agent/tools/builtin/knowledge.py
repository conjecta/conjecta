"""Knowledge-store and knowledge-graph tools."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from math_agent.tools.context import ToolContext
from math_agent.tools.results import ToolResult

if TYPE_CHECKING:
    from math_agent.agent.react_state import ToolObservation

log = logging.getLogger("math_agent.tools")


async def _relate_knowledge_tool(spec: str, ctx: ToolContext) -> ToolResult:
    project_id = (
        ctx.project_context.project_id if ctx.project_context else None
    ) or "default"
    graph = ctx.knowledge_graph
    if graph is None:
        return ToolResult(
            name="relate_knowledge",
            output="Knowledge graph not available.",
            success=False,
        )

    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 3:
        return ToolResult(
            name="relate_knowledge",
            output="Expected format: from_id,to_id,relation (e.g. fact-1,fact-2,implies)",
            success=False,
        )
    from_id, to_id, relation = parts
    rel = graph.add_relation(from_id, to_id, relation, project_id)
    return ToolResult(
        name="relate_knowledge",
        output=f"Added relation {rel.id}: {from_id} {rel.relation} {to_id}",
        success=True,
    )


async def _find_related_tool(item_id: str, ctx: ToolContext) -> ToolResult:
    project_id = (
        ctx.project_context.project_id if ctx.project_context else None
    ) or "default"
    graph = ctx.knowledge_graph
    if graph is None:
        return ToolResult(
            name="find_related", output="Knowledge graph not available.", success=False
        )
    related = graph.get_related(item_id, project_id)
    if not related:
        return ToolResult(name="find_related", output="No related items.", success=True)
    lines = [f"- {r['from_id']} --{r['relation']}--> {r['to_id']}" for r in related]
    return ToolResult(name="find_related", output="\n".join(lines), success=True)


async def _search_knowledge_adapter(query: str, ctx: ToolContext) -> ToolResult:
    """Adapter exposing search_knowledge via the standard ToolFn interface."""
    project_id = ctx.project_context.project_id if ctx.project_context else None
    observation = await _search_knowledge_tool(
        query, project_id, ctx.knowledge_store, knowledge_config=ctx.knowledge_config
    )
    return ToolResult(
        name="search_knowledge",
        output=observation.output,
        success=observation.success,
        lean_code=observation.lean_code,
    )


async def _search_knowledge_tool(
    query: str,
    project_id: str | None,
    knowledge_store: object | None = None,
    *,
    knowledge_config: object | None = None,
) -> ToolObservation:
    from math_agent.agent.react_state import ToolObservation

    if not project_id:
        return ToolObservation(
            success=True,
            output="No project context available; knowledge search skipped.",
        )
    if not query.strip():
        return ToolObservation(
            success=True,
            output="Empty knowledge query; nothing to search.",
        )
    try:
        store = knowledge_store
        if store is None:
            from math_agent.knowledge.supabase import KnowledgeStore

            store = (
                KnowledgeStore(knowledge_config=knowledge_config)
                if knowledge_config is not None
                else KnowledgeStore()
            )
        if not all(
            hasattr(store, method)
            for method in ("search_facts", "search_intuitions", "search_tricks")
        ):
            return ToolObservation(
                success=True,
                output="Knowledge search unavailable for this project store.",
            )
        facts = await asyncio.to_thread(store.search_facts, project_id, query, limit=5)
        intuitions = await asyncio.to_thread(store.search_intuitions, project_id, query, limit=5)
        tricks = await asyncio.to_thread(store.search_tricks, project_id, query, limit=5)
        lines = _format_knowledge_search_results(facts, intuitions, tricks)
        output = "\n".join(lines) if lines else "No relevant knowledge found."
        return ToolObservation(success=True, output=output)
    except Exception as e:
        return ToolObservation(
            success=False,
            output=f"Knowledge search failed: {e}",
            error=str(e),
        )


def _format_knowledge_search_results(
    facts: list[dict],
    intuitions: list[dict],
    tricks: list[dict],
) -> list[str]:
    lines: list[str] = []
    if facts:
        lines.append("Facts:")
        for f in facts:
            lines.append(f"  - {f.get('statement', '')}")
    if intuitions:
        lines.append("Intuitions:")
        for i in intuitions:
            lines.append(f"  - {i.get('title', '')}: {i.get('body', '')}")
    if tricks:
        lines.append("Tricks:")
        for t in tricks:
            lines.append(f"  - {t.get('title', '')}: {t.get('body', '')}")
    return lines
