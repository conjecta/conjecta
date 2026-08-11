"""HTTP routes for research runs, inbox, and share links."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from math_agent.agent.research_artifacts import ResearchArtifactStore
from math_agent.config import load_config
from math_agent.web.active_solves import active_solve_tasks
from math_agent.web.agent_factory import _project_store
from math_agent.web.project_store import ProjectStore, project_store_for_user
from math_agent.web.research_runs import (
    ShareStore,
    artifact_path_for,
    inbox_entry,
    is_research_checkpoint,
    run_detail,
    summarize_run,
)
from math_agent.web.security import require_auth_user, require_http_app_access

router = APIRouter(prefix="/api", tags=["research"])

_share_store_instance: ShareStore | None = None


def _share_store() -> ShareStore:
    global _share_store_instance
    if _share_store_instance is None:
        _share_store_instance = ShareStore()
    return _share_store_instance


def _research_checkpoint_or_404(store: ProjectStore, session_id: str) -> dict[str, Any]:
    checkpoint = store.get_checkpoint(session_id)
    if not is_research_checkpoint(checkpoint):
        raise HTTPException(status_code=404, detail="Research run not found.")
    assert checkpoint is not None
    return checkpoint


def _is_active(session_id: str, user_id: str) -> bool:
    live = active_solve_tasks.status(session_id, user_id=user_id)
    return bool(live and live.get("active"))


@router.get("/research/runs")
async def list_research_runs(request: Request) -> dict[str, Any]:
    require_http_app_access(request)
    user = require_auth_user(request)
    store = _project_store(user.user_id)
    runs = [
        summarize_run(checkpoint, active=_is_active(str(checkpoint.get("session_id") or ""), user.user_id))
        for checkpoint in store.list_checkpoints()
        if is_research_checkpoint(checkpoint)
    ]
    return {"ok": True, "runs": runs}


@router.get("/research/runs/{session_id}")
async def get_research_run(session_id: str, request: Request) -> dict[str, Any]:
    require_http_app_access(request)
    user = require_auth_user(request)
    checkpoint = _research_checkpoint_or_404(_project_store(user.user_id), session_id)
    return {"ok": True, "run": run_detail(checkpoint, active=_is_active(session_id, user.user_id))}


@router.get("/research/runs/{session_id}/artifacts/{artifact_id}")
async def get_research_artifact(session_id: str, artifact_id: str, request: Request) -> dict[str, Any]:
    require_http_app_access(request)
    user = require_auth_user(request)
    checkpoint = _research_checkpoint_or_404(_project_store(user.user_id), session_id)
    path = artifact_path_for(
        checkpoint, artifact_id, artifact_root=load_config().agent.artifact_root
    )
    payload = ResearchArtifactStore.read(path) if path is not None else None
    if payload is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return {"ok": True, "artifact": payload}


@router.get("/inbox")
async def list_inbox(request: Request) -> dict[str, Any]:
    require_http_app_access(request)
    user = require_auth_user(request)
    store = _project_store(user.user_id)
    items = []
    for checkpoint in store.list_checkpoints():
        if not is_research_checkpoint(checkpoint):
            continue
        entry = inbox_entry(checkpoint)
        if entry is not None:
            items.append(entry)
    return {"ok": True, "items": items}


@router.post("/research/runs/{session_id}/share")
async def create_research_share(session_id: str, request: Request) -> dict[str, Any]:
    require_http_app_access(request)
    user = require_auth_user(request)
    _research_checkpoint_or_404(_project_store(user.user_id), session_id)
    record = _share_store().create(user_id=user.user_id, session_id=session_id)
    return {"ok": True, "token": record["token"], "url": f"/share/research/{record['token']}"}


@router.delete("/share/research/{token}")
async def revoke_research_share(token: str, request: Request) -> dict[str, Any]:
    require_http_app_access(request)
    user = require_auth_user(request)
    if not _share_store().revoke(token, user_id=user.user_id):
        raise HTTPException(status_code=404, detail="Share link not found.")
    return {"ok": True}


@router.get("/share/research/{token}")
async def get_shared_research_run(token: str) -> dict[str, Any]:
    record = _share_store().get(token)
    if record is None:
        raise HTTPException(status_code=404, detail="Share link not found.")
    checkpoint = project_store_for_user(str(record["user_id"])).get_checkpoint(
        str(record["session_id"])
    )
    if not is_research_checkpoint(checkpoint):
        raise HTTPException(status_code=404, detail="Research run not found.")
    assert checkpoint is not None
    active = _is_active(str(record["session_id"]), str(record["user_id"]))
    return {"ok": True, "run": run_detail(checkpoint, active=active)}


@router.get("/share/research/{token}/artifacts/{artifact_id}")
async def get_shared_research_artifact(token: str, artifact_id: str) -> dict[str, Any]:
    record = _share_store().get(token)
    if record is None:
        raise HTTPException(status_code=404, detail="Share link not found.")
    checkpoint = project_store_for_user(str(record["user_id"])).get_checkpoint(
        str(record["session_id"])
    )
    if not is_research_checkpoint(checkpoint):
        raise HTTPException(status_code=404, detail="Research run not found.")
    assert checkpoint is not None
    path = artifact_path_for(
        checkpoint, artifact_id, artifact_root=load_config().agent.artifact_root
    )
    payload = ResearchArtifactStore.read(path) if path is not None else None
    if payload is None:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return {"ok": True, "artifact": payload}
