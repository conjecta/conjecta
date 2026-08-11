"""Phone OTP login routes (FastAPI + Aliyun Dypns + JWT)."""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from functools import lru_cache
from threading import Lock
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from math_agent.web.dypns_client import DypnsError, check_sms_verify_code, dypns_configured, send_sms_verify_code
from math_agent.web.security import (
    ACCESS_TOKEN_COOKIE,
    _client_host,
    _cookie_token,
    trusted_proxy_header,
)
from math_agent.web.jwt_auth import (
    decode_access_token,
    issue_access_token,
    mask_phone,
    normalize_phone,
    phone_auth_enabled,
)
from math_agent.web.operations import is_admin_phone
from math_agent.web.user_ban import ban_message, is_phone_banned, is_user_banned

log = logging.getLogger("math_agent.web.phone_auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])


@lru_cache(maxsize=1)
def sms_bypass_phones() -> frozenset[str]:
    """Phones that may log in without an SMS verification code."""
    raw = os.getenv("CONJECTA_SMS_BYPASS_PHONES", "").strip()
    if not raw:
        return frozenset()
    phones: set[str] = set()
    for part in raw.replace(";", ",").split(","):
        value = part.strip()
        if not value:
            continue
        try:
            phones.add(normalize_phone(value))
        except ValueError:
            log.warning("Ignoring invalid CONJECTA_SMS_BYPASS_PHONES entry")
    return frozenset(phones)


def is_sms_bypass_phone(phone: str | None) -> bool:
    if not phone:
        return False
    try:
        return normalize_phone(phone) in sms_bypass_phones()
    except ValueError:
        return False


def clear_sms_bypass_cache() -> None:
    sms_bypass_phones.cache_clear()


class SendCodeRequest(BaseModel):
    phone: str = Field(..., min_length=6, max_length=20)


class VerifyCodeRequest(BaseModel):
    phone: str = Field(..., min_length=6, max_length=20)
    code: str = Field(..., min_length=4, max_length=8)
    out_id: str | None = None


@dataclass
class _PendingSend:
    out_id: str
    expires_at: float


@dataclass
class _SendTracker:
    _entries: dict[str, _PendingSend] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def put(self, phone: str, out_id: str, *, ttl_seconds: float = 600.0) -> None:
        with self._lock:
            self._entries[phone] = _PendingSend(out_id=out_id, expires_at=time.monotonic() + ttl_seconds)
            self._purge_locked()

    def get(self, phone: str) -> str | None:
        with self._lock:
            self._purge_locked()
            entry = self._entries.get(phone)
            return entry.out_id if entry else None

    def _purge_locked(self) -> None:
        now = time.monotonic()
        stale = [k for k, v in self._entries.items() if v.expires_at <= now]
        for key in stale:
            del self._entries[key]


_send_tracker = _SendTracker()

MAX_VERIFY_ATTEMPTS = 5
VERIFY_LOCKOUT_SECONDS = 15 * 60
SEND_COOLDOWN_SECONDS = 60
# Per-IP budget: allow switching phones without blocking every other number for 60s.
SEND_IP_WINDOW_SECONDS = 60
SEND_IP_MAX_PER_WINDOW = 10


@dataclass
class _VerifyState:
    failures: int = 0
    locked_until: float = 0.0


@dataclass
class _VerifyTracker:
    _entries: dict[str, _VerifyState] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def is_locked(self, phone: str, *, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            state = self._entries.get(phone)
            if state is None:
                return False
            if state.locked_until > now:
                return True
            if state.failures >= MAX_VERIFY_ATTEMPTS:
                # Auto-renew lockout on expiry until a successful login clears it.
                state.locked_until = now + VERIFY_LOCKOUT_SECONDS
                return True
            return False

    def record_failure(self, phone: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            state = self._entries.setdefault(phone, _VerifyState())
            state.failures += 1
            if state.failures >= MAX_VERIFY_ATTEMPTS:
                state.locked_until = now + VERIFY_LOCKOUT_SECONDS

    def record_success(self, phone: str) -> None:
        with self._lock:
            self._entries.pop(phone, None)


@dataclass
class _SendFrequencyTracker:
    """Phone: one send per cooldown. IP: bounded burst to stop SMS bombing."""

    _phone_last: dict[str, float] = field(default_factory=dict)
    _ip_hits: dict[str, list[float]] = field(default_factory=dict)
    _lock: Lock = field(default_factory=Lock)

    def check_and_record_phone(self, phone: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            last = self._phone_last.get(phone, 0.0)
            if last > now - SEND_COOLDOWN_SECONDS:
                raise HTTPException(
                    status_code=429,
                    detail="验证码发送过于频繁，请约 1 分钟后再试。",
                )
            self._phone_last[phone] = now
            stale = [k for k, ts in self._phone_last.items() if ts <= now - SEND_COOLDOWN_SECONDS * 2]
            for key in stale:
                del self._phone_last[key]

    def check_and_record_ip(self, host: str, *, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = [ts for ts in self._ip_hits.get(host, []) if ts > now - SEND_IP_WINDOW_SECONDS]
            if len(hits) >= SEND_IP_MAX_PER_WINDOW:
                raise HTTPException(
                    status_code=429,
                    detail="当前网络发送验证码过于频繁，请稍后再试。",
                )
            hits.append(now)
            self._ip_hits[host] = hits
            stale_hosts = [
                key
                for key, values in self._ip_hits.items()
                if not values or values[-1] <= now - SEND_IP_WINDOW_SECONDS * 2
            ]
            for key in stale_hosts:
                del self._ip_hits[key]


_verify_tracker = _VerifyTracker()
_send_frequency_tracker = _SendFrequencyTracker()


def auth_public_config() -> dict[str, Any]:
    from math_agent.knowledge.supabase_client import service_role_configured

    return {
        "phone_auth_enabled": phone_auth_enabled(),
        "sms_configured": dypns_configured(),
        "cloud_storage_configured": service_role_configured(),
    }


@router.get("/config")
async def auth_config() -> dict[str, Any]:
    return {"ok": True, **auth_public_config()}


@router.post("/send-code", response_model=None)
async def send_code(payload: SendCodeRequest, request: Request):
    if not phone_auth_enabled():
        raise HTTPException(status_code=503, detail="Phone auth is not enabled (set CONJECTA_JWT_SECRET).")
    try:
        phone = normalize_phone(payload.phone)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid phone number.") from None
    if is_phone_banned(phone):
        raise HTTPException(status_code=403, detail=ban_message())
    if is_sms_bypass_phone(phone):
        log.info("SMS bypass login via send-code for %s", mask_phone(phone))
        return _login_success_response(phone, request, sms_bypass=True)
    if not dypns_configured():
        raise HTTPException(status_code=503, detail="Aliyun Dypns is not configured.")

    host = _client_host(request.headers, getattr(request, "client", None))
    _send_frequency_tracker.check_and_record_phone(phone)
    _send_frequency_tracker.check_and_record_ip(host)

    try:
        result = await send_sms_verify_code(phone)
    except DypnsError as exc:
        log.warning("send-code failed: %s", exc)
        raise HTTPException(status_code=502, detail=f"短信发送失败：{exc}") from exc
    out_id = str(result.get("out_id") or "")
    if out_id:
        _send_tracker.put(phone, out_id)
    body: dict[str, Any] = {
        "ok": True,
        "message": f"Verification code sent to {mask_phone(phone)}.",
        "out_id": out_id,
    }
    if result.get("verify_code"):
        log.info("SMS debug verify_code for %s (not returned to client)", mask_phone(phone))
    return body


def _cookie_secure(request: Request) -> bool:
    forwarded = trusted_proxy_header(
        request.headers,
        getattr(request, "client", None),
        "x-forwarded-proto",
    ).lower()
    if forwarded in {"http", "https"}:
        return forwarded == "https"
    return request.url.scheme == "https"


def _persist_user_on_login(phone: str) -> None:
    from math_agent.knowledge.supabase_client import service_role_configured
    from math_agent.web.user_store import upsert_user_on_login

    if service_role_configured():
        try:
            upsert_user_on_login(phone)
        except Exception as exc:
            log.exception("Failed to persist user for %s", mask_phone(phone))
            raise HTTPException(
                status_code=502,
                detail=f"Failed to persist user: {exc}",
            ) from exc
    else:
        upsert_user_on_login(phone)  # logs warning, no-op


def _login_success_response(
    phone: str,
    request: Request,
    *,
    sms_bypass: bool = False,
) -> JSONResponse:
    _persist_user_on_login(phone)
    _verify_tracker.record_success(phone)
    token, user, ttl = issue_access_token(phone)
    body: dict[str, Any] = {
        "ok": True,
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": ttl,
        "user": {
            "id": user.user_id,
            "phone": mask_phone(user.phone),
            "is_admin": is_admin_phone(user.phone),
        },
    }
    if sms_bypass:
        body["sms_bypass"] = True
    response = JSONResponse(body)
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        token,
        max_age=ttl,
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
        path="/",
    )
    return response


@router.post("/verify-code")
async def verify_code(payload: VerifyCodeRequest, request: Request) -> JSONResponse:
    if not phone_auth_enabled():
        raise HTTPException(status_code=503, detail="Phone auth is not enabled (set CONJECTA_JWT_SECRET).")
    try:
        phone = normalize_phone(payload.phone)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid phone number.") from None
    if is_phone_banned(phone):
        raise HTTPException(status_code=403, detail=ban_message())
    if is_sms_bypass_phone(phone):
        log.info("SMS bypass login for %s", mask_phone(phone))
        return _login_success_response(phone, request, sms_bypass=True)
    if not dypns_configured():
        raise HTTPException(status_code=503, detail="Aliyun Dypns is not configured.")
    if _verify_tracker.is_locked(phone):
        raise HTTPException(status_code=429, detail="验证失败次数过多，请稍后再试。")
    out_id = payload.out_id or _send_tracker.get(phone)
    try:
        passed = await check_sms_verify_code(phone, payload.code, out_id=out_id)
    except DypnsError as exc:
        log.warning("verify-code failed: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if not passed:
        _verify_tracker.record_failure(phone)
        raise HTTPException(status_code=401, detail="Invalid or expired verification code.")
    return _login_success_response(phone, request)


@router.post("/logout")
async def auth_logout(request: Request) -> JSONResponse:
    response = JSONResponse({"ok": True})
    # Mirror set_cookie attributes so browsers reliably clear Secure cookies.
    response.delete_cookie(
        ACCESS_TOKEN_COOKIE,
        path="/",
        httponly=True,
        secure=_cookie_secure(request),
        samesite="lax",
    )
    return response


@router.get("/me")
async def auth_me(
    request: Request,
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    token = _bearer_value(authorization) or _cookie_token(request.cookies)
    user = decode_access_token(token) if token else None
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")
    if is_user_banned(phone=user.phone, user_id=user.user_id):
        return {
            "ok": True,
            "banned": True,
            "ban_message": ban_message(),
            "user": {
                "id": user.user_id,
                "phone": mask_phone(user.phone),
                "is_admin": False,
                "banned": True,
            },
        }
    return {
        "ok": True,
        "banned": False,
        "user": {
            "id": user.user_id,
            "phone": mask_phone(user.phone),
            "is_admin": is_admin_phone(user.phone),
            "banned": False,
        },
    }


def _bearer_value(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return value.strip()
