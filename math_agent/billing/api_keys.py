from __future__ import annotations

import base64
import json
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from math_agent.billing.models import StoredApiKey

# Model used for requests made with a user-provided (BYOK) endpoint key. The
# hosted platform keeps the pinned default; self-hosted deployments may point
# their own OpenAI-compatible endpoint at any model by setting
# CONJECTA_USER_API_MODEL (e.g. "gpt-4o"). Resolved once at import time.
USER_MODEL_MAP: dict[str, str] = {
    "openai": os.getenv("CONJECTA_USER_API_MODEL", "").strip() or "gpt-5.6-sol",
}

_FORMAT_VERSION = "v1"


def _encryption_key() -> bytes:
    raw = os.getenv("CONJECTA_API_KEY_ENCRYPTION_KEY", "").strip()
    if not raw:
        raise RuntimeError("CONJECTA_API_KEY_ENCRYPTION_KEY is not set")
    key = base64.urlsafe_b64decode(raw.encode())
    if len(key) != 32:
        raise ValueError("CONJECTA_API_KEY_ENCRYPTION_KEY must decode to 32 bytes")
    return key


def encrypt_api_key(provider: str, api_key: str) -> str:
    payload = json.dumps({"provider": provider, "api_key": api_key}).encode()
    key = _encryption_key()
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(key).encrypt(nonce, payload, None)
    encoded_nonce = base64.urlsafe_b64encode(nonce).decode("ascii")
    encoded_ct = base64.urlsafe_b64encode(ciphertext).decode("ascii")
    return f"{_FORMAT_VERSION}:{encoded_nonce}:{encoded_ct}"


def decrypt_api_key(ciphertext: str) -> StoredApiKey:
    parts = ciphertext.split(":")
    if len(parts) != 3 or parts[0] != _FORMAT_VERSION:
        raise ValueError("Unsupported ciphertext format")
    nonce = base64.urlsafe_b64decode(parts[1].encode())
    ct = base64.urlsafe_b64decode(parts[2].encode())
    key = _encryption_key()
    try:
        payload = AESGCM(key).decrypt(nonce, ct, None)
    except InvalidTag as exc:
        raise ValueError("Ciphertext authentication failed") from exc
    data = json.loads(payload)
    return StoredApiKey(provider=data["provider"], api_key=data["api_key"])


def get_user_backend_model(provider: str) -> str:
    if provider not in USER_MODEL_MAP:
        raise ValueError(f"Unsupported provider: {provider}")
    return USER_MODEL_MAP[provider]
