"""Server-only operations telemetry and admin dashboard aggregation."""
from __future__ import annotations

import logging
import os
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from math_agent.knowledge.supabase_client import (
    create_supabase_client,
    run_supabase_async,
    service_role_configured,
)
from math_agent.web.jwt_auth import normalize_phone

log = logging.getLogger("math_agent.web.operations")

USAGE_TABLE = "conjecta_llm_usage"
RUNS_TABLE = "conjecta_solve_runs"
# Empty by default: production must set CONJECTA_ADMIN_PHONES explicitly.
# A hard-coded default phone was an open-source footgun (anyone who OTP-logs
# in as that number became admin).
DEFAULT_ADMIN_PHONE = ""


def _answers_for_runs(runs: list[dict[str, Any]]) -> dict[str, str]:
    """Resolve final answers from per-user project turns, keyed by session id."""
    if not runs:
        return {}
    from math_agent.web.project_store import project_store_for_user

    grouped: dict[tuple[str, str], set[str]] = {}
    for run in runs:
        user_id = str(run.get("user_id") or "").strip()
        project_id = str(run.get("project_id") or "default").strip() or "default"
        session_id = str(run.get("id") or "").strip()
        if not user_id or not session_id:
            continue
        grouped.setdefault((user_id, project_id), set()).add(session_id)

    answers: dict[str, str] = {}
    for (user_id, project_id), session_ids in grouped.items():
        try:
            store = project_store_for_user(user_id)
            for turn in store.list_turns(project_id):
                session_id = str(turn.get("session_id") or "").strip()
                if session_id not in session_ids:
                    continue
                answer = str(turn.get("answer") or "").strip()
                if answer:
                    answers[session_id] = answer
        except Exception:
            log.warning(
                "Failed to load answers for admin dashboard user=%s project=%s",
                user_id,
                project_id,
                exc_info=True,
            )
    return answers


@dataclass(frozen=True)
class UsageContext:
    user_id: str = ""
    session_id: str = ""
    operation: str = "system"


_usage_context: ContextVar[UsageContext] = ContextVar(
    "conjecta_usage_context", default=UsageContext()
)


def set_usage_context(
    *, user_id: str | None, session_id: str | None, operation: str = "system"
) -> Token[UsageContext]:
    return _usage_context.set(
        UsageContext(
            user_id=str(user_id or "")[:128],
            session_id=str(session_id or "")[:128],
            operation=str(operation or "system")[:64],
        )
    )


def reset_usage_context(token: Token[UsageContext]) -> None:
    _usage_context.reset(token)


def admin_phones() -> frozenset[str]:
    raw = os.getenv("CONJECTA_ADMIN_PHONES", DEFAULT_ADMIN_PHONE)
    phones: set[str] = set()
    for value in raw.replace(";", ",").split(","):
        value = value.strip()
        if not value:
            continue
        try:
            phones.add(normalize_phone(value))
        except ValueError:
            log.warning("Ignoring invalid CONJECTA_ADMIN_PHONES entry")
    return frozenset(phones)


def is_admin_phone(phone: str | None) -> bool:
    if not phone:
        return False
    try:
        return normalize_phone(phone) in admin_phones()
    except ValueError:
        return False


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int_attr(value: Any, name: str) -> int:
    if isinstance(value, dict):
        raw = value.get(name)
    else:
        raw = getattr(value, name, 0)
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def usage_payload(usage: Any) -> dict[str, int]:
    """Normalize OpenAI-compatible token usage objects."""
    if usage is None:
        return {}
    prompt_details = (
        usage.get("prompt_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "prompt_tokens_details", None)
    )
    completion_details = (
        usage.get("completion_tokens_details")
        if isinstance(usage, dict)
        else getattr(usage, "completion_tokens_details", None)
    )
    input_tokens = _int_attr(usage, "prompt_tokens")
    output_tokens = _int_attr(usage, "completion_tokens")
    total_tokens = _int_attr(usage, "total_tokens") or input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_tokens": _int_attr(prompt_details, "cached_tokens"),
        "reasoning_tokens": _int_attr(completion_details, "reasoning_tokens"),
    }


