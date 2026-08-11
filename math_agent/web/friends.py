"""Friends graph: request / accept / list for knowledge sharing."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from math_agent.knowledge.supabase_client import (
    create_supabase_client,
    run_supabase,
    service_role_configured,
)
from math_agent.web.jwt_auth import normalize_phone, user_id_for_phone
from math_agent.web.user_store import UserStore

FRIENDSHIPS_TABLE = "friendships"
CLOUD_STORAGE_REQUIRED = "CLOUD_STORAGE_REQUIRED"
_CLOUD_REQUIRED = CLOUD_STORAGE_REQUIRED

# Bounded reads for friend list / request list endpoints.
_FRIENDSHIPS_LIMIT = 500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def require_friends_cloud() -> Any:
    if not service_role_configured():
        raise HTTPException(status_code=503, detail=_CLOUD_REQUIRED)
    try:
        return create_supabase_client(prefer_service_role=True, require_service_role=True)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=_CLOUD_REQUIRED) from exc


def _profile_fields(row: dict[str, Any] | None) -> dict[str, str]:
    row = row or {}
    return {
        "user_id": str(row.get("id") or ""),
        "display_name": str(row.get("display_name") or "").strip(),
        "phone_masked": str(row.get("phone_masked") or ""),
    }


def _label(profile: dict[str, str]) -> str:
    return profile.get("display_name") or profile.get("phone_masked") or profile.get("user_id") or "unknown"


class FriendsService:
    """CRUD for friendships backed by Supabase."""

    def __init__(self, user_id: str, client: Any | None = None) -> None:
        if not user_id or not str(user_id).strip():
            raise ValueError("user_id is required")
        self.user_id = str(user_id)
        self._client_override = client
        self.users = UserStore(client=client)

    @property
    def client(self) -> Any:
        if self._client_override is not None:
            return self._client_override
        return require_friends_cloud()

    @client.setter
    def client(self, value: Any) -> None:
        self._client_override = value
        self.users.client = value

    def list_friends(self) -> list[dict[str, Any]]:
        rows = self._accepted_rows()
        friend_ids = [
            str(r["addressee_id"] if r["requester_id"] == self.user_id else r["requester_id"])
            for r in rows
        ]
        profiles = self._profiles_by_ids(friend_ids)
        out: list[dict[str, Any]] = []
        for fid in friend_ids:
            profile = profiles.get(fid) or {
                "user_id": fid,
                "display_name": "",
                "phone_masked": "",
            }
            out.append({**profile, "label": _label(profile)})
        return out

    def list_requests(self) -> dict[str, list[dict[str, Any]]]:
        incoming = self._rows_eq("addressee_id", self.user_id, status="pending")
        outgoing = self._rows_eq("requester_id", self.user_id, status="pending")
        all_ids = [str(r["requester_id"]) for r in incoming] + [
            str(r["addressee_id"]) for r in outgoing
        ]
        profiles = self._profiles_by_ids(all_ids)

        def enrich(row: dict[str, Any], other_id: str) -> dict[str, Any]:
            profile = profiles.get(other_id) or {
                "user_id": other_id,
                "display_name": "",
                "phone_masked": "",
            }
            return {
                "id": str(row.get("id")),
                "status": str(row.get("status")),
                "created_at": str(row.get("created_at") or ""),
                "other": {**profile, "label": _label(profile)},
            }

        return {
            "incoming": [enrich(r, str(r["requester_id"])) for r in incoming],
            "outgoing": [enrich(r, str(r["addressee_id"])) for r in outgoing],
        }

    def request_friend(self, *, user_id: str | None = None, phone: str | None = None) -> dict[str, Any]:
        target_id = self._resolve_target(user_id=user_id, phone=phone)
        if target_id == self.user_id:
            raise HTTPException(status_code=400, detail="Cannot friend yourself.")
        existing = self._find_pair(self.user_id, target_id)
        now = _now()
        if existing:
            status = str(existing.get("status") or "")
            if status == "accepted":
                raise HTTPException(status_code=400, detail="Already friends.")
            if status == "blocked":
                raise HTTPException(status_code=404, detail="User not found.")
            if status == "pending":
                return {"friendship": existing, "created": False}
            # declined → re-open as pending from this requester
            updated = {
                "requester_id": self.user_id,
                "addressee_id": target_id,
                "status": "pending",
                "updated_at": now,
            }
            run_supabase(
                lambda: self.client.table(FRIENDSHIPS_TABLE)
                .update(updated)
                .eq("id", existing["id"])
                .execute()
            )
            return {"friendship": {**existing, **updated}, "created": False}

        row = {
            "requester_id": self.user_id,
            "addressee_id": target_id,
            "status": "pending",
            "created_at": now,
            "updated_at": now,
        }
        resp = run_supabase(lambda: self.client.table(FRIENDSHIPS_TABLE).insert(row).execute())
        friendship = (resp.data or [row])[0]
        return {"friendship": friendship, "created": True}

    def accept_request(self, friendship_id: str) -> dict[str, Any]:
        row = self._get(friendship_id)
        if str(row.get("addressee_id")) != self.user_id:
            raise HTTPException(status_code=404, detail="Friend request not found.")
        if str(row.get("status")) != "pending":
            raise HTTPException(status_code=400, detail="Request is not pending.")
        now = _now()
        run_supabase(
            lambda: self.client.table(FRIENDSHIPS_TABLE)
            .update({"status": "accepted", "updated_at": now})
            .eq("id", friendship_id)
            .execute()
        )
        row["status"] = "accepted"
        row["updated_at"] = now
        return {"friendship": row}

    def decline_request(self, friendship_id: str) -> dict[str, Any]:
        row = self._get(friendship_id)
        if str(row.get("addressee_id")) != self.user_id:
            raise HTTPException(status_code=404, detail="Friend request not found.")
        if str(row.get("status")) != "pending":
            raise HTTPException(status_code=400, detail="Request is not pending.")
        now = _now()
        run_supabase(
            lambda: self.client.table(FRIENDSHIPS_TABLE)
            .update({"status": "declined", "updated_at": now})
            .eq("id", friendship_id)
            .execute()
        )
        row["status"] = "declined"
        row["updated_at"] = now
        return {"friendship": row}

    def unfriend(self, other_user_id: str) -> dict[str, Any]:
        existing = self._find_pair(self.user_id, other_user_id)
        if existing is None or str(existing.get("status")) != "accepted":
            raise HTTPException(status_code=404, detail="Friendship not found.")
        run_supabase(
            lambda: self.client.table(FRIENDSHIPS_TABLE).delete().eq("id", existing["id"]).execute()
        )
        return {"deleted": True, "user_id": other_user_id}

    def are_friends(self, other_user_id: str) -> bool:
        if not other_user_id or other_user_id == self.user_id:
            return False
        existing = self._find_pair(self.user_id, other_user_id)
        return existing is not None and str(existing.get("status")) == "accepted"

    def friend_ids(self) -> list[str]:
        return [
            str(r["addressee_id"] if r["requester_id"] == self.user_id else r["requester_id"])
            for r in self._accepted_rows()
        ]

    def _resolve_target(self, *, user_id: str | None, phone: str | None) -> str:
        if user_id and str(user_id).strip():
            target = str(user_id).strip()
            profile = self.users.get_profile(target)
            if profile is None:
                raise HTTPException(status_code=404, detail="User not found.")
            return target
        if phone and str(phone).strip():
            try:
                normalized = normalize_phone(str(phone).strip())
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            target = user_id_for_phone(normalized)
            profile = self.users.get_profile(target)
            if profile is None:
                # Also try lookup by phone column in case id scheme differs in tests
                by_phone = self.users.find_by_phone(normalized)
                if by_phone is None:
                    raise HTTPException(status_code=404, detail="User not found.")
                return str(by_phone["id"])
            return target
        raise HTTPException(status_code=400, detail="user_id or phone is required.")

    def _get(self, friendship_id: str) -> dict[str, Any]:
        resp = run_supabase(
            lambda: self.client.table(FRIENDSHIPS_TABLE)
            .select("*")
            .eq("id", friendship_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="Friend request not found.")
        return dict(rows[0])

    def _find_pair(self, a: str, b: str) -> dict[str, Any] | None:
        # One query covering both directions of the (a, b) pair.
        resp = run_supabase(
            lambda: self.client.table(FRIENDSHIPS_TABLE)
            .select("*")
            .in_("requester_id", [a, b])
            .in_("addressee_id", [a, b])
            .limit(2)
            .execute()
        )
        pair = {a, b}
        for row in resp.data or []:
            if {str(row.get("requester_id")), str(row.get("addressee_id"))} == pair:
                return dict(row)
        return None

    def _accepted_rows(self) -> list[dict[str, Any]]:
        as_req = self._rows_eq("requester_id", self.user_id, status="accepted")
        as_addr = self._rows_eq("addressee_id", self.user_id, status="accepted")
        return as_req + as_addr

    def _rows_eq(self, col: str, value: str, *, status: str) -> list[dict[str, Any]]:
        resp = run_supabase(
            lambda: self.client.table(FRIENDSHIPS_TABLE)
            .select("*")
            .eq(col, value)
            .eq("status", status)
            .limit(_FRIENDSHIPS_LIMIT)
            .execute()
        )
        return [dict(r) for r in (resp.data or [])]

    def _profiles_by_ids(self, ids: list[str]) -> dict[str, dict[str, str]]:
        profiles = self.users.get_profiles(ids)
        out: dict[str, dict[str, str]] = {}
        for uid in dict.fromkeys(str(i or "") for i in ids):
            if not uid:
                continue
            fields = _profile_fields(profiles.get(uid))
            fields["user_id"] = uid
            out[uid] = fields
        return out


def are_friends(client: Any, user_a: str, user_b: str) -> bool:
    """Stateless friendship check used by knowledge card access."""
    if not user_a or not user_b or user_a == user_b:
        return False
    # One query covering both directions of the pair.
    resp = run_supabase(
        lambda: client.table(FRIENDSHIPS_TABLE)
        .select("id, status, requester_id, addressee_id")
        .in_("requester_id", [user_a, user_b])
        .in_("addressee_id", [user_a, user_b])
        .eq("status", "accepted")
        .limit(2)
        .execute()
    )
    pair = {user_a, user_b}
    return any(
        {str(row.get("requester_id")), str(row.get("addressee_id"))} == pair
        for row in (resp.data or [])
    )
