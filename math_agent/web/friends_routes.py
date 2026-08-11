"""HTTP routes for friends and profile display name."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from math_agent.knowledge.supabase_client import service_role_configured
from math_agent.web.friends import CLOUD_STORAGE_REQUIRED, FriendsService, require_friends_cloud
from math_agent.web.security import require_auth_user
from math_agent.web.user_store import UserStore

log = logging.getLogger("math_agent.web.friends_routes")
router = APIRouter(prefix="/api", tags=["friends"])


def _friends_service(request: Request) -> FriendsService:
    user = require_auth_user(request)
    client = require_friends_cloud()
    return FriendsService(user_id=user.user_id, client=client)


async def _run_friends(op_name: str, fn):
    try:
        return await asyncio.to_thread(fn)
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Friends %s failed", op_name)
        raise HTTPException(
            status_code=502,
            detail="好友服务暂时不可用，请稍后重试。",
        ) from exc


@router.get("/me/profile")
async def get_my_profile(request: Request):
    user = require_auth_user(request)
    if not service_role_configured():
        return {
            "ok": True,
            "profile": {
                "user_id": user.user_id,
                "display_name": "",
                "phone_masked": "",
                "phone": user.phone,
            },
        }
    store = UserStore()
    profile = await asyncio.to_thread(store.get_profile, user.user_id) or {}
    return {
        "ok": True,
        "profile": {
            "user_id": user.user_id,
            "display_name": str(profile.get("display_name") or ""),
            "phone_masked": str(profile.get("phone_masked") or ""),
            "phone": user.phone,
        },
    }


@router.patch("/me/profile")
async def update_my_profile(payload: dict[str, Any], request: Request):
    user = require_auth_user(request)
    if not service_role_configured():
        raise HTTPException(
            status_code=503,
            detail=CLOUD_STORAGE_REQUIRED,
        )
    if "display_name" not in payload:
        raise HTTPException(status_code=400, detail="display_name is required.")
    store = UserStore()
    try:
        updated = await asyncio.to_thread(
            store.update_display_name, user.user_id, str(payload.get("display_name") or "")
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "ok": True,
        "profile": {
            "user_id": user.user_id,
            "display_name": str(updated.get("display_name") or ""),
            "phone_masked": str(updated.get("phone_masked") or ""),
        },
    }


@router.get("/friends")
async def list_friends(request: Request):
    svc = _friends_service(request)
    return {"ok": True, "friends": await _run_friends("list_friends", svc.list_friends)}


@router.get("/friends/requests")
async def list_friend_requests(request: Request):
    svc = _friends_service(request)
    return {"ok": True, **await _run_friends("list_requests", svc.list_requests)}


@router.post("/friends/request")
async def request_friend(payload: dict[str, Any], request: Request):
    svc = _friends_service(request)
    user_id = payload.get("user_id")
    phone = payload.get("phone")
    result = await _run_friends(
        "request_friend",
        lambda: svc.request_friend(
            user_id=str(user_id) if user_id else None,
            phone=str(phone) if phone else None,
        ),
    )
    return {"ok": True, **result}


@router.post("/friends/requests/{friendship_id}/accept")
async def accept_friend_request(friendship_id: str, request: Request):
    svc = _friends_service(request)
    result = await _run_friends("accept_request", lambda: svc.accept_request(friendship_id))
    return {"ok": True, **result}


@router.post("/friends/requests/{friendship_id}/decline")
async def decline_friend_request(friendship_id: str, request: Request):
    svc = _friends_service(request)
    result = await _run_friends("decline_request", lambda: svc.decline_request(friendship_id))
    return {"ok": True, **result}


@router.delete("/friends/{other_user_id}")
async def unfriend(other_user_id: str, request: Request):
    svc = _friends_service(request)
    result = await _run_friends("unfriend", lambda: svc.unfriend(other_user_id))
    return {"ok": True, **result}
