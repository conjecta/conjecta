from __future__ import annotations

import asyncio
import hmac
import logging
import os
import re
from datetime import date as date_cls, datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel

from math_agent.billing.api_keys import decrypt_api_key, encrypt_api_key
from math_agent.billing.quota import free_tokens_per_day, is_quota_unlimited, remaining_tokens
from math_agent.billing.usage_store import UsageStore, _today_utc
from math_agent.web.security import _bearer_token, require_auth_user

log = logging.getLogger("math_agent.web.billing_routes")
router = APIRouter(prefix="/api", tags=["billing"])


@router.get("/me/usage")
async def me_usage(request: Request):
    user = require_auth_user(request)
    store = UsageStore()
    today_date = _today_utc()
    today = await asyncio.to_thread(store.daily_usage, user.user_id, today_date)
    month = await asyncio.to_thread(store.monthly_summary, user.user_id, today_date.year, today_date.month)
    unlimited = is_quota_unlimited(user_id=user.user_id, phone=getattr(user, "phone", None))
    quota = 0 if unlimited else free_tokens_per_day()
    return {
        "ok": True,
        "unlimited_quota": unlimited,
        "today": {
            "prompt_tokens": today.get("prompt_tokens", 0),
            "completion_tokens": today.get("completion_tokens", 0),
            "total_tokens": today.get("total_tokens", 0),
            "cost_usd": float(today.get("cost_usd", 0) or 0),
            "quota_tokens": quota,
            "remaining_tokens": remaining_tokens(
                today.get("total_tokens", 0),
                user_id=user.user_id,
                phone=getattr(user, "phone", None),
            ),
        },
        "this_month": {
            "prompt_tokens": month["prompt_tokens"],
            "completion_tokens": month["completion_tokens"],
            "total_tokens": month["total_tokens"],
            "cost_usd": month["cost_usd"],
        },
    }


@router.get("/me/api-key")
async def me_api_key(request: Request):
    user = require_auth_user(request)
    from math_agent.knowledge.supabase_client import create_supabase_client

    client = create_supabase_client(prefer_service_role=True)

    def _fetch():
        return (
            client.table("conjecta_users")
            .select("api_keys_encrypted,api_keys_updated_at")
            .eq("id", user.user_id)
            .limit(1)
            .execute()
        )

    try:
        resp = await asyncio.to_thread(_fetch)
    except Exception as exc:
        log.exception("Failed to fetch api key for %s", user.user_id)
        raise HTTPException(status_code=503, detail="Database error") from exc
    data = (resp.data or [None])[0]
    if not data or not data.get("api_keys_encrypted"):
        return {"ok": True, "api_key": None}
    try:
        stored = decrypt_api_key(data["api_keys_encrypted"])
    except Exception as exc:
        log.exception("Failed to decrypt api key for %s", user.user_id)
        raise HTTPException(status_code=500, detail="Stored API key is corrupted") from exc
    return {
        "ok": True,
        "api_key": {
            "provider": stored.provider,
            "updated_at": data.get("api_keys_updated_at"),
        },
    }


class ApiKeyPayload(BaseModel):
    provider: str
    api_key: str


@router.post("/me/api-key")
async def set_me_api_key(request: Request, payload: ApiKeyPayload):
    user = require_auth_user(request)
    provider = str(payload.provider or "").strip()
    api_key = str(payload.api_key or "").strip()
    if provider != "openai":
        raise HTTPException(status_code=400, detail="Unsupported provider.")
    if not api_key:
        raise HTTPException(status_code=400, detail="api_key is required.")
    from math_agent.knowledge.supabase_client import create_supabase_client

    try:
        encrypted = encrypt_api_key(provider, api_key)
    except (RuntimeError, ValueError) as exc:
        log.warning("API key encryption not configured: %s", exc)
        raise HTTPException(status_code=503, detail="API key encryption is not configured.") from exc
    client = create_supabase_client(prefer_service_role=True)
    now = datetime.now(timezone.utc).isoformat()

    def _store():
        client.table("conjecta_users").update(
            {"api_keys_encrypted": encrypted, "api_keys_updated_at": now}
        ).eq("id", user.user_id).execute()

    try:
        await asyncio.to_thread(_store)
    except Exception as exc:
        log.exception("Failed to store api key for %s", user.user_id)
        raise HTTPException(status_code=503, detail="Database error") from exc
    return {"ok": True, "provider": provider, "updated_at": now}


@router.delete("/me/api-key")
async def delete_me_api_key(request: Request):
    user = require_auth_user(request)
    from math_agent.knowledge.supabase_client import create_supabase_client

    client = create_supabase_client(prefer_service_role=True)

    def _clear():
        client.table("conjecta_users").update(
            {"api_keys_encrypted": None, "api_keys_updated_at": None}
        ).eq("id", user.user_id).execute()

    try:
        await asyncio.to_thread(_clear)
    except Exception as exc:
        log.exception("Failed to clear api key for %s", user.user_id)
        raise HTTPException(status_code=503, detail="Database error") from exc
    return {"ok": True}


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@router.get("/admin/usage")
async def admin_usage(
    request: Request,
    authorization: str | None = Header(default=None),
    date: str | None = None,
    limit: int = 100,
    offset: int = 0,
):
    expected = os.getenv("CONJECTA_ADMIN_TOKEN", "").strip()
    supplied = _bearer_token(authorization)
    if (
        not expected
        or not supplied
        or len(supplied) != len(expected)
        or not hmac.compare_digest(supplied, expected)
    ):
        raise HTTPException(status_code=401, detail="Admin token required.")

    target = date or _today_utc().isoformat()
    if date is not None:
        if not _DATE_RE.match(date):
            raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD.")
        try:
            date_cls.fromisoformat(date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="date is not a valid calendar date.") from exc
    store = UsageStore()
    client = store.client
    try:
        resp = (
            client.table("conjecta_usage_daily")
            .select("user_id,prompt_tokens,completion_tokens,total_tokens,estimated_cost_usd,conjecta_users(phone_masked)")
            .eq("date", target)
            .order("total_tokens", desc=True)
            .limit(min(max(limit, 1), 1000))
            .offset(max(offset, 0))
            .execute()
        )
        # Global day total (not page-local): cost column only, no user join.
        cost_resp = (
            client.table("conjecta_usage_daily")
            .select("estimated_cost_usd")
            .eq("date", target)
            .execute()
        )
    except Exception as exc:
        log.exception("Failed to fetch admin usage for date=%s", target)
        raise HTTPException(status_code=503, detail="Database error") from exc
    rows = resp.data or []
    users = []
    for r in rows:
        cost = float(r.get("estimated_cost_usd", 0) or 0)
        user_info = r.get("conjecta_users") or {}
        users.append(
            {
                "user_id": r["user_id"],
                "phone_masked": user_info.get("phone_masked", ""),
                "prompt_tokens": r.get("prompt_tokens", 0),
                "completion_tokens": r.get("completion_tokens", 0),
                "total_tokens": r.get("total_tokens", 0),
                "cost_usd": cost,
            }
        )
    total_cost = sum(
        float(r.get("estimated_cost_usd", 0) or 0) for r in (cost_resp.data or [])
    )
    return {"ok": True, "date": target, "total_cost_usd": total_cost, "users": users}
