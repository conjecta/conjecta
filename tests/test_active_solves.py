import asyncio

import pytest

from math_agent.web.active_solves import ActiveSolveRegistry


@pytest.mark.asyncio
async def test_active_solve_registry_cancels_only_for_owning_user():
    registry = ActiveSolveRegistry()
    task = asyncio.create_task(asyncio.Event().wait())
    registry.register("session-1", user_id="owner", task=task, mode="research")

    assert registry.cancel("session-1", user_id="other") is False
    assert registry.cancel("session-1", user_id="owner") is True
    await asyncio.gather(task, return_exceptions=True)
    registry.discard("session-1", task)

    assert registry.contains("session-1", user_id="owner") is False


@pytest.mark.asyncio
async def test_status_is_invisible_across_users():
    registry = ActiveSolveRegistry()
    task = asyncio.create_task(asyncio.Event().wait())
    registry.register("session-1", user_id="owner", task=task, mode="research")

    assert registry.status("session-1", user_id="other") is None
    snap = registry.status("session-1", user_id="owner")
    assert snap is not None
    assert snap["active"] is True
    assert snap["mode"] == "research"
    assert snap["done"] is False
    assert snap["cancelled"] is False

    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    registry.discard("session-1", task)


@pytest.mark.asyncio
async def test_status_after_task_completes():
    registry = ActiveSolveRegistry()

    async def _finish() -> None:
        return None

    task = asyncio.create_task(_finish())
    registry.register("session-done", user_id="owner", task=task, mode="normal")
    await task

    snap = registry.status("session-done", user_id="owner")
    assert snap is not None
    assert snap["active"] is False
    assert snap["done"] is True
    assert snap["cancelled"] is False
    assert snap["mode"] == "normal"

    registry.discard("session-done", task)
    assert registry.status("session-done", user_id="owner") is None


@pytest.mark.asyncio
async def test_status_after_discard_is_none():
    registry = ActiveSolveRegistry()
    task = asyncio.create_task(asyncio.Event().wait())
    registry.register("session-x", user_id="owner", task=task, mode="normal")
    registry.discard("session-x", task)

    assert registry.status("session-x", user_id="owner") is None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
