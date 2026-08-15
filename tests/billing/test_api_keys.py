import base64
import json
import secrets

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from math_agent.billing.api_keys import (
    USER_API_MODEL,
    USER_API_PROVIDER,
    decrypt_api_key,
    encrypt_api_key,
)


@pytest.fixture(autouse=True)
def _set_key(monkeypatch):
    monkeypatch.setenv(
        "CONJECTA_API_KEY_ENCRYPTION_KEY",
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",  # 32 zero bytes base64
    )


def test_roundtrip():
    ct = encrypt_api_key("https://api.example.com/v1", "sk-test")
    assert ct != "sk-test"
    assert ct.startswith("v2:")
    key = decrypt_api_key(ct)
    assert key.base_url == "https://api.example.com/v1"
    assert key.api_key == "sk-test"
    assert key.legacy_provider == ""


def test_fixed_user_endpoint_identity():
    assert USER_API_MODEL == "gpt-5.6-sol"
    assert USER_API_PROVIDER == "user_endpoint"


def test_decrypts_legacy_provider_payload():
    key = base64.urlsafe_b64decode(
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=".encode()
    )
    nonce = secrets.token_bytes(12)
    payload = json.dumps({"provider": "openai", "api_key": "sk-old"}).encode()
    ciphertext = AESGCM(key).encrypt(nonce, payload, None)
    stored = "v1:{}:{}".format(
        base64.urlsafe_b64encode(nonce).decode(),
        base64.urlsafe_b64encode(ciphertext).decode(),
    )

    decrypted = decrypt_api_key(stored)
    assert decrypted.api_key == "sk-old"
    assert decrypted.base_url == ""
    assert decrypted.legacy_provider == "openai"


def test_missing_encryption_key(monkeypatch):
    monkeypatch.delenv("CONJECTA_API_KEY_ENCRYPTION_KEY", raising=False)
    with pytest.raises(RuntimeError, match="CONJECTA_API_KEY_ENCRYPTION_KEY is not set"):
        encrypt_api_key("https://api.example.com/v1", "sk-test")


def test_wrong_encryption_key_length(monkeypatch):
    monkeypatch.setenv(
        "CONJECTA_API_KEY_ENCRYPTION_KEY",
        base64.urlsafe_b64encode(b"short").decode(),
    )
    with pytest.raises(ValueError, match="32 bytes"):
        encrypt_api_key("https://api.example.com/v1", "sk-test")


def test_tampered_ciphertext():
    ct = encrypt_api_key("https://api.example.com/v1", "sk-test")
    tampered = ct[:-1] + ("X" if ct[-1] != "X" else "Y")
    with pytest.raises(ValueError):
        decrypt_api_key(tampered)


def test_tampered_format():
    with pytest.raises(ValueError, match="Unsupported ciphertext format"):
        decrypt_api_key("not:a:valid:format")
