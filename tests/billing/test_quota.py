from math_agent.billing.quota import (
    FREE_TOKENS_PER_DAY,
    clear_unlimited_quota_cache,
    is_allowed,
    is_quota_unlimited,
    quota_disabled,
    remaining_tokens,
)


def test_is_allowed_under_quota(monkeypatch):
    monkeypatch.delenv("CONJECTA_DISABLE_QUOTA", raising=False)
    assert is_allowed(0) is True
    assert is_allowed(FREE_TOKENS_PER_DAY - 1) is True


def test_is_allowed_at_quota(monkeypatch):
    monkeypatch.delenv("CONJECTA_DISABLE_QUOTA", raising=False)
    assert is_allowed(FREE_TOKENS_PER_DAY) is False


def test_remaining_tokens(monkeypatch):
    monkeypatch.delenv("CONJECTA_DISABLE_QUOTA", raising=False)
    assert remaining_tokens(100_000) == FREE_TOKENS_PER_DAY - 100_000
    assert remaining_tokens(FREE_TOKENS_PER_DAY) == 0


def test_custom_quota_via_env(monkeypatch):
    monkeypatch.delenv("CONJECTA_DISABLE_QUOTA", raising=False)
    monkeypatch.setenv("CONJECTA_FREE_TOKENS_PER_DAY", "100000")
    from math_agent.billing.quota import free_tokens_per_day

    assert free_tokens_per_day() == 100_000


def test_quota_disabled_via_env(monkeypatch):
    monkeypatch.setenv("CONJECTA_DISABLE_QUOTA", "1")
    assert quota_disabled() is True
    assert is_allowed(FREE_TOKENS_PER_DAY * 10) is True
    assert remaining_tokens(FREE_TOKENS_PER_DAY * 10) == FREE_TOKENS_PER_DAY


def test_quota_enforced_by_default(monkeypatch):
    monkeypatch.delenv("CONJECTA_DISABLE_QUOTA", raising=False)
    assert quota_disabled() is False
    assert is_allowed(FREE_TOKENS_PER_DAY) is False


def test_unlimited_quota_phone_skips_limit(monkeypatch):
    monkeypatch.delenv("CONJECTA_DISABLE_QUOTA", raising=False)
    monkeypatch.setenv("CONJECTA_UNLIMITED_QUOTA_PHONES", "13800000001")
    clear_unlimited_quota_cache()
    assert is_quota_unlimited(phone="13800000001") is True
    assert is_allowed(FREE_TOKENS_PER_DAY * 10, phone="13800000001") is True
    assert is_allowed(FREE_TOKENS_PER_DAY * 10, phone="13800138000") is False
    clear_unlimited_quota_cache()
