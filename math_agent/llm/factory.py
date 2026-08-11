from __future__ import annotations

import logging
import os

from math_agent.billing.api_keys import USER_MODEL_MAP
from math_agent.billing.models import StoredApiKey
from math_agent.config import LLMConfig, CriticConfig, ProverConfig
from math_agent.llm.base import LLMBackend

log = logging.getLogger("math_agent.llm.factory")

# Provider -> (base_url, api_key_env)
OPENAI_COMPATIBLE_PROVIDERS: dict[str, tuple[str | None, str]] = {
    "openai": (None, "OPENAI_API_KEY"),
    "kimi": ("https://api.moonshot.ai/v1", "KIMI_API_KEY"),
}


def create_backend(
    config: LLMConfig | CriticConfig | ProverConfig, api_key: str | None = None
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
        )
    else:
        raise ValueError(
            f"Unknown LLM provider: {config.provider}. "
            "Supported: openai, deepseek, kimi"
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
    """Normalize whitespace without rewriting provider model identifiers."""
    return model_string.strip()


def create_backend_from_model_string(
    model_string: str,
    temperature: float = 0.7,
    api_key: str | None = None,
    timeout_seconds: float = 120.0,
    base_url: str | None = None,
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
        base_url=base_url or "",
    )
    return create_backend(config, api_key=api_key)


def create_backend_for_user(
    config: LLMConfig | CriticConfig,
    user_api_key: StoredApiKey | None = None,
) -> LLMBackend:
    if user_api_key is not None:
        model = USER_MODEL_MAP.get(user_api_key.provider)
        if model is None:
            raise ValueError(f"Unsupported user provider: {user_api_key.provider}")
        return create_backend_from_model_string(
            f"{user_api_key.provider}/{model}",
            temperature=config.temperature,
            api_key=user_api_key.api_key,
            timeout_seconds=float(getattr(config, "timeout_seconds", 120.0)),
            base_url=str(getattr(config, "base_url", "") or "") or None,
        )
    return create_backend(config)
