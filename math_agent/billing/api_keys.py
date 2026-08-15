from __future__ import annotations

import base64
import json
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from math_agent.billing.models import StoredApiKey

USER_API_MODEL = "gpt-5.6-sol"
USER_API_PROVIDER = "user_endpoint"
_FORMAT_VERSION = "v2"
_LEGACY_FORMAT_VERSION = "v1"


def _encryption_key() -> bytes:
    raw = os.getenv("CONJECTA_API_KEY_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise RuntimeError("CONJECTA_API_KEY_ENCRYPTION_KEY is not set")
    key = base64.urlsafe_b64decode(raw.encode())
    if len(key) != 32:
        raise ValueError("CONJECTA_API_KEY_ENCRYPTION_KEY must decode to 32 bytes")
    return key


def encrypt_api_key(base_url: str, api_key: str) -> str:
    payload = json.dumps({"base_url": base_url, "api_key": api_key}).encode()
    key = _encryption_key()
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, payload, None)
    encoded_nonce = base64.urlsafe_b64encode(nonce).decode("ascii")
    encoded_ct = base64.urlsafe_b64encode(ciphertext).decode("ascii")
    return f"{_FORMAT_VERSION}:{encoded_nonce}:{encoded_ct}"


def decrypt_api_key(ciphertext: str) -> StoredApiKey:
    parts = ciphertext.split(":")
    if len(parts) != 3 or parts[0] not in {_FORMAT_VERSION, _LEGACY_FORMAT_VERSION}:
        raise ValueError("Unsupported ciphertext format")
    nonce = base64.urlsafe_b64decode(parts[1].encode())
    ct = base64.urlsafe_b64decode(parts[2].encode())
    key = _encryption_key()
    try:
        payload = AESGCM(key).decrypt(nonce, ct, None)
    except InvalidTag as exc:
        raise ValueError("Ciphertext authentication failed") from exc
    data = json.loads(payload)
    api_key = data.get("api_key")
    if not isinstance(api_key, str) or not api_key:
        raise ValueError("Encrypted API key payload is invalid")
    if parts[0] == _LEGACY_FORMAT_VERSION:
        provider = data.get("provider")
        if not isinstance(provider, str) or not provider:
            raise ValueError("Legacy API key payload is invalid")
        return StoredApiKey(api_key=api_key, legacy_provider=provider)
    base_url = data.get("base_url")
    if not isinstance(base_url, str) or not base_url:
        raise ValueError("Encrypted API key payload is invalid")
    return StoredApiKey(api_key=api_key, base_url=base_url)
