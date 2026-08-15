from __future__ import annotations

import logging
import os

from math_agent.billing.api_keys import USER_API_MODEL
from math_agent.billing.models import StoredApiKey
from math_agent.config import LLMConfig, CriticConfig, ProverConfig
from math_agent.llm.base import LLMBackend

log = logging.getLogger("math_agent.llm.factory")

DEEPSEEK_V4_PRO_MODEL = "deepseek-v4-pro"
DEFAULT_DEEPSEEK_MODEL_STRING = f"deepseek/{DEEPSEEK_V4_PRO_MODEL}"
_LEGACY_DEEPSEEK_ALIASES = frozenset({"deepseek-chat", "deepseek-reasoner"})


# Provider -> (base_url, api_key_env)
OPENAI_COMPATIBLE_PROVIDERS: dict[str, tuple[str | None, str]] = {
    "openai": (None, "OPENAI_API_KEY"),
    "shengsuanyun": ("https://router.shengsuanyun.com/api/v1", "SHENGSUANYUN_API_KEY"),
    "kimi": ("https://api.moonshot.ai/v1", "KIMI_API_KEY"),
}


def create_backend(
    config: LLMConfig | CriticConfig | ProverConfig,
    api_key: str | None = None,
    *,
    follow_redirects: bool = True,
) -> LLMBackend:
    log.info(
        "Creating backend provider=%s model=%s temperature=%s api_key_present=%s",
        config.provider,
        config.model,
        config.temperature,
        bool(api_key),
    )
    backend: LLMBackend
    if config.provider == "deepseek":
        from math_agent.llm.deepseek import DeepSeekBackend
        model = normalize_model_string(f"deepseek/{config.model}").split("/", 1)[1]
        backend = DeepSeekBackend(
            model=model,
            default_temperature=config.temperature,
            api_key=api_key,
            timeout_seconds=float(getattr(config, "timeout_seconds", 120.0)),
            retry_max_attempts=int(getattr(config, "retry_max_attempts", 3)),
            retry_base_seconds=float(getattr(config, "retry_base_seconds", 5.0)),
        )
    elif config.provider in OPENAI_COMPATIBLE_PROVIDERS:
        from math_agent.llm.openai import OpenAICompatibleBackend
        base_url, api_key_env = OPENAI_COMPATIBLE_PROVIDERS[config.provider]
        # Explicit base_url (e.g. a self-hosted prover endpoint) overrides the
        # provider default.
        explicit_base_url = getattr(config, "base_url", "")
        if explicit_base_url:
            base_url = explicit_base_url
        if config.provider == "kimi":
            # Kimi Code managed keys (sk-kimi-...) use the coding endpoint;
            # Kimi Platform keys use the moonshot.ai default.
            base_url = os.getenv("KIMI_BASE_URL") or base_url
        backend = OpenAICompatibleBackend(
            model=config.model,
            default_temperature=config.temperature,
            base_url=base_url,
            api_key_env=api_key_env,
            api_key=api_key,
            timeout_seconds=float(getattr(config, "timeout_seconds", 120.0)),
            provider_name=config.provider,
            retry_max_attempts=int(getattr(config, "retry_max_attempts", 3)),
            retry_base_seconds=float(getattr(config, "retry_base_seconds", 5.0)),
            follow_redirects=follow_redirects,
        )
    else:
        raise ValueError(
            f"Unknown LLM provider: {config.provider}. "
            "Supported: openai, deepseek, shengsuanyun, kimi"
        )
    # Per-problem LLM call budget (llm.max_calls_per_problem); ReActAgent reads
    # it off the backend to enforce the cap. Only LLMConfig carries the field.
    # setattr keeps the LLMBackend Protocol free of an attribute the fake
    # backends in tests do not define.
    setattr(
        backend,
        "max_calls_per_problem",
        int(getattr(config, "max_calls_per_problem", 200) or 200),
    )
    return backend


def create_prover_backend(config: ProverConfig) -> LLMBackend | None:
    """Backend for the formal-prover role, or None when the role is disabled.

    Configure ``[llm.prover]`` with a model (and optionally a custom
    ``base_url`` for a self-hosted DeepSeek-Prover / Kimina-Prover endpoint)
    to route tactic generation away from the main reasoning model.
    """
    if not isinstance(config, ProverConfig):
        return None
    if not config.model.strip():
        return None
    return create_backend(config, api_key=config.api_key or None)


def normalize_model_string(model_string: str) -> str:
    """Map legacy DeepSeek aliases to deepseek-v4-pro."""
    model_string = model_string.strip()
    if "/" not in model_string:
        if model_string.startswith("deepseek"):
            return DEFAULT_DEEPSEEK_MODEL_STRING
        return model_string
    provider, model = model_string.split("/", 1)
    if provider == "deepseek" and model in _LEGACY_DEEPSEEK_ALIASES:
        log.info(
            "Normalizing legacy DeepSeek model %s -> %s",
            model_string,
            DEFAULT_DEEPSEEK_MODEL_STRING,
        )
        return DEFAULT_DEEPSEEK_MODEL_STRING
    return model_string


def create_backend_from_model_string(
    model_string: str,
    temperature: float = 0.7,
    api_key: str | None = None,
    timeout_seconds: float = 120.0,
) -> LLMBackend:
    """Create a backend from a 'provider/model' string (used by the web UI)."""
    model_string = normalize_model_string(model_string)
    if "/" not in model_string:
        raise ValueError(
            f"Invalid model format '{model_string}'. Expected 'provider/model'."
        )
    provider, model = model_string.split("/", 1)
    log.debug(
        "Parsed model string provider=%s model=%s temperature=%s",
        provider,
        model,
        temperature,
    )
    config = LLMConfig(
        provider=provider,
        model=model,
        temperature=temperature,
        timeout_seconds=timeout_seconds,
    )
    return create_backend(config, api_key=api_key)


def create_backend_for_user(
    config: LLMConfig | CriticConfig | ProverConfig,
    user_api_key: StoredApiKey | None = None,
) -> LLMBackend:
    if user_api_key is not None:
        if not user_api_key.base_url:
            raise ValueError("User API endpoint must be rebound with a Base URL.")
        user_config = LLMConfig(
            provider="openai",
            model=USER_API_MODEL,
            temperature=config.temperature,
            base_url=user_api_key.base_url,
            timeout_seconds=float(getattr(config, "timeout_seconds", 120.0)),
            retry_max_attempts=int(getattr(config, "retry_max_attempts", 3)),
            retry_base_seconds=float(getattr(config, "retry_base_seconds", 5.0)),
            max_calls_per_problem=int(getattr(config, "max_calls_per_problem", 200)),
        )
        return create_backend(
            user_config,
            api_key=user_api_key.api_key,
            follow_redirects=False,
        )
    return create_backend(config)
