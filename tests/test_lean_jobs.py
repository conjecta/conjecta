from __future__ import annotations

import asyncio

import pytest

from math_agent.config import LeanConfig
from math_agent.web.lean_jobs import LeanJobManager


@pytest.mark.asyncio
async def test_lean_job_manager_reports_disabled_lean():
    manager = LeanJobManager(LeanConfig(enabled=False))
    job = manager.create("theorem t : True := trivial")
    for _ in range(20):
        current = manager.get(job.id)
        if current and current.status in {"failed", "succeeded"}:
            break
        await asyncio.sleep(0.01)

    current = manager.get(job.id)
    assert current is not None
    assert current.status == "failed"
    assert current.result is not None
    assert current.result["failure_kind"] == "disabled"
    await manager.shutdown()
