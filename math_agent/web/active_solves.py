from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any

# Upper bound on solves running concurrently in this process. Each one holds an
# agent object graph, provider connections, and an event queue (~150MB), and
# keeps running after a client disconnects, so an unbounded count degrades every
# in-flight solve rather than shedding the excess.
DEFAULT_MAX_CONCURRENT_SOLVES = 24


def max_concurrent_solves() -> int:
    raw = os.getenv("CONJECTA_MAX_CONCURRENT_SOLVES", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_CONCURRENT_SOLVES
    except ValueError:
        value = DEFAULT_MAX_CONCURRENT_SOLVES
    return max(1, value)


class SolveCapacityError(RuntimeError):
    """Raised when the process is already running its maximum solves."""


class SolveCapacity:
    """Process-wide admission control for solve runs.

    A plain counter rather than a Semaphore: admission is non-blocking (an
    over-capacity request is rejected with 429, never queued), and the event
    loop is single-threaded, so no additional synchronization is needed.
    """

    def __init__(self) -> None:
        self._in_flight = 0

    def try_acquire(self) -> bool:
        """Take a slot without waiting. False means the process is at capacity."""
        if self._in_flight >= max_concurrent_solves():
            return False
        self._in_flight += 1
        return True

    def release(self) -> None:
        self._in_flight = max(0, self._in_flight - 1)

    @property
    def in_flight(self) -> int:
        return self._in_flight


solve_capacity = SolveCapacity()


@dataclass
class ActiveSolve:
    user_id: str
    task: asyncio.Task[Any]
    mode: str


class ActiveSolveRegistry:
    """Track long-running solves so explicit stop differs from disconnect."""

    def __init__(self) -> None:
        self._items: dict[str, ActiveSolve] = {}

    def register(
        self,
        session_id: str,
        *,
        user_id: str | None,
        task: asyncio.Task[Any],
        mode: str,
    ) -> None:
        self._items[session_id] = ActiveSolve(user_id or "", task, mode)

    def discard(self, session_id: str, task: asyncio.Task[Any] | None = None) -> None:
        current = self._items.get(session_id)
        if current is None or (task is not None and current.task is not task):
            return
        self._items.pop(session_id, None)

    def cancel(self, session_id: str, *, user_id: str | None) -> bool:
        current = self._items.get(session_id)
        if current is None or current.user_id != (user_id or ""):
            return False
        if not current.task.done():
            current.task.cancel()
        return True

    def contains(self, session_id: str, *, user_id: str | None) -> bool:
        current = self._items.get(session_id)
        return bool(current and current.user_id == (user_id or ""))

    def status(self, session_id: str, *, user_id: str | None) -> dict[str, Any] | None:
        current = self._items.get(session_id)
        if current is None or current.user_id != (user_id or ""):
            return None
        task = current.task
        return {
            "session_id": session_id,
            "mode": current.mode,
            "active": not task.done(),
            "done": task.done() and not task.cancelled(),
            "cancelled": task.cancelled() if task.done() else False,
        }


active_solve_tasks = ActiveSolveRegistry()
