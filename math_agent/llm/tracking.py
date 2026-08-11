"""Per-solve LLM usage tracking shared by the web app and the eval runner."""

from __future__ import annotations

from typing import Any, AsyncIterator

from math_agent.billing.models import LLMResponse


class UsageAccumulator:
    def __init__(self) -> None:
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.calls = 0
        self.provider = ""
        self.model = ""

    def add(self, response: LLMResponse) -> None:
        self.prompt_tokens += response.prompt_tokens
        self.completion_tokens += response.completion_tokens
        self.total_tokens += response.total_tokens


class LLMCallCounter:
    """Shared per-solve counter for every LLM call (actor, critic, codegen,
    tactic generation). Reset by the agent at the start of each solve."""

    def __init__(self) -> None:
        self.calls = 0

    def add(self, n: int = 1) -> None:
        self.calls += n

    def reset(self) -> None:
        self.calls = 0


class CallCountingBackend:
    """Wrap an LLMBackend so every complete()/stream() call increments the
    shared counter. Calls and attribute access pass through verbatim so
    lightweight fake backends with narrower signatures keep working."""

    def __init__(self, backend: Any, counter: LLMCallCounter) -> None:
        self._backend = backend
        self._counter = counter

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

    async def complete(self, messages: list[Any], **kwargs: Any) -> LLMResponse:
        self._counter.add()
        return await self._backend.complete(messages, **kwargs)

    async def stream(
        self, messages: list[Any], **kwargs: Any
    ) -> AsyncIterator[LLMResponse]:
        self._counter.add()
        async for response in self._backend.stream(messages, **kwargs):
            yield response


class UsageTrackingBackend:
    """Wrap an LLMBackend so every complete()/stream() response feeds an accumulator."""

    def __init__(self, backend: Any, accumulator: UsageAccumulator) -> None:
        self._backend = backend
        self._accumulator = accumulator

    def __getattr__(self, name: str) -> Any:
        return getattr(self._backend, name)

    async def complete(
        self,
        messages: list[Any],
        system: str = "",
        temperature: float | None = None,
        response_format: dict[str, str] | None = None,
        *,
        logprobs: bool = False,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        self._accumulator.calls += 1
        kwargs: dict[str, Any] = {
            "system": system,
            "temperature": temperature,
            "response_format": response_format,
            "logprobs": logprobs,
        }
        # Only forward tools to backends that accept the parameter.
        if tools is not None:
            kwargs["tools"] = tools
        response = await self._backend.complete(messages, **kwargs)
        self._accumulator.add(response)
        return response

    async def stream(
        self,
        messages: list[Any],
        system: str = "",
        temperature: float | None = None,
        response_format: dict[str, str] | None = None,
        *,
        logprobs: bool = False,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMResponse]:
        self._accumulator.calls += 1
        kwargs: dict[str, Any] = {
            "system": system,
            "temperature": temperature,
            "response_format": response_format,
            "logprobs": logprobs,
        }
        if tools is not None:
            kwargs["tools"] = tools
        async for response in self._backend.stream(messages, **kwargs):
            self._accumulator.add(response)
            yield response
