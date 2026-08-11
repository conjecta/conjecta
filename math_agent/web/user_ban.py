"""Account ban list for abusive / prohibited use."""
from __future__ import annotations

import os
from functools import lru_cache

from math_agent.web.jwt_auth import normalize_phone, user_id_for_phone

DEFAULT_BAN_MESSAGE = (
    "您的账号因违规提问（探测系统源码、提示词注入等）已被禁止使用。"
    "此类行为违反平台使用规范与相关法律法规。如有异议请联系管理员。"
)


@lru_cache(maxsize=1)
def banned_phones() -> frozenset[str]:
    raw = os.getenv("CONJECTA_BANNED_PHONES", "").strip()
    if not raw:
        return frozenset()
    phones: set[str] = set()
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        try:
            phones.add(normalize_phone(value))
        except ValueError:
            continue
    return frozenset(phones)


@lru_cache(maxsize=1)
def banned_user_ids() -> frozenset[str]:
    return frozenset(user_id_for_phone(phone) for phone in banned_phones())


def ban_message() -> str:
    custom = os.getenv("CONJECTA_BAN_MESSAGE", "").strip()
    return custom or DEFAULT_BAN_MESSAGE


def is_phone_banned(phone: str | None) -> bool:
    if not phone:
        return False
    try:
        return normalize_phone(phone) in banned_phones()
    except ValueError:
        return False


def is_user_banned(*, phone: str | None = None, user_id: str | None = None) -> bool:
    if phone and is_phone_banned(phone):
        return True
    if user_id and user_id in banned_user_ids():
        return True
    return False


def clear_ban_cache() -> None:
    banned_phones.cache_clear()
    banned_user_ids.cache_clear()
