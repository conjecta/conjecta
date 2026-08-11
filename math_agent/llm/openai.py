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

log = logging.getLogger("math_agent.llm.openai")


class OpenAICompatibleBackend:
    """OpenAI Chat Completions backend."""

    supports_native_tools = True

    def __init__(
        self,
        model: str,
        default_temperature: float = 0.7,
        base_url: str | None = None,
        api_key_env: str = "OPENAI_API_KEY",
        api_key: str | None = None,
        timeout_seconds: float = 120.0,
        provider_name: str = "openai",
        retry_max_attempts: int = 3,
        retry_base_seconds: float = 5.0,
    ):
        self.model = model
        self.default_temperature = default_temperature
        resolved_key = api_key or os.environ.get(api_key_env)
        if not resolved_key:
            raise ValueError(
                f"OpenAI API key required. Set {api_key_env} or provide api_key."
            )
        self._api_key = resolved_key
        self._base_url = base_url
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self.provider_name = provider_name
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
                base_url=self._base_url,
                timeout=self._timeout_seconds,
            )
        return self._client

    def _build_api_messages(self, messages: list[Message], system: str) -> list[dict]:
        api_messages: list[dict] = []
        if system:
            api_messages.append({"role": "system", "content": system})
        api_messages.extend({"role": m.role, "content": m.content} for m in messages)
        return api_messages

    async def _create_completion(self, kwargs: dict, *, logprobs: bool) -> Any:
        """Call chat.completions.create, falling back once without logprobs
        when the provider rejects them with HTTP 400."""
        try:
            response = await self.client.chat.completions.create(**kwargs)
        except Exception as exc:
            if not logprobs or getattr(exc, "status_code", None) != 400:
                raise
            log.warning(
                "Provider rejected logprobs; retrying without them model=%s",
                self.model,
            )
            kwargs.pop("logprobs", None)
            response = await self.client.chat.completions.create(**kwargs)
        if not hasattr(response, "choices"):
            # Gateways occasionally answer 200 with a plain-text body, which
            # the SDK passes through as a raw str.
            raise MalformedResponseError(
                f"non-ChatCompletion response body: {str(response)[:200]!r}"
            )
        return response

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
        temp = temperature if temperature is not None else self.default_temperature
        api_messages = self._build_api_messages(messages, system)
        log.debug(
            "OpenAI request: model=%s messages=%d temperature=%s",
            self.model,
            len(api_messages),
            temp,
        )
        try:
            kwargs = {
                "model": self.model,
                "messages": api_messages,
                "temperature": temp,
            }
            if self.provider_name == "kimi":
                # K-series coding models reject any temperature but 1;
                # omitting it lets the server apply its own default.
                kwargs.pop("temperature")
            if response_format:
                kwargs["response_format"] = response_format
            if logprobs:
                kwargs["logprobs"] = True
            if tools:
                kwargs["tools"] = tools
            response = await call_with_retries(
                lambda: self._create_completion(kwargs, logprobs=logprobs),
                max_attempts=self._retry_max_attempts,
                base_seconds=self._retry_base_seconds,
                description=f"OpenAI complete model={self.model}",
            )
        except Exception:
            log.exception("OpenAI complete failed model=%s", self.model)
            raise
        out = response.choices[0].message.content or ""
        usage = response.usage
        estimated_prompt_tokens = estimate_prompt_tokens(api_messages, self.model)
        prompt_tokens = getattr(usage, "prompt_tokens", None) or estimated_prompt_tokens
        completion_tokens = getattr(usage, "completion_tokens", None) or max(
            1, len(out) // 4
        )
        total_tokens = getattr(usage, "total_tokens", None) or (
            prompt_tokens + completion_tokens
        )
        log.debug(
            "OpenAI response: model=%s chars=%d prompt_tokens=%d completion_tokens=%d total_tokens=%d",
            self.model,
            len(out),
            prompt_tokens,
            completion_tokens,
            total_tokens,
        )
        await record_llm_usage(
            provider=self.provider_name,
            model=self.model,
            usage=getattr(response, "usage", None),
        )
        return LLMResponse(
            text=out,
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
            description=f"OpenAI stream model={self.model}",
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
        temp = temperature if temperature is not None else self.default_temperature
        api_messages = self._build_api_messages(messages, system)
        estimated_prompt_tokens = estimate_prompt_tokens(api_messages, self.model)
        log.debug(
            "OpenAI stream start: model=%s messages=%d temperature=%s logprobs=%s",
            self.model,
            len(api_messages),
            temp,
            logprobs,
        )
        try:
            kwargs = {
                "model": self.model,
                "messages": api_messages,
                "temperature": temp,
                "stream": True,
                "stream_options": {"include_usage": True},
            }
            if self.provider_name == "kimi":
                # K-series coding models reject any temperature but 1;
                # omitting it lets the server apply its own default.
                kwargs.pop("temperature")
            if response_format:
                kwargs["response_format"] = response_format
            if logprobs:
                kwargs["logprobs"] = True
            if tools:
                kwargs["tools"] = tools
            try:
                stream = await self.client.chat.completions.create(**kwargs)
            except Exception as exc:
                if getattr(exc, "status_code", None) != 400:
                    raise
                # Retry without unsupported options (usage reporting and/or logprobs).
                log.warning(
                    "Provider rejected stream options; retrying with reduced kwargs model=%s",
                    self.model,
                )
                kwargs.pop("stream_options", None)
                if logprobs:
                    kwargs.pop("logprobs", None)
                    logprobs = False
                stream = await self.client.chat.completions.create(**kwargs)
            collected_text = ""
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
                    tool_calls.add_delta(choice.delta)
                    delta = choice.delta.content
                    if delta:
                        collected_text += delta
                        produced_content = True
                        yield LLMResponse(
                            text=delta,
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
                    prompt_tokens = getattr(final_usage, "prompt_tokens", None) or estimated_prompt_tokens
                    completion_tokens = getattr(final_usage, "completion_tokens", None) or max(
                        1, len(collected_text) // 2
                    )
                    total_tokens = getattr(final_usage, "total_tokens", None) or (
                        prompt_tokens + completion_tokens
                    )
                    try:
                        await record_llm_usage(
                            provider=self.provider_name,
                            model=self.model,
                            usage={
                                "prompt_tokens": prompt_tokens,
                                "completion_tokens": completion_tokens,
                                "total_tokens": total_tokens,
                            },
                        )
                    except Exception:
                        log.exception("Failed to record OpenAI usage on stream close")
                    recorded = True
            prompt_tokens = getattr(final_usage, "prompt_tokens", None) or estimated_prompt_tokens
            completion_tokens = getattr(final_usage, "completion_tokens", None) or max(
                1, len(collected_text) // 2
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
        except Exception:
            log.exception("OpenAI stream failed model=%s", self.model)
            raise


# Alias for backward compat
OpenAIBackend = OpenAICompatibleBackend
