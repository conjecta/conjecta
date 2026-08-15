from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from math_agent.billing.models import StoredApiKey
from math_agent.net_safety import UnsafeFetchURL
from math_agent.web.app import app

client = TestClient(app)


def test_me_usage_requires_auth(monkeypatch):
    monkeypatch.delenv("CONJECTA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_APP_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.setenv(
        "CONJECTA_JWT_SECRET", "test-jwt-secret-must-be-at-least-32-bytes"
    )

    resp = client.get("/api/me/usage")
    assert resp.status_code == 401


def test_admin_usage_requires_token(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("CONJECTA_ADMIN_TOKEN", "secret")
    resp = client.get("/api/admin/usage")
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Admin token required."


def test_me_usage_returns_daily_and_monthly_summary(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    # Matches UsageStore.daily_usage / monthly_summary contract (cost_usd, not DB column name).
    fake_today = {
        "prompt_tokens": 10,
        "completion_tokens": 20,
        "total_tokens": 30,
        "cost_usd": 0.05,
    }
    fake_month = {
        "prompt_tokens": 100,
        "completion_tokens": 200,
        "total_tokens": 300,
        "cost_usd": 0.5,
    }

    class FakeUsageStore:
        def __init__(self, *_args, **_kwargs):
            pass

        def daily_usage(self, _user_id, _target_date):
            return fake_today

        def monthly_summary(self, _user_id, _year, _month):
            return fake_month

    monkeypatch.setattr("math_agent.web.billing_routes.UsageStore", FakeUsageStore)
    monkeypatch.delenv("CONJECTA_DISABLE_QUOTA", raising=False)
    monkeypatch.setenv("CONJECTA_FREE_TOKENS_PER_DAY", "100")

    resp = client.get("/api/me/usage")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["unlimited_quota"] is False
    assert body["today"]["total_tokens"] == 30
    assert body["today"]["remaining_tokens"] == 70
    assert body["today"]["cost_usd"] == 0.05
    assert body["this_month"]["total_tokens"] == 300
    assert body["this_month"]["cost_usd"] == 0.5


def test_me_usage_unlimited_quota_phone(monkeypatch):
    from math_agent.billing.quota import clear_unlimited_quota_cache
    from math_agent.web.jwt_auth import issue_access_token

    monkeypatch.setenv(
        "CONJECTA_JWT_SECRET", "test-jwt-secret-must-be-at-least-32-bytes"
    )
    monkeypatch.delenv("CONJECTA_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.delenv("CONJECTA_DISABLE_QUOTA", raising=False)
    monkeypatch.setenv("CONJECTA_UNLIMITED_QUOTA_PHONES", "15721590518")
    clear_unlimited_quota_cache()

    class FakeUsageStore:
        def __init__(self, *_args, **_kwargs):
            pass

        def daily_usage(self, _user_id, _target_date):
            return {
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 900_000,
                "cost_usd": 0.05,
            }

        def monthly_summary(self, _user_id, _year, _month):
            return {
                "prompt_tokens": 100,
                "completion_tokens": 200,
                "total_tokens": 300,
                "cost_usd": 0.5,
            }

    monkeypatch.setattr("math_agent.web.billing_routes.UsageStore", FakeUsageStore)
    token, _user, _ttl = issue_access_token("15721590518")
    resp = client.get("/api/me/usage", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["unlimited_quota"] is True
    assert body["today"]["quota_tokens"] == 0
    clear_unlimited_quota_cache()


def test_me_api_key_returns_none_when_not_set(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    fake_client = MagicMock()
    fake_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[{}])
    monkeypatch.setattr(
        "math_agent.knowledge.supabase_client.create_supabase_client",
        lambda **_: fake_client,
    )

    resp = client.get("/api/me/api-key")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "api_key": None}


def test_me_api_key_returns_endpoint_when_set(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    fake_client = MagicMock()
    fake_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{"api_keys_encrypted": "secret", "api_keys_updated_at": "2024-01-01T00:00:00Z"}]
    )
    monkeypatch.setattr(
        "math_agent.knowledge.supabase_client.create_supabase_client",
        lambda **_: fake_client,
    )

    monkeypatch.setattr(
        "math_agent.web.billing_routes.decrypt_api_key",
        lambda _ciphertext: StoredApiKey(
            api_key="sk-hidden", base_url="https://api.example.com/v1"
        ),
    )

    resp = client.get("/api/me/api-key")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["api_key"]["base_url"] == "https://api.example.com/v1"
    assert body["api_key"]["model"] == "gpt-5.6-sol"
    assert body["api_key"]["requires_rebind"] is False
    assert "sk-hidden" not in resp.text
    assert body["api_key"]["updated_at"] == "2024-01-01T00:00:00Z"


def test_me_api_key_marks_legacy_record_for_rebind(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    fake_client = MagicMock()
    fake_client.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
        data=[{"api_keys_encrypted": "legacy", "api_keys_updated_at": "2024-01-01T00:00:00Z"}]
    )
    monkeypatch.setattr(
        "math_agent.knowledge.supabase_client.create_supabase_client",
        lambda **_: fake_client,
    )
    monkeypatch.setattr(
        "math_agent.web.billing_routes.decrypt_api_key",
        lambda _ciphertext: StoredApiKey(
            api_key="sk-hidden", legacy_provider="openai"
        ),
    )

    resp = client.get("/api/me/api-key")
    assert resp.status_code == 200
    assert resp.json()["api_key"] == {
        "base_url": None,
        "model": "gpt-5.6-sol",
        "requires_rebind": True,
        "updated_at": "2024-01-01T00:00:00Z",
    }


def test_set_me_api_key_rejects_empty_key(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    resp = client.post(
        "/api/me/api-key",
        json={"base_url": "https://api.example.com/v1", "api_key": "   "},
    )
    assert resp.status_code == 400


def test_set_me_api_key_stores_encrypted_key(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    captured = {}

    def fake_encrypt(base_url, api_key):
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        return "encrypted-blob"

    async def fake_validate(base_url):
        assert base_url == "https://api.example.com/v1/"
        return "https://api.example.com/v1"

    fake_client = MagicMock()
    monkeypatch.setattr("math_agent.web.billing_routes.encrypt_api_key", fake_encrypt)
    monkeypatch.setattr(
        "math_agent.web.billing_routes.validate_public_https_url", fake_validate
    )
    monkeypatch.setattr(
        "math_agent.knowledge.supabase_client.create_supabase_client",
        lambda **_: fake_client,
    )

    resp = client.post(
        "/api/me/api-key",
        json={
            "base_url": "https://api.example.com/v1/",
            "api_key": "sk-secret",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["base_url"] == "https://api.example.com/v1"
    assert body["model"] == "gpt-5.6-sol"
    assert body["requires_rebind"] is False
    assert captured["base_url"] == "https://api.example.com/v1"
    assert captured["api_key"] == "sk-secret"
    assert "sk-secret" not in resp.text
    update_call = fake_client.table.return_value.update
    assert update_call.call_args[0][0]["api_keys_encrypted"] == "encrypted-blob"


def test_set_me_api_key_rejects_invalid_base_url(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    async def reject_url(_base_url):
        raise UnsafeFetchURL("Base URL must use HTTPS.")

    monkeypatch.setattr(
        "math_agent.web.billing_routes.validate_public_https_url", reject_url
    )

    resp = client.post(
        "/api/me/api-key",
        json={"base_url": "http://127.0.0.1/v1", "api_key": "sk-secret"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "INVALID_API_BASE_URL"


def test_delete_me_api_key_clears_stored_key(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    fake_client = MagicMock()
    monkeypatch.setattr(
        "math_agent.knowledge.supabase_client.create_supabase_client",
        lambda **_: fake_client,
    )

    resp = client.delete("/api/me/api-key")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    update_call = fake_client.table.return_value.update
    assert update_call.call_args[0][0] == {"api_keys_encrypted": None, "api_keys_updated_at": None}


def test_set_me_api_key_returns_503_when_encryption_not_configured(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    async def fake_validate(base_url):
        return base_url

    def failing_encrypt(_base_url, _api_key):
        raise RuntimeError("CONJECTA_API_KEY_ENCRYPTION_KEY is not set")

    monkeypatch.setattr("math_agent.web.billing_routes.encrypt_api_key", failing_encrypt)
    monkeypatch.setattr(
        "math_agent.web.billing_routes.validate_public_https_url", fake_validate
    )

    resp = client.post(
        "/api/me/api-key",
        json={"base_url": "https://api.example.com/v1", "api_key": "sk-secret"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "API key encryption is not configured."


def test_admin_usage_rejects_wrong_token(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("CONJECTA_ADMIN_TOKEN", "secret")
    resp = client.get("/api/admin/usage", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


def test_admin_usage_rejects_invalid_target_date(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("CONJECTA_ADMIN_TOKEN", "secret")

    resp = client.get(
        "/api/admin/usage?date=07-14-2026",
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "date must be YYYY-MM-DD."


def test_admin_usage_rejects_non_calendar_date(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("CONJECTA_ADMIN_TOKEN", "secret")

    resp = client.get(
        "/api/admin/usage?date=2026-02-30",
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "date is not a valid calendar date."


def test_admin_usage_uses_target_date_query_param(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("CONJECTA_ADMIN_TOKEN", "secret")

    fake_client = MagicMock()
    eq_chain = fake_client.table.return_value.select.return_value.eq.return_value
    eq_chain.order.return_value.limit.return_value.offset.return_value.execute.return_value = MagicMock(
        data=[]
    )
    eq_chain.execute.return_value = MagicMock(data=[])

    class FakeUsageStore:
        def __init__(self, *_args, **_kwargs):
            self.client = fake_client

    monkeypatch.setattr("math_agent.web.billing_routes.UsageStore", FakeUsageStore)

    resp = client.get(
        "/api/admin/usage?date=2026-07-14",
        headers={"Authorization": "Bearer secret"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["date"] == "2026-07-14"
    assert fake_client.table.return_value.select.return_value.eq.call_count == 2
    fake_client.table.return_value.select.return_value.eq.assert_called_with("date", "2026-07-14")


def test_admin_usage_returns_daily_usage_for_all_users(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")
    monkeypatch.setenv("CONJECTA_ADMIN_TOKEN", "secret")

    fake_rows = [
        {
            "user_id": "u1",
            "prompt_tokens": 1,
            "completion_tokens": 2,
            "total_tokens": 3,
            "estimated_cost_usd": 0.01,
            "conjecta_users": {"phone_masked": "138****0001"},
        },
        {
            "user_id": "u2",
            "prompt_tokens": 4,
            "completion_tokens": 5,
            "total_tokens": 6,
            "estimated_cost_usd": 0.02,
            "conjecta_users": None,
        },
    ]

    fake_client = MagicMock()
    eq_chain = fake_client.table.return_value.select.return_value.eq.return_value
    # Page of users (paginated query).
    eq_chain.order.return_value.limit.return_value.offset.return_value.execute.return_value = MagicMock(
        data=fake_rows
    )
    # Global day cost sum includes a third user not on this page.
    eq_chain.execute.return_value = MagicMock(
        data=[
            {"estimated_cost_usd": 0.01},
            {"estimated_cost_usd": 0.02},
            {"estimated_cost_usd": 0.04},
        ]
    )

    class FakeUsageStore:
        def __init__(self, *_args, **_kwargs):
            self.client = fake_client

    monkeypatch.setattr("math_agent.web.billing_routes.UsageStore", FakeUsageStore)

    resp = client.get("/api/admin/usage", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["total_cost_usd"] == 0.07
    assert len(body["users"]) == 2
    assert body["users"][0]["phone_masked"] == "138****0001"
    assert body["users"][1]["phone_masked"] == ""


def test_set_me_api_key_returns_503_on_bad_encryption_key(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    async def _valid_url(base_url: str) -> str:
        return base_url

    def _bad_encrypt(_base_url: str, _api_key: str) -> str:
        raise ValueError("CONJECTA_API_KEY_ENCRYPTION_KEY must decode to 32 bytes")

    monkeypatch.setattr("math_agent.web.billing_routes.encrypt_api_key", _bad_encrypt)
    monkeypatch.setattr(
        "math_agent.web.billing_routes.validate_public_https_url", _valid_url
    )

    resp = client.post(
        "/api/me/api-key",
        json={"base_url": "https://api.example.com/v1", "api_key": "sk-test"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "API key encryption is not configured."
