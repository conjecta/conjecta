"""Shared type aliases used across the math_agent package."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]
