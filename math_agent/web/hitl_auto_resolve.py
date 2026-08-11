"""Auto-resolve HITL pauses that receive no human response in time.

When a solve run pauses for human input it would otherwise wait forever.
With ``[agent.hitl] auto_resolve_seconds`` > 0, entering the waiting state
arms a timer keyed by ``(session_id, request_id)``; on expiry the default
decision (``approve`` when allowed, else the first allowed decision) is
claimed atomically via ``claim_human_decision`` and the run resumes
headless — the same checkpoint resume the human ``decisions/stream``
endpoint performs, but without a client, an HTTP request, or a quota
charge (same detached pattern as ``run_recovery``).

A human decision that wins the claim race simply makes the timer a no-op,
and a successful human claim cancels the timer outright.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from math_agent.config import load_config
from math_agent.web import agent_factory

log = logging.getLogger("math_agent.web.hitl_auto_resolve")

# (session_id, request_id) -> pending auto-resolve task.
_timers: dict[tuple[str, str], asyncio.Task[None]] = {}


def configured_timeout() -> float:
    """Return the auto-resolve delay in seconds; <= 0 disables the feature."""
    try:
        return float(load_config().agent.hitl.auto_resolve_seconds)
    except Exception:
        return 0.0


def default_decision(allowed_decisions: list[Any]) -> str | None:
    """Pick the default decision for a pending interaction."""
    allowed = [str(item) for item in allowed_decisions or []]
    if not allowed:
        return None
    return "approve" if "approve" in allowed else allowed[0]


def schedule_auto_resolve(
    session_id: str,
    request_id: str,
    *,
    user_id: str | None,
    timeout_seconds: float | None = None,
) -> None:
    """Arm the auto-resolve timer for one pending interaction."""
    timeout = configured_timeout() if timeout_seconds is None else float(timeout_seconds)
    if timeout <= 0 or not session_id or not request_id or not user_id:
        return
    key = (session_id, request_id)
    cancel_auto_resolve(*key)
    _timers[key] = asyncio.create_task(
        _auto_resolve_after_timeout(
            session_id, request_id, user_id=user_id, timeout_seconds=timeout
        )
    )


def cancel_auto_resolve(session_id: str, request_id: str | None = None) -> None:
    """Cancel timers for a session, optionally scoped to one request."""
    for key in [
        key
        for key in _timers
        if key[0] == session_id and (request_id is None or key[1] == request_id)
    ]:
        _timers.pop(key).cancel()


async def shutdown_auto_resolve_timers() -> None:
    """Cancel every pending timer; called from the app lifespan shutdown."""
    tasks = list(_timers.values())
    _timers.clear()
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _auto_resolve_after_timeout(
    session_id: str,
    request_id: str,
    *,
    user_id: str,
    timeout_seconds: float,
) -> None:
    try:
        await asyncio.sleep(timeout_seconds)
        store = agent_factory._project_store(user_id)
        checkpoint = await asyncio.to_thread(store.get_checkpoint, session_id)
        pending = (
            checkpoint.get("pending_interaction")
            if isinstance(checkpoint, dict)
            else None
        )
        if not isinstance(pending, dict) or str(
            pending.get("request_id") or ""
        ) != request_id:
            # The run moved on (decided, resumed, or re-paused elsewhere).
            return
        decision = default_decision(pending.get("allowed_decisions") or [])
        if decision is None:
            log.warning(
                "HITL auto-resolve skipped: no allowed decisions session=%s request=%s",
                session_id,
                request_id,
            )
            return
        human_decision = {
            "request_id": request_id,
            "decision": decision,
            "feedback": (
                "auto-resolved: no human response within "
                f"{int(timeout_seconds)}s"
            ),
        }
        claimed = await asyncio.to_thread(
            store.claim_human_decision, session_id, human_decision
        )
        if claimed is None:
            # A human decision won the claim race; leave the run to them.
            return
        log.info(
            "HITL auto-resolved session=%s request=%s decision=%s",
            session_id,
            request_id,
            decision,
        )
        msg = {
            "problem": "",
            "checkpoint_id": session_id,
            "session_id": session_id,
            "project_id": str(checkpoint.get("project_id") or "default"),
            "mode": "react",
            "human_decision": human_decision,
        }
        # Headless resume: drain the shared session stream so events reach
        # the recorder and the result persists, with no transport attached.
        from math_agent.web.solve_session import stream_solve_events

        async for _event in stream_solve_events(msg, user_id=user_id):
            pass
    except asyncio.CancelledError:
        raise
    except Exception:
        log.exception(
            "HITL auto-resolve failed session=%s request=%s", session_id, request_id
        )
    finally:
        _timers.pop((session_id, request_id), None)
