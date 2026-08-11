from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any


class EmbeddingProvider(ABC):
    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text."""


class SyncEmbeddingProvider(EmbeddingProvider, ABC):
    """Mixin for providers that also expose a synchronous embed path."""

    @abstractmethod
    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        """Synchronous variant of :meth:`embed`."""


class NullEmbeddingProvider(EmbeddingProvider):
    """Disables semantic search."""

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [[] for _ in texts]


class OpenAIEmbeddingProvider(SyncEmbeddingProvider):
    def __init__(self, model: str = "text-embedding-3-small", api_key: str | None = None):
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "")
        if not self.api_key:
            raise RuntimeError("OpenAIEmbeddingProvider requires OPENAI_API_KEY")
        try:
            import openai
        except ImportError as exc:
            raise RuntimeError(
                "OpenAIEmbeddingProvider requires the 'openai' package"
            ) from exc
        self._async_client = openai.AsyncOpenAI(api_key=self.api_key)
        self._sync_client = openai.OpenAI(api_key=self.api_key)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self._async_client.embeddings.create(input=texts, model=self.model)
        return [item.embedding for item in response.data]

    def embed_sync(self, texts: list[str]) -> list[list[float]]:
        response = self._sync_client.embeddings.create(input=texts, model=self.model)
        return [item.embedding for item in response.data]


def create_embedding_provider(config: dict[str, Any] | None) -> EmbeddingProvider:
    if not config or not config.get("enabled"):
        return NullEmbeddingProvider()
    provider = config.get("provider", "openai")
    if provider == "openai":
        return OpenAIEmbeddingProvider(
            model=config.get("model", "text-embedding-3-small"),
            api_key=config.get("api_key"),
        )
    raise ValueError(f"Unknown embedding provider: {provider}")
