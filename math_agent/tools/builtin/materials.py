"""Project-material tools (add/search raw text snippets)."""
from __future__ import annotations

import logging

from math_agent.tools.context import ToolContext
from math_agent.tools.results import ToolResult

log = logging.getLogger("math_agent.tools")


async def _add_material_tool(text: str, ctx: ToolContext) -> ToolResult:
    project_id = (
        ctx.project_context.project_id if ctx.project_context else None
    ) or "default"
    store = ctx.material_store
    if store is None:
        return ToolResult(
            name="add_material", output="Material store not available.", success=False
        )
    m = store.add(project_id, "text", "User-provided material", text, "user")
    return ToolResult(
        name="add_material", output=f"Added material {m.id}.", success=True
    )


async def _search_materials_tool(query: str, ctx: ToolContext) -> ToolResult:
    project_id = (
        ctx.project_context.project_id if ctx.project_context else None
    ) or "default"
    store = ctx.material_store
    if store is None:
        return ToolResult(
            name="search_materials",
            output="Material store not available.",
            success=False,
        )
    results = store.search(project_id, query, limit=10)
    if not results:
        return ToolResult(
            name="search_materials", output="No matching materials.", success=True
        )
    lines = [f"- [{m.kind}] {m.label}\n{m.text[:600]}" for m in results]
    return ToolResult(name="search_materials", output="\n\n".join(lines), success=True)
