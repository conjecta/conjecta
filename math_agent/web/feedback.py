"""Solve feedback persistence for admin operations."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from math_agent.knowledge.supabase_client import create_supabase_client, service_role_configured

FEEDBACK_TABLE = "conjecta_solve_feedback"
CLOUD_STORAGE_REQUIRED = "CLOUD_STORAGE_REQUIRED"
ALLOWED_RATINGS = frozenset({"satisfied", "unsatisfied"})
ALLOWED_OUTCOMES = frozenset({"completed", "failed"})
MAX_COMMENT = 2000
MAX_PREVIEW = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_unique_violation(exc: BaseException) -> bool:
    return str(getattr(exc, "code", "")) == "23505"


def normalize_feedback_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rating = str(payload.get("rating") or "").strip()
    outcome = str(payload.get("outcome") or "").strip()
    if rating not in ALLOWED_RATINGS:
        raise HTTPException(status_code=400, detail="rating must be satisfied or unsatisfied.")
    if outcome not in ALLOWED_OUTCOMES:
        raise HTTPException(status_code=400, detail="outcome must be completed or failed.")
    comment = str(payload.get("comment") or "").strip()
    if len(comment) > MAX_COMMENT:
        raise HTTPException(status_code=400, detail=f"comment must be ≤ {MAX_COMMENT} characters.")
    session_id = str(payload.get("session_id") or "").strip() or None
    preview = str(payload.get("problem_preview") or "").strip()[:MAX_PREVIEW]
    return {
        "rating": rating,
        "outcome": outcome,
        "comment": comment,
        "session_id": session_id,
        "problem_preview": preview,
    }


class FeedbackStore:
    def __init__(self, client: Any | None = None) -> None:
        self.client = client if client is not None else create_supabase_client(
            prefer_service_role=True, require_service_role=True
        )

    def upsert(
        self,
        user_id: str,
        *,
        rating: str,
        outcome: str,
        comment: str = "",
        session_id: str | None = None,
        problem_preview: str = "",
    ) -> dict[str, Any]:
        user_id = str(user_id or "").strip()
        if not user_id:
            raise HTTPException(status_code=400, detail="user_id is required.")
        now = _now()
        session_id = (session_id or "").strip() or None
        row = {
            "user_id": user_id,
            "session_id": session_id,
            "rating": rating,
            "comment": comment,
            "outcome": outcome,
            "problem_preview": problem_preview[:MAX_PREVIEW],
            "updated_at": now,
        }
        # Partial unique index on (user_id, session_id) WHERE session_id IS NOT NULL
        # prevents PostgREST upsert(on_conflict=...); insert + catch 23505 is atomic.
        if session_id:
            row["created_at"] = now
            try:
                resp = self.client.table(FEEDBACK_TABLE).insert(row).execute()
                return (resp.data or [row])[0]
            except Exception as exc:
                if not _is_unique_violation(exc):
                    raise
                update_row = {k: v for k, v in row.items() if k != "created_at"}
                resp = (
                    self.client.table(FEEDBACK_TABLE)
                    .update(update_row)
                    .eq("user_id", user_id)
                    .eq("session_id", session_id)
                    .execute()
                )
                updated = (resp.data or [{}])[0]
                return {**updated, **update_row}
        row["created_at"] = now
        resp = self.client.table(FEEDBACK_TABLE).insert(row).execute()
        return (resp.data or [row])[0]

    def list_feedback(
        self, *, limit: int = 100, rating: str | None = None
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 500))
        query = (
            self.client.table(FEEDBACK_TABLE)
            .select("*")
            .order("created_at", desc=True)
            .limit(limit)
        )
        if rating:
            rating = str(rating).strip()
            if rating not in ALLOWED_RATINGS:
                raise HTTPException(status_code=400, detail="Invalid rating filter.")
            query = (
                self.client.table(FEEDBACK_TABLE)
                .select("*")
                .eq("rating", rating)
                .order("created_at", desc=True)
                .limit(limit)
            )
        resp = query.execute()
        return [dict(r) for r in (resp.data or [])]


def enrich_feedback_rows(
    rows: list[dict[str, Any]], profiles: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        uid = str(row.get("user_id") or "")
        profile = profiles.get(uid) or {}
        display = str(profile.get("display_name") or "").strip()
        phone_masked = str(profile.get("phone_masked") or "")
        out.append(
            {
                **row,
                "display_name": display,
                "phone_masked": phone_masked,
                "label": display or phone_masked or uid or "—",
            }
        )
    return out


def feedback_store_or_none() -> FeedbackStore | None:
    if not service_role_configured():
        return None
    try:
        return FeedbackStore()
    except Exception:
        return None
