"""Pytest configuration — keep tests isolated from local .env secrets."""
from __future__ import annotations

import os

# Set sensitive env vars to empty strings before any app module is imported.
# app.py calls load_dotenv(override=False), so these empties prevent .env
# from leaking into the test process.
_SENSITIVE_VARS = [
    # LLM providers
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    # Search / embeddings
    "TAVILY_API_KEY",
    # Supabase
    "SUPABASE_URL",
    "SUPABASE_ANON_KEY",
    "SUPABASE_SERVICE_ROLE_KEY",
    "VITE_SUPABASE_URL",
    "VITE_SUPABASE_ANON_KEY",
    "NEXT_PUBLIC_SUPABASE_URL",
    "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    # Application tokens
    "CONJECTA_AUTH_TOKEN",
    "CONJECTA_APP_TOKEN",
    "CONJECTA_ADMIN_TOKEN",
    "CONJECTA_JWT_SECRET",
    "CONJECTA_API_KEY_ENCRYPTION_KEY",
    # Aliyun SMS / phone verification
    "ALIYUN_ACCESS_KEY_ID",
    "ALIYUN_ACCESS_KEY_SECRET",
    "ALIYUN_DYPNS_SIGN_NAME",
    "ALIYUN_DYPNS_TEMPLATE_CODE",
    "ALIYUN_SMS_TEMPLATE_CODE",
]

for name in _SENSITIVE_VARS:
    os.environ[name] = ""

# Tests do not configure a billing backend by default. Quota-enforcement tests
# clear this and enable Supabase stubs explicitly.
os.environ["CONJECTA_DISABLE_QUOTA"] = "1"
