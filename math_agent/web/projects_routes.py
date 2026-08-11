"""HTTP routes for project CRUD, starring, members, and conversations."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from math_agent.web.agent_factory import (
    _project_access_from_request,
    _project_store,
    _tenant_project_store,
)
from math_agent.web.project_access import project_access_service_or_none
from math_agent.web.project_repository import project_repository_or_none

web_log = logging.getLogger("math_agent.web")

router = APIRouter(prefix="/api/projects", tags=["projects"])


@router.get("")
async def list_projects(request: Request):
    user, store = _tenant_project_store(request)
    access_svc = project_access_service_or_none()
    if access_svc is not None:
        try:
            return {"ok": True, "projects": await asyncio.to_thread(access_svc.list_accessible_projects, user.user_id)}
        except Exception as exc:
            web_log.warning("Accessible project list failed, falling back: %s", exc)
    repo = project_repository_or_none()
    if repo is not None:
        try:
            projects = await asyncio.to_thread(repo.list_projects, user.user_id)
            for item in projects:
                item.setdefault("owner_user_id", user.user_id)
                item.setdefault("role", "lead")
            return {"ok": True, "projects": projects}
        except Exception as exc:
            web_log.warning("Supabase project list failed, falling back to local: %s", exc)
    projects = await asyncio.to_thread(store.list_projects)
    for item in projects:
        item.setdefault("owner_user_id", user.user_id)
        item.setdefault("role", "lead")
    return {"ok": True, "projects": projects}


@router.get("/{project_id}")
async def get_project(project_id: str, request: Request, owner_user_id: str | None = None):
    user, access = await asyncio.to_thread(_project_access_from_request, request, project_id, owner_user_id=owner_user_id)
    store = _project_store(access.knowledge_tenant_id)
    repo = project_repository_or_none()
    if repo is not None:
        try:
            data = await asyncio.to_thread(repo.get_project, access.owner_user_id, project_id)
            if data is not None:
                try:
                    local = await asyncio.to_thread(store.get_project, project_id)
                    data = {**local, **data, "project": {**local.get("project", {}), **data.get("project", {})}}
                except HTTPException:
                    pass
                data["owner_user_id"] = access.owner_user_id
                data["role"] = access.role
                return {"ok": True, **data}
        except HTTPException:
            raise
        except Exception as exc:
            web_log.warning("Supabase get_project failed: %s", exc)
    try:
        data = await asyncio.to_thread(store.get_project, project_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="Project not found.")
    data["owner_user_id"] = access.owner_user_id
    data["role"] = access.role
    return {"ok": True, **data}


@router.put("/{project_id}")
async def save_project(
    project_id: str,
    payload: dict[str, Any],
    request: Request,
    owner_user_id: str | None = None,
):
    user, access = await asyncio.to_thread(_project_access_from_request, request, project_id, owner_user_id=owner_user_id, create_if_missing=True)
    if access.role != "lead":
        # Collaborators may edit knowledge but not project metadata ownership payload.
        raise HTTPException(status_code=404, detail="Project not found.")
    store = _project_store(access.knowledge_tenant_id)
    project = payload.get("project") if isinstance(payload.get("project"), dict) else payload
    data = await asyncio.to_thread(store.save_project, project_id, project)
    repo = project_repository_or_none()
    if repo is not None:
        try:
            cloud = await asyncio.to_thread(repo.upsert_project, access.owner_user_id, project_id, data.get("project") or project)
            data = {**data, **cloud}
        except Exception as exc:
            web_log.warning("Supabase project upsert failed: %s", exc)
    data["owner_user_id"] = access.owner_user_id
    data["role"] = access.role
    return {"ok": True, **data}


@router.post("/{project_id}/star")
async def star_project(
    project_id: str,
    payload: dict[str, Any],
    request: Request,
    owner_user_id: str | None = None,
):
    user, access = await asyncio.to_thread(_project_access_from_request, request, project_id, owner_user_id=owner_user_id)
    if access.role != "lead":
        raise HTTPException(status_code=404, detail="Project not found.")
    store = _project_store(access.knowledge_tenant_id)
    starred = bool(payload.get("starred"))
    data = await asyncio.to_thread(store.set_starred, project_id, starred)
    repo = project_repository_or_none()
    if repo is not None:
        try:
            cloud = await asyncio.to_thread(repo.set_starred, access.owner_user_id, project_id, starred)
            data = {**data, **cloud}
        except HTTPException:
            raise
        except Exception as exc:
            web_log.warning("Supabase star failed: %s", exc)
    return {"ok": True, **data}


@router.get("/{project_id}/members")
async def list_project_members(
    project_id: str, request: Request, owner_user_id: str | None = None
):
    _, access = await asyncio.to_thread(_project_access_from_request, request, project_id, owner_user_id=owner_user_id)
    svc = project_access_service_or_none()
    if svc is None:
        # Solo / local: only the lead is a member.
        return {
            "ok": True,
            "members": [
                {
                    "user_id": access.owner_user_id,
                    "role": "lead",
                    "display_name": "",
                    "phone_masked": "",
                    "label": access.owner_user_id,
                    "added_by": access.owner_user_id,
                    "created_at": "",
                }
            ],
        }
    return {"ok": True, "members": await asyncio.to_thread(svc.list_members, access)}


@router.post("/{project_id}/members")
async def add_project_member(
    project_id: str,
    payload: dict[str, Any],
    request: Request,
    owner_user_id: str | None = None,
):
    _, access = await asyncio.to_thread(_project_access_from_request, request, project_id, owner_user_id=owner_user_id)
    svc = project_access_service_or_none()
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="CLOUD_STORAGE_REQUIRED",
        )
    member_user_id = str(payload.get("user_id") or "").strip()
    phone = str(payload.get("phone") or "").strip() or None
    result = await asyncio.to_thread(svc.add_member, access, member_user_id, phone=phone)
    return {"ok": True, **result}


@router.delete("/{project_id}/members/{member_user_id}")
async def remove_project_member(
    project_id: str,
    member_user_id: str,
    request: Request,
    owner_user_id: str | None = None,
):
    _, access = await asyncio.to_thread(_project_access_from_request, request, project_id, owner_user_id=owner_user_id)
    svc = project_access_service_or_none()
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="CLOUD_STORAGE_REQUIRED",
        )
    result = await asyncio.to_thread(svc.remove_member, access, member_user_id)
    return {"ok": True, **result}


@router.delete("/{project_id}/conversations/{conversation_id}")
async def delete_conversation(
    project_id: str,
    conversation_id: str,
    request: Request,
    owner_user_id: str | None = None,
):
    _, access = await asyncio.to_thread(_project_access_from_request, request, project_id, owner_user_id=owner_user_id)
    if access.role != "lead":
        # Conversations live in the lead's tenant; only the lead may delete them.
        raise HTTPException(status_code=404, detail="Project not found.")
    store = _project_store(access.knowledge_tenant_id)
    deleted = await asyncio.to_thread(store.delete_conversation, project_id, conversation_id)
    return {"ok": True, "deleted": deleted}
