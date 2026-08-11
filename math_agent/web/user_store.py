"""Persist phone-authenticated users in Supabase."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from math_agent.knowledge.supabase_client import (
    create_supabase_client,
    run_supabase,
    service_role_configured,
)
from math_agent.web.jwt_auth import mask_phone, user_id_for_phone

log = logging.getLogger("math_agent.web.user_store")
USERS_TABLE = "conjecta_users"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class UserStore:
    """Server-side user repository (service role)."""

    def __init__(self, client: Any | None = None) -> None:
        # Prefer a property-backed client so run_supabase retries can open a
        # fresh httpx session after RemoteProtocolError. Injected clients
        # (tests / shared fakes) stay on the override.
        self._client_override = client

    @property
    def client(self) -> Any:
        if self._client_override is not None:
            return self._client_override
        return create_supabase_client(prefer_service_role=True)

    @client.setter
    def client(self, value: Any) -> None:
        self._client_override = value

    def upsert_login(self, phone: str) -> dict[str, Any]:
        user_id = user_id_for_phone(phone)
        now = _now()
        row = {
            "id": user_id,
            "phone": phone,
            "phone_masked": mask_phone(phone),
            "last_login_at": now,
        }
        # On conflict update last_login; created_at only set on insert via DB default
        # when we omit it — but upsert needs created_at for insert path.
        existing = run_supabase(
            lambda: (
                self.client.table(USERS_TABLE)
                .select("id, created_at")
                .eq("id", user_id)
                .limit(1)
                .execute()
            )
        )
        data = existing.data or []
        if data:
            resp = run_supabase(
                lambda: (
                    self.client.table(USERS_TABLE)
                    .update(
                        {
                            "last_login_at": now,
                            "phone_masked": mask_phone(phone),
                            "phone": phone,
                        }
                    )
                    .eq("id", user_id)
                    .execute()
                )
            )
            out = (resp.data or [None])[0] or {**row, "created_at": data[0].get("created_at")}
            return out
        row["created_at"] = now
        resp = run_supabase(lambda: self.client.table(USERS_TABLE).insert(row).execute())
        out = (resp.data or [None])[0] or row
        return out

    def list_users(self, *, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))
        resp = run_supabase(
            lambda: (
                self.client.table(USERS_TABLE)
                .select("id, phone, phone_masked, display_name, created_at, last_login_at")
                .order("last_login_at", desc=True)
                .limit(limit)
                .offset(offset)
                .execute()
            )
        )
        return resp.data or []

    def get_profile(self, user_id: str) -> dict[str, Any] | None:
        user_id = str(user_id or "").strip()
        if not user_id:
            return None
        resp = run_supabase(
            lambda: (
                self.client.table(USERS_TABLE)
                .select("id, phone, phone_masked, display_name, created_at, last_login_at")
                .eq("id", user_id)
                .limit(1)
                .execute()
            )
        )
        rows = resp.data or []
        return dict(rows[0]) if rows else None

    def get_profiles(self, user_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Batch-fetch profiles by id; returns {user_id: profile} for existing users."""
        unique = [uid for uid in dict.fromkeys(str(u or "").strip() for u in user_ids) if uid]
        if not unique:
            return {}
        try:
            resp = run_supabase(
                lambda: (
                    self.client.table(USERS_TABLE)
                    .select("id, phone, phone_masked, display_name, created_at, last_login_at")
                    .in_("id", unique)
                    .execute()
                )
            )
            found = {
                str(row.get("id")): dict(row)
                for row in (resp.data or [])
                if row.get("id")
            }
        except Exception:
            # Partial/fake clients without in_ support: per-id fallback.
            found = {}
        for uid in unique:
            if uid not in found:
                profile = self.get_profile(uid)
                if profile is not None:
                    found[uid] = profile
        return found

    def find_by_phone(self, phone: str) -> dict[str, Any] | None:
        phone = str(phone or "").strip()
        if not phone:
            return None
        resp = run_supabase(
            lambda: (
                self.client.table(USERS_TABLE)
                .select("id, phone, phone_masked, display_name, created_at, last_login_at")
                .eq("phone", phone)
                .limit(1)
                .execute()
            )
        )
        rows = resp.data or []
        return dict(rows[0]) if rows else None

    def update_display_name(self, user_id: str, display_name: str) -> dict[str, Any]:
        user_id = str(user_id or "").strip()
        name = str(display_name or "").strip()[:64]
        if not user_id:
            raise ValueError("user_id is required")
        existing = self.get_profile(user_id)
        if existing is None:
            raise ValueError("User not found")
        resp = run_supabase(
            lambda: (
                self.client.table(USERS_TABLE)
                .update({"display_name": name})
                .eq("id", user_id)
                .execute()
            )
        )
        out = (resp.data or [None])[0]
        if out:
            return dict(out)
        return {**existing, "display_name": name}


def upsert_user_on_login(phone: str) -> dict[str, Any] | None:
    """Upsert user when service role is configured; otherwise no-op with warning."""
    if not service_role_configured():
        log.warning(
            "SUPABASE_SERVICE_ROLE_KEY not set; skipping user persistence for %s",
            mask_phone(phone),
        )
        return None
    store = UserStore()
    return store.upsert_login(phone)
