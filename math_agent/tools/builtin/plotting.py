"""matplotlib figure-plotting tool."""
from __future__ import annotations

import json
import logging

from math_agent.tools.context import ToolContext
from math_agent.tools.plot_sandbox import run_plot
from math_agent.tools.results import ToolResult

log = logging.getLogger("math_agent.tools")


def _safe_caption(text: str) -> str:
    """Keep a caption from breaking the markdown image syntax."""
    cleaned = text.replace("[", "(").replace("]", ")").strip()
    return cleaned[:120] or "figure"


async def _plot_figure_tool(args_json: str, ctx: ToolContext) -> ToolResult:
    try:
        payload = json.loads(args_json) if args_json.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    code = str(payload.get("code") or "")
    caption = _safe_caption(str(payload.get("caption") or ""))
    log.debug("Plot figure code received chars=%d", len(code))

    if ctx.figure_dir is None:
        return ToolResult(
            name="plot_figure",
            output="plot_figure is unavailable: no figure directory is configured for this session.",
            success=False,
        )

    result = await run_plot(code, out_dir=ctx.figure_dir)
    if not result.success:
        return ToolResult(name="plot_figure", output=result.output, success=False)

    embeds: list[str] = []
    for name in result.figures:
        if ctx.figure_url_prefix:
            embeds.append(f"![{caption}]({ctx.figure_url_prefix}/{name})")
        else:
            embeds.append(f"{ctx.figure_dir / name}")
    embed_block = "\n".join(embeds)
    output = (
        f"Figure saved ({len(result.figures)} image(s)). "
        "Embed it in the final answer verbatim on its own line:\n"
        f"{embed_block}"
    )
    if result.output:
        output += f"\n[plot output]\n{result.output}"
    return ToolResult(name="plot_figure", output=output, success=True)
