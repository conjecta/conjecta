"""HTTP routes for contact/feedback intake and admin dashboards."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from math_agent.config import load_config
from math_agent.knowledge.supabase_client import service_role_configured
from math_agent.web.contact import ContactStore, normalize_contact_payload
from math_agent.web.feedback import (
    CLOUD_STORAGE_REQUIRED,
    enrich_feedback_rows,
    feedback_store_or_none,
    normalize_feedback_payload,
)
from math_agent.web.operations import OperationsStore
from math_agent.web.security import require_admin_user, require_auth_user
from math_agent.web.user_store import UserStore

web_log = logging.getLogger("math_agent.web")

router = APIRouter(prefix="/api", tags=["admin"])


@router.get("/admin/overview")
async def admin_overview(request: Request, days: int = 30, limit: int = 100):
    require_admin_user(request)
    if not service_role_configured():
        raise HTTPException(
            status_code=503,
            detail="Operations storage is not configured.",
        )
    try:
        users = await asyncio.to_thread(UserStore().list_users, limit=1000)
        data = await asyncio.to_thread(
            OperationsStore().dashboard,
            users=users,
            days=days,
            limit=limit,
        )
    except Exception as exc:
        web_log.exception("Failed to build admin overview")
        raise HTTPException(status_code=502, detail="Operations data is unavailable.") from exc
    return {"ok": True, **data}


@router.post("/contact")
async def submit_contact(payload: dict[str, Any], request: Request):
    """Public contact form from the marketing homepage."""
    data = normalize_contact_payload(payload if isinstance(payload, dict) else {})
    config = load_config()
    log_dir = Path(config.logging.dir or "logs")
    store = ContactStore(log_dir / "contact_messages.jsonl")
    row = await asyncio.to_thread(store.add, data)
    web_log.info(
        "Contact message received id=%s email=%s name=%s",
        row.get("id"),
        row.get("email"),
        row.get("name"),
    )
    return {"ok": True, "id": row["id"]}


@router.post("/feedback")
async def submit_feedback(payload: dict[str, Any], request: Request):
    user = require_auth_user(request)
    store = feedback_store_or_none()
    if store is None:
        raise HTTPException(status_code=503, detail=CLOUD_STORAGE_REQUIRED)
    data = normalize_feedback_payload(payload if isinstance(payload, dict) else {})
    row = await asyncio.to_thread(store.upsert, user.user_id, **data)
    return {"ok": True, "feedback": row}


@router.get("/admin/feedback")
async def admin_list_feedback(
    request: Request, limit: int = 100, rating: str | None = None
):
    require_admin_user(request)
    store = feedback_store_or_none()
    if store is None:
        raise HTTPException(status_code=503, detail=CLOUD_STORAGE_REQUIRED)
    rows = await asyncio.to_thread(store.list_feedback, limit=limit, rating=rating)
    profiles: dict[str, dict[str, Any]] = {}
    user_store = UserStore() if service_role_configured() else None
    for row in rows:
        uid = str(row.get("user_id") or "")
        if not uid or uid in profiles or user_store is None:
            continue
        profiles[uid] = await asyncio.to_thread(user_store.get_profile, uid) or {}
    return {"ok": True, "feedback": enrich_feedback_rows(rows, profiles)}
