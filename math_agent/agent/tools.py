"""Backward-compatibility shim.

The tool registry and the built-in tool implementations moved to the
``math_agent.tools`` package (``registry``, ``context``, ``results`` and
``builtin/``). This module re-exports the public API so existing
``math_agent.agent.tools`` imports keep working; new code should import from
``math_agent.tools`` directly.
"""
from __future__ import annotations

from math_agent.tools import (
    ToolArgMap,
    ToolContext,
    ToolDescription,
    ToolFn,
    ToolRegistry,
    ToolResult,
)

__all__ = [
    "ToolArgMap",
    "ToolContext",
    "ToolDescription",
    "ToolFn",
    "ToolRegistry",
    "ToolResult",
]
