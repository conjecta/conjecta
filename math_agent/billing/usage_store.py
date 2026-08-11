from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from math_agent.billing.models import UsageRecord
from math_agent.knowledge.supabase_client import create_supabase_client

log = logging.getLogger("math_agent.billing.usage_store")
DAILY_TABLE = "conjecta_usage_daily"

_EMPTY_USAGE = {
    "prompt_tokens": 0,
    "completion_tokens": 0,
    "total_tokens": 0,
    "cost_usd": 0.0,
}


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _schema_missing(exc: BaseException) -> bool:
    """True when billing tables/columns are not migrated yet (PostgREST cache miss)."""
    msg = str(exc)
    return (
        "PGRST204" in msg
        or "PGRST205" in msg
        or "Could not find the table" in msg
        or "Could not find the '" in msg
    )


class UsageStore:
    def __init__(self, client: Any | None = None) -> None:
        self.client = client if client is not None else create_supabase_client(prefer_service_role=True)

    def record(self, user_id: str, usage: UsageRecord, source: str = "platform") -> None:
        try:
            # total_tokens is intentionally omitted: the database computes it
            # from prompt_tokens + completion_tokens inside increment_usage.
            self.client.rpc(
                "increment_usage",
                {
                    "p_user_id": user_id,
                    "p_prompt_tokens": usage.prompt_tokens,
                    "p_completion_tokens": usage.completion_tokens,
                    "p_cost_usd": usage.cost_usd,
                    "p_provider": usage.provider,
                    "p_model": usage.model,
                    "p_source": source,
                },
            ).execute()
        except Exception:
            log.exception("Failed to record usage for %s", user_id)
            raise

    def daily_usage(self, user_id: str, target_date: date | None = None) -> dict[str, Any]:
        target = (target_date or _today_utc()).isoformat()
        try:
            resp = (
                self.client.table(DAILY_TABLE)
                .select("prompt_tokens,completion_tokens,total_tokens,estimated_cost_usd")
                .eq("user_id", user_id)
                .eq("date", target)
                .limit(1)
                .execute()
            )
        except Exception as exc:
            if _schema_missing(exc):
                log.error(
                    "Billing schema missing (apply docs/supabase_billing_schema.sql); "
                    "treating daily usage as zero for %s",
                    user_id,
                )
                return dict(_EMPTY_USAGE)
            raise
        data = resp.data or []
        if data:
            row = data[0]
            return {
                "prompt_tokens": row.get("prompt_tokens", 0),
                "completion_tokens": row.get("completion_tokens", 0),
                "total_tokens": row.get("total_tokens", 0),
                "cost_usd": float(row.get("estimated_cost_usd", 0) or 0),
            }
        return dict(_EMPTY_USAGE)

    def monthly_summary(self, user_id: str, year: int, month: int) -> dict[str, Any]:
        start = date(year, month, 1)
        if month == 12:
            end = date(year + 1, 1, 1)
        else:
            end = date(year, month + 1, 1)
        try:
            resp = (
                self.client.table(DAILY_TABLE)
                .select("prompt_tokens,completion_tokens,total_tokens,estimated_cost_usd")
                .eq("user_id", user_id)
                .gte("date", start.isoformat())
                .lt("date", end.isoformat())
                .execute()
            )
        except Exception as exc:
            if _schema_missing(exc):
                log.error(
                    "Billing schema missing (apply docs/supabase_billing_schema.sql); "
                    "treating monthly summary as zero for %s",
                    user_id,
                )
                return dict(_EMPTY_USAGE)
            raise
        rows = resp.data or []
        return {
            "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in rows),
            "completion_tokens": sum(r.get("completion_tokens", 0) for r in rows),
            "total_tokens": sum(r.get("total_tokens", 0) for r in rows),
            "cost_usd": sum(float(r.get("estimated_cost_usd", 0) or 0) for r in rows),
        }
