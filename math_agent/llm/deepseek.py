from __future__ import annotations

import logging
import os
from typing import Any, AsyncIterator

from math_agent.billing.models import LLMResponse
from math_agent.llm.base import Message
from math_agent.llm.retry import (
    MalformedResponseError,
    call_with_retries,
    stream_with_retries,
)
from math_agent.llm.utils import (
    ToolCallAccumulator,
    estimate_prompt_tokens,
    mean_logprob,
    token_logprobs_from_choice,
    tool_calls_from_message,
)
from math_agent.web.operations import record_llm_usage

log = logging.getLogger("math_agent.llm.deepseek")

# https://api-docs.deepseek.com/zh-cn/
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"

# Legacy / alias names sent directly to the DeepSeek API.
_LEGACY_MODEL_MAP: dict[str, tuple[str, bool]] = {
    "deepseek-chat": ("deepseek-chat", False),
    "deepseek-reasoner": ("deepseek-reasoner", True),
}

# v4 models that support thinking mode
_THINKING_MODELS = frozenset({"deepseek-v4-pro", "deepseek-v4-flash"})


def _resolve_model(model: str) -> tuple[str, bool]:
    """Return (api_model, thinking_enabled)."""
    if model in _LEGACY_MODEL_MAP:
        return _LEGACY_MODEL_MAP[model]
    thinking = model in _THINKING_MODELS
    return model, thinking


def _extract_content(message: Any) -> str:
    content = getattr(message, "content", None) or ""
    if content:
        return content
    # Fallback if only reasoning chain is present (should not happen on success)
    return getattr(message, "reasoning_content", None) or ""


