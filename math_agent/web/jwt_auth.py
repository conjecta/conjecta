"""JWT helpers for phone-authenticated Conjecta users."""
from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass

import jwt

_PHONE_RE = re.compile(r"^1\d{10}$")
_DEFAULT_TTL_SECONDS = 7 * 24 * 3600
_MIN_SECRET_BYTES = 32


@dataclass(frozen=True)
class AuthUser:
    user_id: str
    phone: str


def phone_auth_enabled() -> bool:
    return bool(jwt_secret())


def jwt_secret() -> str | None:
    secret = os.getenv("CONJECTA_JWT_SECRET", "").strip()
    if not secret:
        return None
    if len(secret.encode("utf-8")) < _MIN_SECRET_BYTES:
        raise RuntimeError("CONJECTA_JWT_SECRET must be at least 32 bytes.")
    return secret


def jwt_ttl_seconds() -> int:
    raw = os.getenv("CONJECTA_JWT_TTL_SECONDS", "").strip()
    try:
        return max(300, int(raw)) if raw else _DEFAULT_TTL_SECONDS
    except ValueError:
        return _DEFAULT_TTL_SECONDS


def normalize_phone(phone: str, *, country_code: str = "86") -> str:
    digits = re.sub(r"\D", "", phone or "")
    if country_code == "86" and digits.startswith("86") and len(digits) == 13:
        digits = digits[2:]
    if not _PHONE_RE.match(digits):
        raise ValueError("Invalid mainland China mobile number.")
    return digits


def user_id_for_phone(phone: str) -> str:
    digest = hashlib.sha256(phone.encode("utf-8")).hexdigest()[:16]
    return f"u_{digest}"


def issue_access_token(phone: str) -> tuple[str, AuthUser, int]:
    secret = jwt_secret()
    if not secret:
        raise RuntimeError("CONJECTA_JWT_SECRET is not configured.")
    normalized = normalize_phone(phone)
    user = AuthUser(user_id=user_id_for_phone(normalized), phone=normalized)
    now = int(time.time())
    ttl = jwt_ttl_seconds()
    payload = {
        "sub": user.user_id,
        "phone": user.phone,
        "iat": now,
        "exp": now + ttl,
        "typ": "access",
    }
    token = jwt.encode(payload, secret, algorithm="HS256")
    if isinstance(token, bytes):
        token = token.decode("utf-8")
    return token, user, ttl


def decode_access_token(token: str) -> AuthUser | None:
    secret = jwt_secret()
    if not secret or not token:
        return None
    try:
        payload = jwt.decode(token, secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "access":
        return None
    sub = str(payload.get("sub") or "").strip()
    phone = str(payload.get("phone") or "").strip()
    if not sub or not phone:
        return None
    return AuthUser(user_id=sub, phone=phone)


def mask_phone(phone: str) -> str:
    if len(phone) < 7:
        return phone
    return f"{phone[:3]}****{phone[-4:]}"
