from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from math_agent.config import LeanConfig
from math_agent.lean.result import LeanResult
from math_agent.lean.runner import LeanRunner


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LeanJob:
    id: str
    code: str
    status: str = "queued"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    result: dict[str, Any] | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "result": self.result,
            "error": self.error,
        }


class LeanJobManager:
    def __init__(self, config: LeanConfig | None = None) -> None:
        self.config = config
        self._jobs: dict[str, LeanJob] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def create(self, code: str, *, config: LeanConfig | None = None) -> LeanJob:
        job = LeanJob(id=f"lean-{uuid.uuid4().hex[:12]}", code=code)
        self._jobs[job.id] = job
        self._tasks[job.id] = asyncio.create_task(self._run(job, config or self.config))
        return job

    def get(self, job_id: str) -> LeanJob | None:
        return self._jobs.get(job_id)

    async def shutdown(self) -> None:
        for task in self._tasks.values():
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)

    async def _run(self, job: LeanJob, config: LeanConfig | None) -> None:
        job.status = "running"
        job.updated_at = _now()
        try:
            if config is None or not config.enabled:
                result = LeanResult(
                    success=False,
                    errors=["Lean is disabled in server configuration."],
                    lean_available=False,
                    failure_kind="disabled",
                )
            else:
                result = await LeanRunner(config).check_proof(job.code)
            job.result = {
                "success": result.success,
                "errors": result.errors,
                "warnings": result.warnings,
                "uses_sorry": result.uses_sorry,
                "lean_available": result.lean_available,
                "failure_kind": result.failure_kind,
                "diagnostics": result.diagnostics,
            }
            job.status = "succeeded" if result.success else "failed"
        except asyncio.CancelledError:
            job.status = "cancelled"
            raise
        except Exception as exc:
            job.status = "failed"
            job.error = str(exc)
        finally:
            job.updated_at = _now()
