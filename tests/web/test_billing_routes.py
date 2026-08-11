from unittest.mock import MagicMock

from fastapi.testclient import TestClient

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
    monkeypatch.setenv("CONJECTA_UNLIMITED_QUOTA_PHONES", "13800000001")
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
    token, _user, _ttl = issue_access_token("13800000001")
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


def test_me_api_key_returns_provider_when_set(monkeypatch):
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

    class FakeStoredKey:
        provider = "openai"

    monkeypatch.setattr(
        "math_agent.web.billing_routes.decrypt_api_key",
        lambda _ciphertext: FakeStoredKey(),
    )

    resp = client.get("/api/me/api-key")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["api_key"]["provider"] == "openai"
    assert body["api_key"]["updated_at"] == "2024-01-01T00:00:00Z"


def test_set_me_api_key_rejects_invalid_provider(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    resp = client.post("/api/me/api-key", json={"provider": "invalid", "api_key": "sk-xxx"})
    assert resp.status_code == 400


def test_set_me_api_key_rejects_empty_key(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    resp = client.post("/api/me/api-key", json={"provider": "openai", "api_key": "   "})
    assert resp.status_code == 400


def test_set_me_api_key_stores_encrypted_key(monkeypatch):
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_ALLOW_UNAUTHENTICATED", "1")

    captured = {}

    def fake_encrypt(provider, api_key):
        captured["provider"] = provider
        captured["api_key"] = api_key
        return "encrypted-blob"

    fake_client = MagicMock()
    monkeypatch.setattr("math_agent.web.billing_routes.encrypt_api_key", fake_encrypt)
    monkeypatch.setattr(
        "math_agent.knowledge.supabase_client.create_supabase_client",
        lambda **_: fake_client,
    )

    resp = client.post("/api/me/api-key", json={"provider": "openai", "api_key": "sk-secret"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["provider"] == "openai"
    assert captured["provider"] == "openai"
    assert captured["api_key"] == "sk-secret"
    update_call = fake_client.table.return_value.update
    assert update_call.call_args[0][0]["api_keys_encrypted"] == "encrypted-blob"


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

    def failing_encrypt(_provider, _api_key):
        raise RuntimeError("CONJECTA_API_KEY_ENCRYPTION_KEY is not set")

    monkeypatch.setattr("math_agent.web.billing_routes.encrypt_api_key", failing_encrypt)

    resp = client.post("/api/me/api-key", json={"provider": "openai", "api_key": "sk-secret"})
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

    def _bad_encrypt(_provider: str, _api_key: str) -> str:
        raise ValueError("CONJECTA_API_KEY_ENCRYPTION_KEY must decode to 32 bytes")

    monkeypatch.setattr("math_agent.web.billing_routes.encrypt_api_key", _bad_encrypt)

    resp = client.post(
        "/api/me/api-key",
        json={"provider": "openai", "api_key": "sk-test"},
    )
    assert resp.status_code == 503
    assert resp.json()["detail"] == "API key encryption is not configured."