class OperationsStore:
    def __init__(self, client: Any | None = None) -> None:
        self.client = client if client is not None else create_supabase_client(
            require_service_role=True
        )

    def record_usage(
        self, *, provider: str, model: str, usage: dict[str, int]
    ) -> None:
        if not usage:
            return
        context = _usage_context.get()
        self.client.table(USAGE_TABLE).insert(
            {
                "user_id": context.user_id or None,
                "session_id": context.session_id or None,
                "operation": context.operation,
                "provider": str(provider or "unknown")[:64],
                "model": str(model or "unknown")[:160],
                **usage,
                "created_at": _iso_now(),
            }
        ).execute()

    def start_run(
        self,
        *,
        session_id: str,
        user_id: str | None,
        project_id: str,
        problem: str,
        mode: str,
        model: str,
    ) -> None:
        self.client.table(RUNS_TABLE).upsert(
            {
                "id": session_id,
                "user_id": str(user_id or ""),
                "project_id": project_id[:128],
                "problem": problem[:8000],
                "mode": mode[:32],
                "model": model[:160],
                "status": "running",
                "started_at": _iso_now(),
            },
            on_conflict="id",
        ).execute()

    def finish_run(
        self, *, session_id: str, status: str, reason: str | None = None
    ) -> None:
        row: dict[str, Any] = {"status": status[:32], "finished_at": _iso_now()}
        if reason:
            row["reason"] = reason[:64]
        try:
            self.client.table(RUNS_TABLE).update(row).eq("id", session_id).execute()
        except Exception:
            if not reason:
                raise
            # Tolerate deployments where the ``reason`` column migration has
            # not been applied yet: persist the status rather than nothing.
            log.warning("finish_run with reason failed; retrying without reason")
            self.client.table(RUNS_TABLE).update(
                {"status": status[:32], "finished_at": _iso_now()}
            ).eq("id", session_id).execute()

    def list_running_runs(
        self, *, since_iso: str, started_before: str, limit: int
    ) -> list[dict[str, Any]]:
        """Recent runs still marked ``running`` (recovery candidates).

        ``started_before`` excludes runs created after the caller started
        scanning, so a fresh solve is never swept up as a stale leftover.
        """
        resp = (
            self.client.table(RUNS_TABLE)
            .select("id,user_id,project_id,mode,model,started_at")
            .eq("status", "running")
            .gte("started_at", since_iso)
            .lt("started_at", started_before)
            .order("started_at", desc=False)
            .limit(limit)
            .execute()
        )
        return [row for row in (resp.data or []) if isinstance(row, dict)]

    def mark_running_interrupted(self, *, started_before: str, reason: str) -> None:
        """Mark every pre-scan ``running`` row as interrupted by a restart."""
        row = {
            "status": "interrupted",
            "finished_at": _iso_now(),
            "reason": reason[:64],
        }
        try:
            (
                self.client.table(RUNS_TABLE)
                .update(row)
                .eq("status", "running")
                .lt("started_at", started_before)
                .execute()
            )
        except Exception:
            log.warning("mark_running_interrupted with reason failed; retrying without reason")
            (
                self.client.table(RUNS_TABLE)
                .update({"status": "interrupted", "finished_at": _iso_now()})
                .eq("status", "running")
                .lt("started_at", started_before)
                .execute()
            )

    def dashboard(
        self, *, users: list[dict[str, Any]], days: int = 30, limit: int = 100
    ) -> dict[str, Any]:
        days = max(1, min(int(days), 90))
        limit = max(1, min(int(limit), 500))
        since_dt = datetime.now(timezone.utc) - timedelta(days=days - 1)
        since = since_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()

        runs_resp = (
            self.client.table(RUNS_TABLE)
            .select("id,user_id,project_id,problem,mode,model,status,started_at,finished_at")
            .gte("started_at", since)
            .order("started_at", desc=True)
            .limit(10000)
            .execute()
        )
        usage_resp = (
            self.client.table(USAGE_TABLE)
            .select(
                "user_id,session_id,provider,model,input_tokens,output_tokens,total_tokens,"
                "cached_tokens,reasoning_tokens,created_at"
            )
            .gte("created_at", since)
            .order("created_at", desc=True)
            .limit(10000)
            .execute()
        )
        runs = [row for row in (runs_resp.data or []) if isinstance(row, dict)]
        usage_rows = [row for row in (usage_resp.data or []) if isinstance(row, dict)]

        by_session: dict[str, dict[str, int]] = {}
        by_user: dict[str, dict[str, Any]] = {}
        daily: dict[str, dict[str, Any]] = {}
        totals = {
            "input_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "cached_tokens": 0,
            "reasoning_tokens": 0,
        }
        token_fields = tuple(totals)
        for row in usage_rows:
            session_id = str(row.get("session_id") or "")
            user_id = str(row.get("user_id") or "")
            created_at = str(row.get("created_at") or "")
            day = created_at[:10]
            session_totals = by_session.setdefault(
                session_id, {field: 0 for field in token_fields}
            )
            user_totals = by_user.setdefault(
                user_id,
                {**{field: 0 for field in token_fields}, "runs": 0, "last_active_at": ""},
            )
            day_totals = daily.setdefault(
                day, {"date": day, "tokens": 0, "runs": 0, "users": set()}
            )
            for field in token_fields:
                value = _int_attr(row, field)
                totals[field] += value
                session_totals[field] += value
                user_totals[field] += value
            day_totals["tokens"] += _int_attr(row, "total_tokens")
            if user_id:
                day_totals["users"].add(user_id)
            if created_at > user_totals["last_active_at"]:
                user_totals["last_active_at"] = created_at

        run_ids_by_day: dict[str, set[str]] = {}
        for run in runs:
            user_id = str(run.get("user_id") or "")
            started_at = str(run.get("started_at") or "")
            day = started_at[:10]
            stats = by_user.setdefault(
                user_id,
                {**{field: 0 for field in token_fields}, "runs": 0, "last_active_at": ""},
            )
            stats["runs"] += 1
            if started_at > stats["last_active_at"]:
                stats["last_active_at"] = started_at
            run_ids_by_day.setdefault(day, set()).add(str(run.get("id") or ""))

        user_rows: list[dict[str, Any]] = []
        known_ids: set[str] = set()
        for user in users:
            user_id = str(user.get("id") or "")
            known_ids.add(user_id)
            stats = by_user.get(user_id, {})
            user_rows.append(
                {
                    "id": user_id,
                    "phone": str(user.get("phone") or user.get("phone_masked") or "—"),
                    "created_at": user.get("created_at") or "",
                    "last_login_at": user.get("last_login_at") or "",
                    "last_active_at": stats.get("last_active_at") or "",
                    "runs": int(stats.get("runs") or 0),
                    **{field: int(stats.get(field) or 0) for field in token_fields},
                }
            )
        for user_id, stats in by_user.items():
            if not user_id or user_id in known_ids:
                continue
            user_rows.append(
                {
                    "id": user_id,
                    "phone": "未知用户",
                    "created_at": "",
                    "last_login_at": "",
                    "last_active_at": stats.get("last_active_at") or "",
                    "runs": int(stats.get("runs") or 0),
                    **{field: int(stats.get(field) or 0) for field in token_fields},
                }
            )
        user_rows.sort(
            key=lambda row: (int(row["total_tokens"]), str(row["last_active_at"])),
            reverse=True,
        )

        limited_runs = runs[:limit]
        answers_by_session = _answers_for_runs(limited_runs)
        records: list[dict[str, Any]] = []
        for run in limited_runs:
            started = str(run.get("started_at") or "")
            finished = str(run.get("finished_at") or "")
            duration_ms = 0
            try:
                if started and finished:
                    duration_ms = max(
                        0,
                        int(
                            (
                                datetime.fromisoformat(finished.replace("Z", "+00:00"))
                                - datetime.fromisoformat(started.replace("Z", "+00:00"))
                            ).total_seconds()
                            * 1000
                        ),
                    )
            except ValueError:
                duration_ms = 0
            user_id = str(run.get("user_id") or "")
            phone = next(
                (row["phone"] for row in user_rows if row["id"] == user_id),
                "未知用户",
            )
            session_id = str(run.get("id") or "")
            records.append(
                {
                    **run,
                    "phone": phone,
                    "answer": answers_by_session.get(session_id, ""),
                    "duration_ms": duration_ms,
                    **by_session.get(
                        session_id,
                        {field: 0 for field in token_fields},
                    ),
                }
            )

        day_rows = []
        for offset in range(days - 1, -1, -1):
            day = (datetime.now(timezone.utc) - timedelta(days=offset)).date().isoformat()
            item = daily.get(day, {"date": day, "tokens": 0, "users": set()})
            day_rows.append(
                {
                    "date": day,
                    "tokens": int(item.get("tokens") or 0),
                    "runs": len(run_ids_by_day.get(day, set())),
                    "users": len(item.get("users") or set()),
                }
            )

        completed = sum(1 for run in runs if run.get("status") == "completed")
        failed = sum(1 for run in runs if run.get("status") == "failed")
        return {
            "period_days": days,
            "summary": {
                **totals,
                "users": len(users),
                "active_users": sum(1 for row in user_rows if row["runs"] > 0),
                "runs": len(runs),
                "completed_runs": completed,
                "failed_runs": failed,
                "avg_tokens_per_run": round(totals["total_tokens"] / len(runs)) if runs else 0,
            },
            "daily": day_rows,
            "users": user_rows,
            "records": records,
            "generated_at": _iso_now(),
        }


async def record_llm_usage(*, provider: str, model: str, usage: Any) -> None:
    payload = usage_payload(usage)
    if not payload or not service_role_configured():
        return
    try:
        await run_supabase_async(
            lambda: OperationsStore().record_usage(
                provider=provider, model=model, usage=payload
            )
        )
    except Exception:
        log.exception("Failed to persist LLM usage")


async def start_solve_run(**kwargs: Any) -> None:
    if not service_role_configured():
        return
    try:
        await run_supabase_async(lambda: OperationsStore().start_run(**kwargs))
    except Exception:
        log.exception("Failed to persist solve start")


async def finish_solve_run(
    *, session_id: str, status: str, reason: str | None = None
) -> None:
    if not service_role_configured():
        return
    try:
        await run_supabase_async(
            lambda: OperationsStore().finish_run(
                session_id=session_id, status=status, reason=reason
            )
        )
    except Exception:
        log.exception("Failed to persist solve finish")
