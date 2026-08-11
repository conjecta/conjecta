from __future__ import annotations

import pytest

from math_agent.web.jwt_auth import (
    decode_access_token,
    issue_access_token,
    jwt_secret,
    normalize_phone,
    phone_auth_enabled,
    user_id_for_phone,
)


def test_configured_jwt_secret_must_be_at_least_32_bytes(monkeypatch):
    monkeypatch.setenv("CONJECTA_JWT_SECRET", "x" * 31)

    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        jwt_secret()


def test_normalize_phone_strips_country_prefix():
    assert normalize_phone("8613812345678") == "13812345678"
    assert normalize_phone("138-1234-5678") == "13812345678"


def test_normalize_phone_rejects_invalid():
    with pytest.raises(ValueError):
        normalize_phone("12345")


def test_issue_and_decode_access_token(monkeypatch):
    monkeypatch.setenv(
        "CONJECTA_JWT_SECRET", "test-jwt-secret-must-be-at-least-32-bytes"
    )
    monkeypatch.setenv("CONJECTA_JWT_TTL_SECONDS", "3600")
    assert phone_auth_enabled()

    phone = "13812345678"
    token, user, ttl = issue_access_token(phone)
    assert ttl == 3600
    assert user.phone == phone
    assert user.user_id == user_id_for_phone(phone)

    decoded = decode_access_token(token)
    assert decoded is not None
    assert decoded.user_id == user.user_id
    assert decoded.phone == phone


def test_decode_rejects_tampered_token(monkeypatch):
    monkeypatch.setenv(
        "CONJECTA_JWT_SECRET", "test-jwt-secret-must-be-at-least-32-bytes"
    )
    token, _, _ = issue_access_token("13812345678")
    assert decode_access_token(token + "x") is None