class DeepSeekBackend:
    """DeepSeek API via the OpenAI Python SDK."""

    supports_native_tools = True

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        default_temperature: float = 0.7,
        api_key: str | None = None,
        reasoning_effort: str = "high",
        timeout_seconds: float = 120.0,
        retry_max_attempts: int = 3,
        retry_base_seconds: float = 5.0,
    ):
        resolved_key = api_key or os.environ.get("DEEPSEEK_API_KEY")
        if not resolved_key:
            raise ValueError(
                "DeepSeek API key required. Set DEEPSEEK_API_KEY or provide api_key."
            )

        self.model, self._thinking_enabled = _resolve_model(model)
        self.default_temperature = default_temperature
        self.reasoning_effort = reasoning_effort
        self._api_key = resolved_key
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        # 0 retries disables retrying and keeps the previous fail-fast behavior.
        self._retry_max_attempts = max(0, int(retry_max_attempts))
        self._retry_base_seconds = max(0.0, float(retry_base_seconds))
        self._client: Any | None = None

    @property
    def client(self) -> Any:
        if self._client is None:
            from openai import AsyncOpenAI

            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=DEEPSEEK_BASE_URL,
                timeout=self._timeout_seconds,
            )
        return self._client

    def _completion_kwargs(
        self,
        api_messages: list[dict],
        temperature: float | None,
        *,
        stream: bool,
        response_format: dict[str, str] | None = None,
        logprobs: bool = False,
        tools: list[dict] | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": api_messages,
            "stream": stream,
            "max_tokens": 8192,
        }
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        if response_format:
            kwargs["response_format"] = response_format
        if logprobs:
            kwargs["logprobs"] = True
        if tools:
            kwargs["tools"] = tools
        if self._thinking_enabled:
            kwargs["extra_body"] = {
                "thinking": {"type": "enabled"},
                "reasoning_effort": self.reasoning_effort,
            }
        else:
            temp = temperature if temperature is not None else self.default_temperature
            kwargs["temperature"] = temp
        return kwargs

    def _build_messages(
        self, messages: list[Message], system: str
    ) -> list[dict[str, Any]]:
        api_messages: list[dict[str, Any]] = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend({"role": m.role, "content": m.content} for m in messages)
        return api_messages

    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        temperature: float | None = None,
        response_format: dict[str, str] | None = None,
        *,
        logprobs: bool = False,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        api_messages = self._build_messages(messages, system)
        estimated_prompt_tokens = estimate_prompt_tokens(api_messages, self.model)
        kwargs = self._completion_kwargs(
            api_messages,
            temperature,
            stream=False,
            response_format=response_format,
            logprobs=logprobs,
            tools=tools,
        )
        log.debug(
            "DeepSeek request: model=%s thinking=%s messages=%d base_url=%s",
            self.model,
            self._thinking_enabled,
            len(api_messages),
            DEEPSEEK_BASE_URL,
        )
        log.debug(
            "DeepSeek kwargs (no messages): %s",
            {k: v for k, v in kwargs.items() if k != "messages"},
        )
        async def _create() -> Any:
            try:
                response = await self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                if not logprobs or getattr(exc, "status_code", None) != 400:
                    raise
                log.warning(
                    "DeepSeek rejected logprobs; retrying without them model=%s",
                    self.model,
                )
                kwargs.pop("logprobs", None)
                response = await self.client.chat.completions.create(**kwargs)
            if not hasattr(response, "choices"):
                # Gateways occasionally answer 200 with a plain-text body,
                # which the SDK passes through as a raw str.
                raise MalformedResponseError(
                    f"non-ChatCompletion response body: {str(response)[:200]!r}"
                )
            return response

        try:
            response = await call_with_retries(
                _create,
                max_attempts=self._retry_max_attempts,
                base_seconds=self._retry_base_seconds,
                description=f"DeepSeek complete model={self.model}",
            )
        except Exception as e:
            log.error(
                "DeepSeek API error: %s model=%s messages=%d",
                e,
                self.model,
                len(api_messages),
            )
            raise
        content = _extract_content(response.choices[0].message)
        usage = response.usage
        prompt_tokens = getattr(usage, "prompt_tokens", None) or estimated_prompt_tokens
        completion_tokens = getattr(usage, "completion_tokens", None) or max(
            1, len(content) // 4
        )
        total_tokens = getattr(usage, "total_tokens", None) or (
            prompt_tokens + completion_tokens
        )
        log.debug(
            "DeepSeek response model=%s chars=%d prompt_tokens=%d completion_tokens=%d total_tokens=%d",
            self.model,
            len(content),
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )
        await record_llm_usage(
            provider="deepseek", model=self.model, usage=getattr(response, "usage", None)
        )
        log.debug("DeepSeek raw output:\n%s", content)
        return LLMResponse(
            text=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            mean_logprob=mean_logprob(token_logprobs_from_choice(response.choices[0])),
            tool_calls=tool_calls_from_message(response.choices[0].message),
        )

    async def stream(
        self,
        messages: list[Message],
        system: str = "",
        temperature: float | None = None,
        response_format: dict[str, str] | None = None,
        *,
        logprobs: bool = False,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMResponse]:
        # Retry only while nothing has been yielded to the caller yet; once
        # content has been emitted, a mid-stream failure must propagate
        # because retrying would duplicate content.
        async for item in stream_with_retries(
            lambda: self._stream_once(
                messages,
                system,
                temperature,
                response_format,
                logprobs=logprobs,
                tools=tools,
            ),
            max_attempts=self._retry_max_attempts,
            base_seconds=self._retry_base_seconds,
            description=f"DeepSeek stream model={self.model}",
        ):
            yield item

    async def _stream_once(
        self,
        messages: list[Message],
        system: str = "",
        temperature: float | None = None,
        response_format: dict[str, str] | None = None,
        *,
        logprobs: bool = False,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMResponse]:
        api_messages = self._build_messages(messages, system)
        estimated_prompt_tokens = estimate_prompt_tokens(api_messages, self.model)
        kwargs = self._completion_kwargs(
            api_messages,
            temperature,
            stream=True,
            response_format=response_format,
            logprobs=logprobs,
            tools=tools,
        )
        try:
            stream = await self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 400:
                raise
            log.warning(
                "DeepSeek rejected stream options; retrying with reduced kwargs model=%s",
                self.model,
            )
            kwargs.pop("stream_options", None)
            kwargs.pop("logprobs", None)
            stream = await self.client.chat.completions.create(**kwargs)
        chunks: list[str] = []
        final_usage = None
        recorded = False
        produced_content = False
        completed = False
        collected_logprobs: list[float] = []
        tool_calls = ToolCallAccumulator()
        try:
            async for chunk in stream:
                if getattr(chunk, "usage", None):
                    final_usage = chunk.usage
                    continue
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                collected_logprobs.extend(token_logprobs_from_choice(choice))
                delta = choice.delta
                tool_calls.add_delta(delta)
                if delta.content:
                    chunks.append(delta.content)
                    produced_content = True
                    yield LLMResponse(
                        text=delta.content,
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                    )
            completed = True
        finally:
            # Skip accounting when the attempt failed before producing any
            # content: the outer retry wrapper may retry it, and only the
            # attempt that actually streamed (or completed) should count.
            if not recorded and (completed or produced_content):
                collected = "".join(chunks)
                prompt_tokens = getattr(final_usage, "prompt_tokens", None) or estimated_prompt_tokens
                completion_tokens = getattr(final_usage, "completion_tokens", None) or max(
                    1, len(collected) // 2
                )
                total_tokens = getattr(final_usage, "total_tokens", None) or (
                    prompt_tokens + completion_tokens
                )
                try:
                    await record_llm_usage(
                        provider="deepseek",
                        model=self.model,
                        usage={
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": completion_tokens,
                            "total_tokens": total_tokens,
                        },
                    )
                except Exception:
                    log.exception("Failed to record DeepSeek usage on stream close")
                recorded = True
        collected = "".join(chunks)
        log.debug("DeepSeek stream raw output:\n%s", collected)
        prompt_tokens = getattr(final_usage, "prompt_tokens", None) or estimated_prompt_tokens
        completion_tokens = getattr(final_usage, "completion_tokens", None) or max(
            1, len(collected) // 2
        )
        total_tokens = getattr(final_usage, "total_tokens", None) or (
            prompt_tokens + completion_tokens
        )
        yield LLMResponse(
            text="",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            mean_logprob=mean_logprob(collected_logprobs),
            tool_calls=tool_calls.tool_calls(),
        )
