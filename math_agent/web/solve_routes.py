"""HTTP routes for solve sessions: NDJSON streams, interrupt/status, HITL
resume, goal actions, and Lean jobs."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from math_agent.billing.models import StoredApiKey
from math_agent.config import load_config
from math_agent.llm.tracking import UsageAccumulator as _UsageAccumulator
from math_agent.agent.research_artifacts import _safe_component
from math_agent.web.active_solves import (
    active_solve_tasks,
    max_concurrent_solves,
)
from math_agent.web.agent_factory import (
    _begin_solve_quota,
    _project_store,
    _record_usage,
    _settle_solve_quota,
    _solve_usage,
    _solve_user_api_key,
    lean_jobs,
)
from math_agent.web.agent_factory import (  # noqa: F401  (tests patch it here)
    _check_solve_quota,
)
from math_agent.web.attachments import MAX_SOLVE_REQUEST_BYTES
from math_agent.web.security import require_auth_user, require_http_app_access
from math_agent.web.solve_mode import resolve_solve_mode
from math_agent.web import hitl_auto_resolve
from math_agent.web.solve_session import encode_ndjson_event, stream_solve_events
from math_agent.web.state_backend import get_state_backend
from math_agent.web.trace_store import SESSION_ID_RE, read_trace, trace_exists

web_log = logging.getLogger("math_agent.web")
SOLVE_STREAM_HEARTBEAT_SECONDS = 15

router = APIRouter(prefix="/api", tags=["solve"])


async def _read_solve_json_body(request: Request) -> dict[str, Any]:
    """Read one solve request without ever materializing more than the hard cap."""
    raw_content_length = request.headers.get("content-length", "").strip()
    if raw_content_length:
        try:
            content_length = int(raw_content_length)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid Content-Length.") from exc
        if content_length < 0:
            raise HTTPException(status_code=400, detail="Invalid Content-Length.")
        if content_length > MAX_SOLVE_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="Solve request is too large.")

    body = bytearray()
    async for chunk in request.stream():
        if not isinstance(chunk, bytes):
            raise HTTPException(status_code=400, detail="Invalid request body.")
        if len(chunk) > MAX_SOLVE_REQUEST_BYTES - len(body):
            raise HTTPException(status_code=413, detail="Solve request is too large.")
        body.extend(chunk)
    try:
        msg = json.loads(body)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid JSON body.") from exc
    if not isinstance(msg, dict):
        raise HTTPException(status_code=400, detail="Expected a JSON object.")
    return msg


async def _ndjson_solve_stream(
    msg: dict[str, Any],
    request: Request,
    *,
    user_id: str,
    user_api_key: StoredApiKey | None,
    usage: _UsageAccumulator,
    quota_reservation: str | None = None,
) -> AsyncIterator[str]:
    """Encode one solve session as NDJSON with heartbeat pings and disconnect
    logging. Shared by the solve stream, HITL-decision resume, and goal-action
    resume endpoints."""
    token_key = _solve_user_api_key.set(user_api_key)
    token_usage = _solve_usage.set(usage)
    events = stream_solve_events(msg, user_id=user_id)
    aiter = events.__aiter__()
    pending: asyncio.Task[dict[str, Any]] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(aiter.__anext__())
            try:
                event = await asyncio.wait_for(
                    asyncio.shield(pending),
                    timeout=SOLVE_STREAM_HEARTBEAT_SECONDS,
                )
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    web_log.warning(
                        "Solve stream client disconnected user=%s; closing event stream",
                        user_id,
                    )
                    break
                yield encode_ndjson_event({"type": "ping"})
                continue
            except StopAsyncIteration:
                break
            pending = None
            if await request.is_disconnected():
                web_log.warning(
                    "Solve stream client disconnected user=%s; closing event stream",
                    user_id,
                )
                break
            yield encode_ndjson_event(event)
    except asyncio.CancelledError:
        raise
    finally:
        if pending is not None:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await events.aclose()
        _solve_user_api_key.reset(token_key)
        _solve_usage.reset(token_usage)
        await _record_usage(user_api_key, usage, user_id)
        # Settle only after the durable usage record: while the reservation is
        # held, concurrent solves cannot pass the quota check on stale usage.
        await _settle_solve_quota(quota_reservation, usage.total_tokens)


async def _require_solve_capacity() -> None:
    """Reject at the HTTP boundary while a real status code can still be sent.

    Once StreamingResponse starts, headers are committed and the only way to
    signal overload is an in-band error event the client must special-case.
    This is an advisory pre-check; ``stream_solve_events`` still owns the
    authoritative slot acquisition.
    """
    if await get_state_backend().capacity.in_flight() >= max_concurrent_solves():
        raise HTTPException(status_code=429, detail="SERVER_BUSY")


@router.post("/solve/stream")
async def solve_stream(request: Request) -> StreamingResponse:
    require_http_app_access(request)
    user = require_auth_user(request)
    msg = await _read_solve_json_body(request)
    try:
        resolve_solve_mode(msg)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await _require_solve_capacity()

    user_api_key, quota_reservation = await _begin_solve_quota(user.user_id)
    usage = _UsageAccumulator()

    return StreamingResponse(
        _ndjson_solve_stream(
            msg,
            request,
            user_id=user.user_id,
            user_api_key=user_api_key,
            usage=usage,
            quota_reservation=quota_reservation,
        ),
        media_type="application/x-ndjson",
    )


@router.post("/solve/{session_id}/interrupt")
async def interrupt_solve(session_id: str, request: Request) -> JSONResponse:
    require_http_app_access(request)
    user = require_auth_user(request)
    if not active_solve_tasks.cancel(session_id, user_id=user.user_id):
        raise HTTPException(status_code=404, detail="Active solve not found.")
    return JSONResponse({"ok": True, "session_id": session_id})


@router.get("/solve/{session_id}/status")
async def solve_session_status(session_id: str, request: Request) -> JSONResponse:
    """Return whether a solve is still running after disconnect."""
    require_http_app_access(request)
    user = require_auth_user(request)
    live = active_solve_tasks.status(session_id, user_id=user.user_id)
    checkpoint = _project_store(user.user_id).get_checkpoint(session_id)
    if live is None and checkpoint is None:
        raise HTTPException(status_code=404, detail="Solve session not found.")
    pending = checkpoint.get("pending_interaction") if isinstance(checkpoint, dict) else None
    return JSONResponse(
        {
            "ok": True,
            "session_id": session_id,
            "active": bool(live and live.get("active")),
            "mode": (live or {}).get("mode")
            or (
                "research"
                if isinstance(checkpoint, dict) and checkpoint.get("strategy") == "research"
                else "react"
            ),
            "waiting_human": isinstance(pending, dict),
            "has_checkpoint": checkpoint is not None,
            "resumable": checkpoint is not None,
            "has_trace": trace_exists(user.user_id, session_id),
        }
    )


@router.get("/solve/{session_id}/trace")
async def solve_session_trace(session_id: str, request: Request) -> JSONResponse:
    """Return the persisted intermediate events of a solve for replay."""
    require_http_app_access(request)
    user = require_auth_user(request)
    if not SESSION_ID_RE.match(session_id or ""):
        raise HTTPException(status_code=400, detail="Invalid session id.")
    events = await asyncio.to_thread(read_trace, user.user_id, session_id)
    if not events:
        # Shared-project solves persist under the knowledge tenant's root;
        # resolve it through the checkpoint's project context.
        checkpoint = _project_store(user.user_id).get_checkpoint(session_id)
        context = checkpoint.get("project_context") if isinstance(checkpoint, dict) else None
        tenant = str(context.get("user_id") or "").strip() if isinstance(context, dict) else ""
        if tenant and tenant != user.user_id:
            events = await asyncio.to_thread(read_trace, tenant, session_id)
    if not events:
        raise HTTPException(status_code=404, detail="Solve trace not found.")
    return JSONResponse({"ok": True, "session_id": session_id, "events": events})


@router.post("/solve/{session_id}/decisions/stream")
async def resume_solve_with_human_decision(
    session_id: str, request: Request
) -> StreamingResponse:
    """Validate and claim a pending HITL request, then resume it as a new stream."""
    require_http_app_access(request)
    user = require_auth_user(request)
    payload = await _read_solve_json_body(request)
    checkpoint = _project_store(user.user_id).get_checkpoint(session_id)
    if not checkpoint:
        raise HTTPException(status_code=404, detail="Checkpoint not found.")
    pending = checkpoint.get("pending_interaction")
    if not isinstance(pending, dict):
        raise HTTPException(status_code=409, detail="Checkpoint is not waiting for human input.")
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id or request_id != str(pending.get("request_id") or ""):
        raise HTTPException(status_code=409, detail="Human-input request is stale or mismatched.")
    decision = str(payload.get("decision") or "").strip().lower()
    allowed = pending.get("allowed_decisions") or []
    if decision not in {"approve", "reject", "edit", "respond"} or decision not in allowed:
        raise HTTPException(status_code=400, detail="Invalid human decision.")

    human_decision = {
        "request_id": request_id,
        "decision": decision,
        "feedback": str(payload.get("feedback") or "")[:8000],
        **(
            {"edited_action": payload["edited_action"]}
            if isinstance(payload.get("edited_action"), dict)
            else {}
        ),
    }
    claimed = _project_store(user.user_id).claim_human_decision(
        session_id, human_decision
    )
    if claimed is None:
        raise HTTPException(
            status_code=409,
            detail="This human-input request was already decided or is no longer pending.",
        )
    hitl_auto_resolve.cancel_auto_resolve(session_id, request_id)

    msg = {
        "problem": "",
        "checkpoint_id": session_id,
        "project_id": str(checkpoint.get("project_id") or "default"),
        "mode": (
            "research" if checkpoint.get("strategy") == "research" else "react"
        ),
        "human_decision": human_decision,
    }

    user_api_key, quota_reservation = await _begin_solve_quota(user.user_id)
    usage = _UsageAccumulator()

    return StreamingResponse(
        _ndjson_solve_stream(
            msg,
            request,
            user_id=user.user_id,
            user_api_key=user_api_key,
            usage=usage,
            quota_reservation=quota_reservation,
        ),
        media_type="application/x-ndjson",
    )


@router.post("/solve/{session_id}/goals/{goal_id}/actions")
async def apply_solve_goal_action(
    session_id: str, goal_id: str, request: Request
) -> StreamingResponse:
    """Validate and claim a node-level goal action, then resume it as a new stream."""
    require_http_app_access(request)
    user = require_auth_user(request)
    payload = await _read_solve_json_body(request)
    store = _project_store(user.user_id)
    checkpoint = store.get_checkpoint(session_id)
    if not checkpoint:
        raise HTTPException(status_code=404, detail="Checkpoint not found.")
    action = str(payload.get("action") or "").strip().lower()
    if action not in {"retry", "edit"}:
        raise HTTPException(status_code=400, detail="Invalid goal action.")
    statement = str(payload.get("statement") or "")[:8000].strip()
    if action == "edit" and not statement:
        raise HTTPException(
            status_code=400, detail="Edit actions require a non-empty statement."
        )
    proof_graph = checkpoint.get("proof_graph")
    raw_goals = proof_graph.get("goals") if isinstance(proof_graph, dict) else None
    goal_ids = {
        str(goal.get("id") or "")
        for goal in raw_goals or []
        if isinstance(goal, dict)
    }
    if goal_id not in goal_ids:
        raise HTTPException(
            status_code=400, detail="Goal is not part of the checkpoint's proof graph."
        )
    live = active_solve_tasks.status(session_id, user_id=user.user_id)
    if live and live.get("active"):
        raise HTTPException(
            status_code=409,
            detail="Session has an active run; interrupt it before applying goal actions.",
        )

    guidance = str(payload.get("guidance") or "")[:8000].strip()
    goal_action = {
        "goal_id": goal_id,
        "action": action,
        **({"statement": statement} if statement else {}),
        **({"guidance": guidance} if guidance else {}),
    }
    claimed = store.claim_goal_action(session_id, goal_action)
    if claimed is None:
        raise HTTPException(
            status_code=409,
            detail="A goal action was already claimed for this checkpoint.",
        )

    msg = {
        "problem": "",
        "checkpoint_id": session_id,
        "project_id": str(checkpoint.get("project_id") or "default"),
        "mode": (
            "research" if checkpoint.get("strategy") == "research" else "react"
        ),
        "goal_action": goal_action,
    }

    user_api_key, quota_reservation = await _begin_solve_quota(user.user_id)
    usage = _UsageAccumulator()

    return StreamingResponse(
        _ndjson_solve_stream(
            msg,
            request,
            user_id=user.user_id,
            user_api_key=user_api_key,
            usage=usage,
            quota_reservation=quota_reservation,
        ),
        media_type="application/x-ndjson",
    )


@router.post("/lean/jobs")
async def create_lean_job(payload: dict[str, Any], request: Request):
    require_http_app_access(request)
    require_auth_user(request)
    code = (payload.get("code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Lean code is required.")
    job = lean_jobs.create(code, config=load_config().lean)
    return {"ok": True, "job": job.to_dict()}


@router.get("/lean/jobs/{job_id}")
async def get_lean_job(job_id: str, request: Request):
    require_http_app_access(request)
    require_auth_user(request)
    job = lean_jobs.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Lean job not found.")
    return {"ok": True, "job": job.to_dict()}


def _user_owns_figure_session(user_id: str, session_id: str) -> bool:
    """True when the session is an active solve or a checkpoint for this user."""
    if active_solve_tasks.contains(session_id, user_id=user_id):
        return True
    checkpoint = _project_store(user_id).get_checkpoint(session_id)
    return isinstance(checkpoint, dict)


@router.get("/solve/figures/{session_id}/{filename}")
async def get_solve_figure(session_id: str, filename: str, request: Request):
    """Serve a PNG generated by the plot_figure tool for one solve session."""
    require_http_app_access(request)
    user = require_auth_user(request)
    session_component = _safe_component(session_id)
    file_component = _safe_component(filename)
    if (
        session_component != session_id
        or file_component != filename
        or not file_component.endswith(".png")
    ):
        raise HTTPException(status_code=404, detail="Figure not found.")
    if not _user_owns_figure_session(user.user_id, session_id):
        raise HTTPException(status_code=404, detail="Figure not found.")
    artifact_root = Path(load_config().agent.artifact_root).resolve()
    path = (artifact_root / session_component / "figures" / file_component).resolve()
    if not path.is_relative_to(artifact_root) or not path.is_file():
        raise HTTPException(status_code=404, detail="Figure not found.")
    return FileResponse(path, media_type="image/png")
