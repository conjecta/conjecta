"""Sandboxed-Python compute tool."""
from __future__ import annotations

import logging

from math_agent.tools.context import ToolContext
from math_agent.tools.python_sandbox import run_python
from math_agent.tools.results import ToolResult

log = logging.getLogger("math_agent.tools")


async def _compute_tool(code: str, _ctx: ToolContext) -> ToolResult:
    log.debug("Compute code received chars=%d", len(code))
    result = await run_python(code)
    return ToolResult(name="compute", output=result.output, success=result.success)
