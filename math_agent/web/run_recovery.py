"""Resume solve runs that were interrupted by a server restart.

Runs are in-process asyncio tasks, so a restart kills every active solve.
At startup we scan the operations table for rows still marked ``running``
(they can only be leftovers from the previous process at that point) and
resume each one from its persisted checkpoint as a detached headless run
that keeps the original session id — clients polling ``/status`` for that
session pick it up transparently.

Bounds (env-tunable) prevent a recovery stampede:
- ``CONJECTA_RECOVERY_MAX_RUNS`` (default 20) caps resumed runs per startup.
- ``CONJECTA_RECOVERY_MAX_AGE_HOURS`` (default 24) ignores older leftovers.

Everything left marked ``running`` after the scan window is flipped to
``interrupted`` so rows never stay stuck. Runs paused for human input are
set to ``waiting`` and left for the user's decision instead of resuming.

Known boundary: the deployment is a single uvicorn worker; the active-solve
registry is per-process, so this does not coordinate across workers.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from math_agent.knowledge.supabase_client import service_role_configured
from math_agent.web import agent_factory
from math_agent.web.operations import OperationsStore, finish_solve_run
from math_agent.web.project_store import project_store_for_user

log = logging.getLogger("math_agent.web.run_recovery")

_DEFAULT_MAX_RUNS = 20
_DEFAULT_MAX_AGE_HOURS = 24.0


def _max_runs() -> int:
    try:
        return max(0, int(os.getenv("CONJECTA_RECOVERY_MAX_RUNS", _DEFAULT_MAX_RUNS)))
    except ValueError:
        return _DEFAULT_MAX_RUNS


def _max_age_hours() -> float:
    try:
        return max(0.0, float(os.getenv("CONJECTA_RECOVERY_MAX_AGE_HOURS", _DEFAULT_MAX_AGE_HOURS)))
    except ValueError:
        return _DEFAULT_MAX_AGE_HOURS


async def _drive_resumed_run(msg: dict[str, Any], user_id: str) -> None:
    """Drain a headless resume stream; the session registers itself active."""
    from math_agent.web.solve_session import stream_solve_events

    try:
        async for _event in stream_solve_events(msg, user_id=user_id):
            pass
    except Exception:
        log.exception(
            "Resumed solve run failed session=%s", msg.get("session_id")
        )


async def recover_interrupted_runs() -> None:
    if not service_role_configured():
        log.info("Solve run recovery skipped: service role not configured")
        return
    now = datetime.now(timezone.utc)
    started_before = now.isoformat()
    since_iso = (now - timedelta(hours=_max_age_hours())).isoformat()
    try:
        store = OperationsStore()
        candidates = await asyncio.to_thread(
            store.list_running_runs,
            since_iso=since_iso,
            started_before=started_before,
            limit=_max_runs(),
        )
        # Every "running" row predating this scan is a leftover; resumable
        # candidates flip back to "running" when their resume starts.
        await asyncio.to_thread(
            store.mark_running_interrupted,
            started_before=started_before,
            reason="server_restart",
        )
    except Exception:
        log.exception("Failed to scan interrupted solve runs")
        return

    for run in candidates:
        session_id = str(run.get("id") or "").strip()
        user_id = str(run.get("user_id") or "").strip()
        if not session_id or not user_id:
            continue
        try:
            checkpoint = await asyncio.to_thread(
                project_store_for_user(user_id).get_checkpoint, session_id
            )
        except Exception:
            log.warning("Checkpoint load failed for session=%s", session_id)
            checkpoint = None
        if not isinstance(checkpoint, dict):
            # Nothing to resume from; the run stays "interrupted".
            continue
        if isinstance(checkpoint.get("pending_interaction"), dict):
            # Paused for human input: leave the decision to the user.
            await finish_solve_run(session_id=session_id, status="waiting")
            continue
        msg = {
            "checkpoint_id": session_id,
            "session_id": session_id,
            "project_id": str(run.get("project_id") or "default"),
            "mode": str(run.get("mode") or "react"),
        }
        agent_factory.post_solve_tasks.create(_drive_resumed_run(msg, user_id))
        log.info(
            "Resuming interrupted solve run session=%s user=%s",
            session_id,
            user_id,
        )
        # Stagger spawns to soften the burst on the LLM provider.
        await asyncio.sleep(1.0)
