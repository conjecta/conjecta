from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from math_agent.web import app as web_app
from math_agent.web import phone_auth
from math_agent.web.security import ACCESS_TOKEN_COOKIE


def _config_for(tmp_path):
    return SimpleNamespace(
        logging=SimpleNamespace(dir=str(tmp_path), enabled=False, level="INFO"),
        lean=SimpleNamespace(enabled=False, mathlib_dep=False),
    )


@pytest.fixture
def auth_env(monkeypatch, tmp_path):
    monkeypatch.delenv("CONJECTA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_APP_TOKEN", raising=False)
    monkeypatch.setenv(
        "CONJECTA_JWT_SECRET", "test-jwt-secret-must-be-at-least-32-bytes"
    )
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "test-ak")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "test-sk")
    monkeypatch.setenv("ALIYUN_DYPNS_SIGN_NAME", "测试签名")
    monkeypatch.setenv("CONJECTA_SMS_DEBUG", "1")
    monkeypatch.setattr(web_app, "load_config", lambda: _config_for(tmp_path))
    return TestClient(web_app.app)


def test_auth_config(auth_env):
    resp = auth_env.get("/api/auth/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["phone_auth_enabled"] is True
    assert data["sms_configured"] is True
    assert "cloud_storage_configured" in data
    assert data["cloud_storage_configured"] is False


def test_send_and_verify_code_flow(auth_env, monkeypatch):
    async def fake_send(phone: str):
        return {"out_id": "out-123", "verify_code": "654321"}

    async def fake_check(phone: str, code: str, *, out_id: str | None = None):
        assert phone == "13812345678"
        assert code == "654321"
        assert out_id == "out-123"
        return True

    calls: list[str] = []

    def fake_upsert(phone: str):
        calls.append(phone)
        return {"id": "u_test", "phone": phone}

    monkeypatch.setattr(phone_auth, "send_sms_verify_code", fake_send)
    monkeypatch.setattr(phone_auth, "check_sms_verify_code", fake_check)
    monkeypatch.setattr("math_agent.web.user_store.upsert_user_on_login", fake_upsert)
    monkeypatch.setattr(
        "math_agent.knowledge.supabase_client.service_role_configured", lambda: False
    )

    send = auth_env.post("/api/auth/send-code", json={"phone": "13812345678"})
    assert send.status_code == 200
    send_data = send.json()
    assert send_data["out_id"] == "out-123"
    assert "debug_verify_code" not in send_data

    verify = auth_env.post(
        "/api/auth/verify-code",
        json={"phone": "13812345678", "code": "654321", "out_id": "out-123"},
    )
    assert verify.status_code == 200
    assert ACCESS_TOKEN_COOKIE in verify.cookies
    verify_data = verify.json()
    assert verify_data["token_type"] == "Bearer"
    assert verify_data["access_token"]
    assert calls == ["13812345678"]

    me = auth_env.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {verify_data['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["user"]["phone"] == "138****5678"


def test_verify_code_rejects_invalid(auth_env, monkeypatch):
    async def fake_send(_phone: str):
        return {"out_id": "out-456"}

    async def fake_check(_phone: str, _code: str, *, out_id: str | None = None):
        return False

    monkeypatch.setattr(phone_auth, "send_sms_verify_code", fake_send)
    monkeypatch.setattr(phone_auth, "check_sms_verify_code", fake_check)

    auth_env.post("/api/auth/send-code", json={"phone": "13812345678"})
    resp = auth_env.post(
        "/api/auth/verify-code",
        json={"phone": "13812345678", "code": "000000"},
    )
    assert resp.status_code == 401


def test_logout_clears_cookie(auth_env, monkeypatch):
    async def fake_send(_phone: str):
        return {"out_id": "out-789"}

    async def fake_check(_phone: str, _code: str, *, out_id: str | None = None):
        return True

    monkeypatch.setattr(phone_auth, "send_sms_verify_code", fake_send)
    monkeypatch.setattr(phone_auth, "check_sms_verify_code", fake_check)

    verify = auth_env.post(
        "/api/auth/verify-code",
        json={"phone": "13812345678", "code": "654321", "out_id": "out-789"},
    )
    assert ACCESS_TOKEN_COOKIE in verify.cookies

    logout = auth_env.post("/api/auth/logout")
    assert logout.status_code == 200
    assert logout.cookies.get(ACCESS_TOKEN_COOKIE) in ("", None)


def test_sms_bypass_phone_logs_in_without_code(auth_env, monkeypatch):
    phone_auth.clear_sms_bypass_cache()
    monkeypatch.setenv("CONJECTA_SMS_BYPASS_PHONES", "13800000001")
    phone_auth.clear_sms_bypass_cache()

    sent: list[str] = []

    async def fake_send(phone: str):
        sent.append(phone)
        return {"out_id": "should-not-send"}

    async def fake_check(*_args, **_kwargs):
        raise AssertionError("SMS check must not run for bypass phones")

    monkeypatch.setattr(phone_auth, "send_sms_verify_code", fake_send)
    monkeypatch.setattr(phone_auth, "check_sms_verify_code", fake_check)
    monkeypatch.setattr(
        "math_agent.knowledge.supabase_client.service_role_configured", lambda: False
    )

    send = auth_env.post("/api/auth/send-code", json={"phone": "13800000001"})
    assert send.status_code == 200
    send_data = send.json()
    assert send_data["sms_bypass"] is True
    assert send_data["access_token"]
    assert ACCESS_TOKEN_COOKIE in send.cookies
    assert sent == []

    me = auth_env.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["phone"] == "138****0001"

    verify = auth_env.post(
        "/api/auth/verify-code",
        json={"phone": "13800000001", "code": "0000"},
    )
    assert verify.status_code == 200
    assert verify.json()["sms_bypass"] is True


def test_cookie_scheme_header_is_trusted_only_from_configured_proxy(monkeypatch):
    untrusted_request = SimpleNamespace(
        headers={"x-forwarded-proto": "http"},
        client=SimpleNamespace(host="203.0.113.10"),
        url=SimpleNamespace(scheme="https"),
    )
    trusted_request = SimpleNamespace(
        headers={"x-forwarded-proto": "https"},
        client=SimpleNamespace(host="127.0.0.1"),
        url=SimpleNamespace(scheme="http"),
    )
    monkeypatch.setenv("CONJECTA_TRUSTED_PROXY_CIDRS", "127.0.0.0/8")

    assert phone_auth._cookie_secure(untrusted_request) is True
    assert phone_auth._cookie_secure(trusted_request) is True
