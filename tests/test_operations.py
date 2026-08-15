from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from math_agent.web.operations import (
    OperationsStore,
    RUNS_TABLE,
    USAGE_TABLE,
    is_admin_phone,
    reset_usage_context,
    set_usage_context,
    usage_payload,
)


class _Table:
    def __init__(self, rows):
        self.rows = rows
        self.result = list(rows)

    def select(self, _fields):
        self.result = list(self.rows)
        return self

    def gte(self, field, value):
        self.result = [row for row in self.result if str(row.get(field) or "") >= value]
        return self

    def order(self, field, desc=False):
        self.result.sort(key=lambda row: str(row.get(field) or ""), reverse=desc)
        return self

    def limit(self, value):
        self.result = self.result[:value]
        return self

    def insert(self, row):
        self.rows.append(dict(row))
        self.result = [row]
        return self

    def execute(self):
        return SimpleNamespace(data=list(self.result))


class _Client:
    def __init__(self, tables=None):
        self.tables = tables or {RUNS_TABLE: [], USAGE_TABLE: []}

    def table(self, name):
        return _Table(self.tables.setdefault(name, []))


def test_default_admin_phone_and_env_override(monkeypatch):
    monkeypatch.delenv("CONJECTA_ADMIN_PHONES", raising=False)
    # No hard-coded default admin: unset env means nobody is admin.
    assert is_admin_phone("17855537173") is False
    assert is_admin_phone("13812345678") is False
    monkeypatch.setenv("CONJECTA_ADMIN_PHONES", "13812345678, 13900001111")
    assert is_admin_phone("17855537173") is False
    assert is_admin_phone("+86 138 1234 5678") is True


def test_usage_payload_reads_provider_details():
    usage = SimpleNamespace(
        prompt_tokens=120,
        completion_tokens=45,
        total_tokens=165,
        prompt_tokens_details=SimpleNamespace(cached_tokens=30),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=12),
    )
    assert usage_payload(usage) == {
        "input_tokens": 120,
        "output_tokens": 45,
        "total_tokens": 165,
        "cached_tokens": 30,
        "reasoning_tokens": 12,
    }


def test_record_usage_carries_user_and_session_context():
    client = _Client()
    token = set_usage_context(user_id="u-one", session_id="s-one", operation="solve")
    try:
        OperationsStore(client=client).record_usage(
            provider="deepseek",
            model="deepseek-v4-pro",
            usage={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "cached_tokens": 2,
                "reasoning_tokens": 1,
            },
        )
    finally:
        reset_usage_context(token)
    row = client.tables[USAGE_TABLE][0]
    assert row["user_id"] == "u-one"
    assert row["session_id"] == "s-one"
    assert row["total_tokens"] == 15


def test_dashboard_joins_users_runs_and_token_usage():
    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=2)
    client = _Client(
        {
            RUNS_TABLE: [
                {
                    "id": "s-one",
                    "user_id": "u-one",
                    "project_id": "default",
                    "problem": "Prove it",
                    "mode": "auto",
                    "model": "deepseek/deepseek-v4-pro",
                    "status": "completed",
                    "started_at": started.isoformat(),
                    "finished_at": now.isoformat(),
                }
            ],
            USAGE_TABLE: [
                {
                    "user_id": "u-one",
                    "session_id": "s-one",
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                    "input_tokens": 100,
                    "output_tokens": 40,
                    "total_tokens": 140,
                    "cached_tokens": 20,
                    "reasoning_tokens": 8,
                    "created_at": now.isoformat(),
                }
            ],
        }
    )
    result = OperationsStore(client=client).dashboard(
        users=[
            {
                "id": "u-one",
                "phone": "13812345678",
                "phone_masked": "138****5678",
                "created_at": started.isoformat(),
                "last_login_at": now.isoformat(),
            }
        ],
        days=7,
    )
    assert result["summary"]["runs"] == 1
    assert result["summary"]["total_tokens"] == 140
    assert result["users"][0]["phone"] == "13812345678"
    assert result["users"][0]["runs"] == 1
    assert result["records"][0]["total_tokens"] == 140
    assert result["records"][0]["duration_ms"] == 120_000
    assert result["records"][0]["answer"] == ""
