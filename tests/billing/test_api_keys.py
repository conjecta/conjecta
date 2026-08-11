import base64

import pytest

from math_agent.billing.api_keys import (
    decrypt_api_key,
    encrypt_api_key,
    get_user_backend_model,
    USER_MODEL_MAP,
)


@pytest.fixture(autouse=True)
def _set_key(monkeypatch):
    monkeypatch.setenv(
        "CONJECTA_API_KEY_ENCRYPTION_KEY",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",  # 32 zero bytes base64
    )


def test_roundtrip():
    ct = encrypt_api_key("openai", "sk-test")
    assert ct != "sk-test"
    assert ct.startswith("v1:")
    key = decrypt_api_key(ct)
    assert key.provider == "openai"
    assert key.api_key == "sk-test"


def test_user_model_map():
    assert USER_MODEL_MAP == {"openai": "gpt-5.6-sol"}


def test_get_user_backend_model():
    assert get_user_backend_model("openai") == "gpt-5.6-sol"


def test_get_user_backend_model_unsupported():
    with pytest.raises(ValueError, match="Unsupported provider"):
        get_user_backend_model("unknown")


def test_missing_encryption_key(monkeypatch):
    monkeypatch.delenv("CONJECTA_API_KEY_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CONJECTA_API_KEY_ENCRYPTION_KEY is not set"):
        encrypt_api_key("openai", "sk-test")


def test_wrong_encryption_key_length(monkeypatch):
    monkeypatch.setenv(
        "CONJECTA_API_KEY_ENCRYPTION_KEY",
        base64.urlsafe_b64encode(b"short").decode(),
    )
    with pytest.raises(ValueError, match="32 bytes"):
        encrypt_api_key("openai", "sk-test")


def test_tampered_ciphertext():
    ct = encrypt_api_key("openai", "sk-test")
    tampered = ct[:-1] + ("X" if ct[-1] != "X" else "Y")
    with pytest.raises(ValueError):
        decrypt_api_key(tampered)


def test_tampered_format():
    with pytest.raises(ValueError, match="Unsupported ciphertext format"):
        decrypt_api_key("not:a:valid:format")
