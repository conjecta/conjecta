from datetime import date
from unittest.mock import MagicMock

import pytest

from math_agent.billing.models import UsageRecord
from math_agent.billing.usage_store import UsageStore


def _mock_client():
    client = MagicMock()
    client.table.return_value = client
    client.rpc.return_value = client
    client.select.return_value = client
    client.eq.return_value = client
    client.limit.return_value = client
    client.gte.return_value = client
    client.lt.return_value = client
    client.execute.return_value = MagicMock(data=[])
    return client


def _usage_record(**kwargs):
    defaults = {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
        "cost_usd": 0.65,
        "provider": "openai",
        "model": "gpt-5.5",
    }
    defaults.update(kwargs)
    return UsageRecord(**defaults)


def test_record_calls_increment_usage_rpc_with_payload():
    client = _mock_client()
    store = UsageStore(client)
    record = _usage_record()

    store.record("u_1", record)

    client.rpc.assert_called_once()
    call = client.rpc.mock_calls[0]
    assert call.args[0] == "increment_usage"
    params = call.args[1]
    assert params["p_user_id"] == "u_1"
    assert params["p_prompt_tokens"] == 10
    assert params["p_completion_tokens"] == 20
    assert params["p_cost_usd"] == 0.65
    assert params["p_provider"] == "openai"
    assert params["p_model"] == "gpt-5.5"
    assert params["p_source"] == "platform"
    assert "p_total_tokens" not in params
    assert "p_date" not in params
    client.execute.assert_called_once()


def test_record_passes_source_parameter():
    client = _mock_client()
    store = UsageStore(client)
    record = _usage_record()

    store.record("u_1", record, source="user")

    call = client.rpc.mock_calls[0]
    params = call.args[1]
    assert params["p_source"] == "user"


def test_record_multiple_calls_accumulate_via_rpc():
    client = _mock_client()
    store = UsageStore(client)

    store.record("u_1", _usage_record(prompt_tokens=5, completion_tokens=5, total_tokens=10, cost_usd=0.1))
    store.record("u_1", _usage_record(prompt_tokens=7, completion_tokens=8, total_tokens=15, cost_usd=0.2))

    assert client.rpc.call_count == 2
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    for call in client.rpc.call_args_list:
        params = call.args[1]
        totals["prompt_tokens"] += params["p_prompt_tokens"]
        totals["completion_tokens"] += params["p_completion_tokens"]
        totals["cost_usd"] += params["p_cost_usd"]

    assert totals["prompt_tokens"] == 12
    assert totals["completion_tokens"] == 13
    assert totals["cost_usd"] == pytest.approx(0.3)


def test_daily_usage_returns_row_with_cost_usd_alias():
    client = _mock_client()
    client.execute.return_value = MagicMock(
        data=[
            {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "estimated_cost_usd": 0.65,
            }
        ]
    )
    store = UsageStore(client)

    result = store.daily_usage("u_1", date(2026, 7, 14))

    assert result == {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
        "cost_usd": 0.65,
    }
    assert "estimated_cost_usd" not in result


def test_daily_usage_returns_zero_defaults_when_missing():
    client = _mock_client()
    store = UsageStore(client)

    result = store.daily_usage("u_1", date(2026, 7, 14))

    assert result == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
    }


def test_monthly_summary_sums_rows_for_month():
    client = _mock_client()
    client.execute.return_value = MagicMock(
        data=[
            {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
                "estimated_cost_usd": 0.65,
            },
            {
                "prompt_tokens": 5,
                "completion_tokens": 10,
                "total_tokens": 15,
                "estimated_cost_usd": 0.35,
            },
        ]
    )
    store = UsageStore(client)

    result = store.monthly_summary("u_1", 2026, 7)

    assert result == {
        "prompt_tokens": 15,
        "completion_tokens": 30,
        "total_tokens": 45,
        "cost_usd": 1.0,
    }
    client.gte.assert_called_once_with("date", "2026-07-01")
    client.lt.assert_called_once_with("date", "2026-08-01")


def test_monthly_summary_year_end_boundary():
    client = _mock_client()
    store = UsageStore(client)

    store.monthly_summary("u_1", 2026, 12)

    client.gte.assert_called_once_with("date", "2026-12-01")
    client.lt.assert_called_once_with("date", "2027-01-01")
