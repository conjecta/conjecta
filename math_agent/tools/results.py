"""Result and description dataclasses shared by the tool registry."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ToolResult:
    name: str
    output: str
    success: bool
    lean_code: str | None = None


@dataclass
class ToolDescription:
    """Data-driven description of a tool exposed to the LLM.

    Keeping descriptions in the registry removes the need to hardcode tool
    lists in prompts and lets us disclose MCP tools progressively.
    """

    name: str
    args: str
    description: str
    category: str = "builtin"  # "builtin", "plugin", or "mcp"
