from __future__ import annotations

from typing import AsyncIterator, Protocol

from math_agent.billing.models import LLMResponse


class Message:
    role: str
    content: str | list[dict]

    def __init__(self, role: str, content: str | list[dict]) -> None:
        self.role = role
        self.content = content


class LLMBackend(Protocol):
    async def complete(
        self,
        messages: list[Message],
        system: str = "",
        temperature: float | None = None,
        response_format: dict[str, str] | None = None,
        *,
        logprobs: bool = False,
        tools: list[dict] | None = None,
    ) -> LLMResponse: ...

    def stream(
        self,
        messages: list[Message],
        system: str = "",
        temperature: float | None = None,
        response_format: dict[str, str] | None = None,
        *,
        logprobs: bool = False,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMResponse]: ...
