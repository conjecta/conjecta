from __future__ import annotations

import pytest

from math_agent.web.jwt_auth import user_id_for_phone
from math_agent.web import user_ban


@pytest.fixture(autouse=True)
def _clear_ban_cache():
    user_ban.clear_ban_cache()
    yield
    user_ban.clear_ban_cache()


def test_banned_phone_is_detected(monkeypatch):
    monkeypatch.setenv("CONJECTA_BANNED_PHONES", "13800000003")
    user_ban.clear_ban_cache()

    assert user_ban.is_phone_banned("13800000003") is True
    assert user_ban.is_user_banned(user_id=user_id_for_phone("13800000003")) is True
    assert user_ban.is_phone_banned("13800138000") is False
    assert "违规" in user_ban.ban_message() or "禁止" in user_ban.ban_message()


def test_custom_ban_message(monkeypatch):
    monkeypatch.setenv("CONJECTA_BANNED_PHONES", "13800000003")
    monkeypatch.setenv("CONJECTA_BAN_MESSAGE", "账号已封禁测试文案")
    user_ban.clear_ban_cache()

    assert user_ban.ban_message() == "账号已封禁测试文案"
