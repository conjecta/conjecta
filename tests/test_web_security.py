from __future__ import annotations

import pytest

from math_agent.web.security import (
    InMemoryRateLimiter,
    request_rate_key,
    require_http_app_access,
)


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key.lower(), default)


class FakeRequest:
    def __init__(self, headers=None, host="127.0.0.1", path="/api/test"):
        self.headers = FakeHeaders({(k or "").lower(): v for k, v in (headers or {}).items()})
        self.client = type("Client", (), {"host": host})()
        self.url = type("URL", (), {"path": path})()


def test_rate_limiter_blocks_after_limit():
    limiter = InMemoryRateLimiter(limit_per_minute=2)
    limiter.check("client", now=100.0)
    limiter.check("client", now=101.0)
    try:
        limiter.check("client", now=102.0)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 429
    else:
        raise AssertionError("expected rate limit exception")


def test_optional_app_auth_protects_api(monkeypatch):
    monkeypatch.setenv("CONJECTA_AUTH_TOKEN", "app-token")

    with pytest.raises(Exception) as exc:
        require_http_app_access(FakeRequest())
    assert exc.value.status_code == 401

    require_http_app_access(FakeRequest({"x-conjecta-auth-token": "app-token"}))


def test_missing_app_auth_allows_loopback(monkeypatch):
    monkeypatch.delenv("CONJECTA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_APP_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_ALLOW_UNAUTHENTICATED", raising=False)

    require_http_app_access(FakeRequest(host="127.0.0.1"))


def test_missing_app_auth_blocks_non_local_request(monkeypatch):
    monkeypatch.delenv("CONJECTA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_APP_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)

    with pytest.raises(Exception) as exc:
        require_http_app_access(FakeRequest(host="203.0.113.10"))
    assert exc.value.status_code == 403


def test_untrusted_peer_cannot_spoof_loopback_identity(monkeypatch):
    monkeypatch.delenv("CONJECTA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_APP_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_TRUSTED_PROXY_CIDRS", "10.0.0.0/8")
    request = FakeRequest(
        {
            "x-forwarded-for": "127.0.0.1",
            "x-real-ip": "127.0.0.1",
        },
        host="203.0.113.10",
    )

    with pytest.raises(Exception) as exc:
        require_http_app_access(request)

    assert exc.value.status_code == 403
    assert request_rate_key(request) == "203.0.113.10:/api/test"


def test_trusted_proxy_uses_valid_overwritten_real_ip(monkeypatch):
    monkeypatch.setenv("CONJECTA_TRUSTED_PROXY_CIDRS", "127.0.0.0/8, ::1/128")
    request = FakeRequest(
        {
            "x-forwarded-for": "127.0.0.1, 198.51.100.20",
            "x-real-ip": "198.51.100.20",
        },
        host="127.0.0.1:4312",
    )

    assert request_rate_key(request) == "198.51.100.20:/api/test"


@pytest.mark.parametrize(
    "headers,expected_identity",
    [
        ({}, "proxy-client-unknown"),
        (
            {"x-real-ip": "not-an-ip", "x-forwarded-for": "127.0.0.1"},
            "proxy-client-invalid",
        ),
    ],
)
def test_trusted_proxy_missing_or_invalid_real_ip_fails_closed_for_auth_and_rate(
    monkeypatch,
    headers,
    expected_identity,
):
    monkeypatch.delenv("CONJECTA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_APP_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.delenv("CONJECTA_JWT_SECRET", raising=False)
    monkeypatch.setenv("CONJECTA_TRUSTED_PROXY_CIDRS", "127.0.0.0/8, ::1/128")
    request = FakeRequest(headers, host="127.0.0.1:4312")

    with pytest.raises(Exception) as exc:
        require_http_app_access(request)

    assert exc.value.status_code == 403
    assert request_rate_key(request) == f"{expected_identity}:/api/test"


def test_missing_jwt_blocks_non_local_when_phone_auth_enabled(monkeypatch):
    monkeypatch.delenv("CONJECTA_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_APP_TOKEN", raising=False)
    monkeypatch.delenv("CONJECTA_ALLOW_UNAUTHENTICATED", raising=False)
    monkeypatch.setenv(
        "CONJECTA_JWT_SECRET", "test-jwt-secret-must-be-at-least-32-bytes"
    )

    with pytest.raises(Exception) as exc:
        require_http_app_access(FakeRequest(host="203.0.113.10"))
    assert exc.value.status_code == 401


def test_jwt_bearer_grants_api_access(monkeypatch):
    monkeypatch.setenv("CONJECTA_AUTH_TOKEN", "app-token")
    monkeypatch.setenv(
        "CONJECTA_JWT_SECRET", "test-jwt-secret-must-be-at-least-32-bytes"
    )

    from math_agent.web.jwt_auth import issue_access_token

    token, _, _ = issue_access_token("13812345678")
    require_http_app_access(FakeRequest({"authorization": f"Bearer {token}"}))
