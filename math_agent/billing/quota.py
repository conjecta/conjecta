from __future__ import annotations

import os
from functools import lru_cache

FREE_TOKENS_PER_DAY = 500_000


def quota_disabled() -> bool:
    """True only when CONJECTA_DISABLE_QUOTA is set (local deployment).

    Production and CI must leave this unset so platform free-tier limits apply.
    """
    return os.getenv("CONJECTA_DISABLE_QUOTA", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def free_tokens_per_day() -> int:
    raw = os.getenv("CONJECTA_FREE_TOKENS_PER_DAY", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            pass
    return FREE_TOKENS_PER_DAY


@lru_cache(maxsize=1)
def unlimited_quota_phones() -> frozenset[str]:
    """Phones that skip the platform free-tier daily token cap."""
    raw = os.getenv("CONJECTA_UNLIMITED_QUOTA_PHONES", "").strip()
    if not raw:
        return frozenset()
    from math_agent.web.jwt_auth import normalize_phone

    phones: set[str] = set()
    for part in raw.replace(";", ",").split(","):
        value = part.strip()
        if not value:
            continue
        try:
            phones.add(normalize_phone(value))
        except ValueError:
            continue
    return frozenset(phones)


@lru_cache(maxsize=1)
def unlimited_quota_user_ids() -> frozenset[str]:
    from math_agent.web.jwt_auth import user_id_for_phone

    return frozenset(user_id_for_phone(phone) for phone in unlimited_quota_phones())


def is_quota_unlimited(*, phone: str | None = None, user_id: str | None = None) -> bool:
    if phone:
        from math_agent.web.jwt_auth import normalize_phone

        try:
            if normalize_phone(phone) in unlimited_quota_phones():
                return True
        except ValueError:
            pass
    if user_id and user_id in unlimited_quota_user_ids():
        return True
    return False


def clear_unlimited_quota_cache() -> None:
    unlimited_quota_phones.cache_clear()
    unlimited_quota_user_ids.cache_clear()


def is_allowed(used_tokens: int, *, user_id: str | None = None, phone: str | None = None) -> bool:
    if quota_disabled() or is_quota_unlimited(user_id=user_id, phone=phone):
        return True
    return used_tokens < free_tokens_per_day()


def remaining_tokens(
    used_tokens: int, *, user_id: str | None = None, phone: str | None = None
) -> int:
    if quota_disabled() or is_quota_unlimited(user_id=user_id, phone=phone):
        return free_tokens_per_day()
    return max(0, free_tokens_per_day() - used_tokens)
