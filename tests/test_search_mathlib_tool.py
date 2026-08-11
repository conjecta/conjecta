from __future__ import annotations

import asyncio
import time

import pytest

from math_agent.agent.react_state import Action
from math_agent.agent.tools import ToolContext, ToolRegistry


def test_registry_lists_search_mathlib():
    registry = ToolRegistry(enabled_tools=["search_mathlib"])
    assert "search_mathlib" in registry.available


@pytest.mark.asyncio
async def test_execute_action_search_mathlib(monkeypatch):
    def fake_search_by_name(name: str, *, max_results: int = 10):
        return [
            {
                "name": "Nat.Prime",
                "module": "Mathlib.Data.Nat.Prime.Defs",
                "signature": "Nat.Prime (n : Nat) : Prop",
            }
        ]

    class FakeSearch:
        def search_by_name(self, name: str, *, max_results: int = 10):
            return fake_search_by_name(name, max_results=max_results)

    monkeypatch.setattr(
        "math_agent.agent.tools.default_search", FakeSearch
    )

    registry = ToolRegistry(enabled_tools=["search_mathlib"])
    action = Action(name="search_mathlib", args={"query": "Nat.Prime"})
    obs = await registry.execute_action(action, ToolContext())
    assert obs.success is True
    assert "Nat.Prime" in obs.output
    assert "Mathlib.Data.Nat.Prime.Defs" in obs.output


@pytest.mark.asyncio
async def test_search_mathlib_no_results(monkeypatch):
    class FakeSearch:
        def search_by_name(self, name: str, *, max_results: int = 10):
            return []

        def search_by_type_snippet(self, snippet: str, *, max_results: int = 10):
            return []

    monkeypatch.setattr(
        "math_agent.agent.tools.default_search", FakeSearch
    )

    registry = ToolRegistry(enabled_tools=["search_mathlib"])
    result = await registry.call("search_mathlib", "NonexistentDecl", ToolContext())
    assert result.success is True
    assert "No mathlib declarations found" in result.output
    assert "Proceed by constructing the proof" in result.output


@pytest.mark.asyncio
async def test_search_mathlib_failure(monkeypatch):
    def boom():
        raise RuntimeError("mathlib4 checkout not found")

    monkeypatch.setattr("math_agent.agent.tools.default_search", boom)

    registry = ToolRegistry(enabled_tools=["search_mathlib"])
    result = await registry.call("search_mathlib", "Nat.Prime", ToolContext())
    assert result.success is False
    assert "Search failed" in result.output
    assert "mathlib4 checkout not found" in result.output


@pytest.mark.asyncio
async def test_search_mathlib_does_not_block_event_loop(monkeypatch):
    """If search_by_name sleeps, other asyncio tasks should still run."""
    call_times: list[float] = []

    def slow_search_by_name(name: str, *, max_results: int = 10):
        time.sleep(0.2)
        call_times.append(time.monotonic())
        return []

    def fast_search_by_type_snippet(snippet: str, *, max_results: int = 10):
        call_times.append(time.monotonic())
        return []

    class FakeSearch:
        def search_by_name(self, name: str, *, max_results: int = 10):
            return slow_search_by_name(name, max_results=max_results)

        def search_by_type_snippet(self, snippet: str, *, max_results: int = 10):
            return fast_search_by_type_snippet(snippet, max_results=max_results)

    monkeypatch.setattr("math_agent.agent.tools.default_search", FakeSearch)

    async def background_task():
        await asyncio.sleep(0.05)
        call_times.append(time.monotonic())

    registry = ToolRegistry(enabled_tools=["search_mathlib"])
    action = Action(name="search_mathlib", args={"query": "slow"})

    _, pending = await asyncio.wait(
        {
            asyncio.create_task(registry.execute_action(action, ToolContext())),
            asyncio.create_task(background_task()),
        },
        return_when=asyncio.ALL_COMPLETED,
    )
    assert not pending

    # The background task must complete during the 0.2s synchronous sleep.
    assert len(call_times) >= 3
