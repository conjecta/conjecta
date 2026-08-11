from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import openai
import pytest

from math_agent.config import KnowledgeConfig, load_config
from math_agent.knowledge.embeddings import (
    NullEmbeddingProvider,
    OpenAIEmbeddingProvider,
    create_embedding_provider,
)
from math_agent.knowledge.supabase import KnowledgeStore


def test_null_provider_returns_empty_embeddings():
    provider = NullEmbeddingProvider()
    assert asyncio.run(provider.embed(["a", "b"])) == [[], []]


def test_create_provider_returns_null_when_disabled():
    provider = create_embedding_provider({"enabled": False})
    assert isinstance(provider, NullEmbeddingProvider)


def test_create_provider_returns_null_when_config_is_none():
    provider = create_embedding_provider(None)
    assert isinstance(provider, NullEmbeddingProvider)


def test_create_provider_rejects_unknown_provider():
    with pytest.raises(ValueError, match="Unknown embedding provider"):
        create_embedding_provider({"enabled": True, "provider": "unknown"})


def test_openai_provider_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIEmbeddingProvider()


def test_openai_provider_uses_configured_api_key():
    provider = OpenAIEmbeddingProvider(api_key="sk-test")
    assert provider.api_key == "sk-test"
    assert provider.model == "text-embedding-3-small"


@pytest.mark.asyncio
async def test_openai_provider_embed_is_async(monkeypatch):
    async def fake_create(*, input, model):
        class FakeResponse:
            data = [type("Item", (), {"embedding": [0.1, 0.2]})()]
        return FakeResponse()

    fake_client = MagicMock()
    fake_client.embeddings.create = fake_create
    monkeypatch.setattr(openai, "AsyncOpenAI", lambda **kwargs: fake_client)

    provider = OpenAIEmbeddingProvider(api_key="sk-test")
    result = await provider.embed(["hello"])
    assert result == [[0.1, 0.2]]


def test_openai_provider_embed_sync_returns_embeddings(monkeypatch):
    def fake_create(*, input, model):
        class FakeResponse:
            data = [type("Item", (), {"embedding": [0.3, 0.4]})()]
        return FakeResponse()

    fake_client = MagicMock()
    fake_client.embeddings.create = fake_create
    monkeypatch.setattr(openai, "OpenAI", lambda **kwargs: fake_client)

    provider = OpenAIEmbeddingProvider(api_key="sk-test")
    result = provider.embed_sync(["hello"])
    assert result == [[0.3, 0.4]]


def test_knowledge_store_uses_embedding_provider_when_configured():
    fake_client = MagicMock()
    fake_client.table.return_value.select.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = []
    store = KnowledgeStore(
        client=fake_client,
        knowledge_config=KnowledgeConfig(embedding_enabled=False),
    )
    assert store._embedding() is None


def test_knowledge_store_embedding_disabled_by_default():
    fake_client = MagicMock()
    fake_client.table.return_value.select.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = []
    store = KnowledgeStore(client=fake_client)
    assert store._embedding() is None


def test_knowledge_store_creates_openai_provider_when_enabled(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    fake_client = MagicMock()
    fake_client.table.return_value.select.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = []
    store = KnowledgeStore(
        client=fake_client,
        knowledge_config=KnowledgeConfig(embedding_enabled=True),
    )
    provider = store._embedding()
    assert isinstance(provider, OpenAIEmbeddingProvider)


def test_knowledge_store_falls_back_when_provider_creation_fails():
    fake_client = MagicMock()
    fake_client.table.return_value.select.return_value.eq.return_value.in_.return_value.limit.return_value.execute.return_value.data = []
    store = KnowledgeStore(
        client=fake_client,
        knowledge_config=KnowledgeConfig(
            embedding_enabled=True,
            embedding_provider="openai",
            embedding_api_key="",
        ),
    )
    assert store._embedding() is None


def test_hybrid_rank_combines_lexical_and_vector_results():
    store = KnowledgeStore(client=MagicMock())
    lexical = [{"id": "a", "text": "first"}, {"id": "b", "text": "second"}]
    vector = [{"id": "b", "text": "second"}, {"id": "c", "text": "third"}]
    result = store._hybrid_rank(lexical, vector, limit=2)
    assert [r["id"] for r in result] == ["b", "a"]


def test_hybrid_rank_limits_results():
    store = KnowledgeStore(client=MagicMock())
    lexical = [{"id": str(i)} for i in range(10)]
    vector = [{"id": str(i + 10)} for i in range(10)]
    result = store._hybrid_rank(lexical, vector, limit=5)
    assert len(result) == 5


class _FakeSearchClient:
    """Minimal fake supabase client for search tests."""

    def __init__(self, lexical_data, vector_data):
        self._lexical_data = lexical_data
        self._vector_data = vector_data

    def table(self, name: str):
        return _FakeTableQuery(self._lexical_data)

    def rpc(self, name: str, params: dict):
        return _FakeRpcQuery(self._vector_data)


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeTableQuery:
    def __init__(self, data):
        self._data = data

    def select(self, *_a, **_k):
        return self

    def eq(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def or_(self, *_a, **_k):
        return self

    def execute(self):
        return _FakeExecute(self._data)


class _FakeRpcQuery:
    def __init__(self, data):
        self._data = data

    def execute(self):
        return _FakeExecute(self._data)


def test_search_table_returns_lexical_results_when_embedding_disabled():
    lexical = [{"id": "a", "statement": "prime numbers", "status": "approved"}]
    client = _FakeSearchClient(lexical_data=lexical, vector_data=[])
    store = KnowledgeStore(
        client=client,
        knowledge_config=KnowledgeConfig(embedding_enabled=False),
    )
    results = store.search_facts("default", "prime")
    assert len(results) == 1
    assert results[0]["id"] == "a"


def test_search_table_disabled_embedding_honors_limit():
    """Lexical ranking must use the caller's limit when embeddings are disabled."""
    lexical = [
        {"id": str(i), "statement": f"number {i}", "status": "approved"}
        for i in range(30)
    ]
    client = _FakeSearchClient(lexical_data=lexical, vector_data=[])
    store = KnowledgeStore(
        client=client,
        knowledge_config=KnowledgeConfig(embedding_enabled=False, hybrid_search_top_k=5),
    )
    results = store.search_facts("default", "number", limit=10)
    assert len(results) == 10


def test_search_table_hybrid_satisfies_limit_with_empty_vector_results():
    """Hybrid ranking must still return up to ``limit`` lexical results when vector search is empty."""
    lexical = [
        {"id": str(i), "statement": f"number {i}", "status": "approved"}
        for i in range(30)
    ]
    client = _FakeSearchClient(lexical_data=lexical, vector_data=[])

    class _FakeProvider:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1] * 1536]

    store = KnowledgeStore(
        client=client,
        knowledge_config=KnowledgeConfig(embedding_enabled=True, hybrid_search_top_k=5),
    )
    store._embedding_provider = _FakeProvider()
    results = store.search_facts("default", "number", limit=10)
    assert len(results) == 10


def test_search_table_hybrid_ranking_when_embedding_enabled():
    lexical = [
        {"id": "a", "statement": "prime numbers", "status": "approved"},
        {"id": "b", "statement": "even numbers", "status": "approved"},
    ]
    vector = [
        {"id": "b", "statement": "even numbers", "status": "approved"},
    ]
    client = _FakeSearchClient(lexical_data=lexical, vector_data=vector)

    class _FakeProvider:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1] * 1536]

    store = KnowledgeStore(
        client=client,
        knowledge_config=KnowledgeConfig(embedding_enabled=True),
    )
    store._embedding_provider = _FakeProvider()
    results = store.search_facts("default", "even prime", limit=2)
    assert [r["id"] for r in results] == ["b", "a"]


class _CapturingRpcClient:
    """Fake client that records the parameters passed to ``match_knowledge_embeddings``."""

    def __init__(self, vector_data):
        self._vector_data = vector_data
        self.last_rpc_params = None

    def table(self, name: str):
        return _FakeTableQuery([])

    def rpc(self, name: str, params: dict):
        self.last_rpc_params = params
        return _FakeRpcQuery(self._vector_data)


def test_vector_search_respects_hybrid_search_top_k():
    """The vector candidate fetch limit should scale with the configured top-k, not hard-fetch 100."""
    client = _CapturingRpcClient(vector_data=[])

    class _FakeProvider:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1] * 1536]

    store = KnowledgeStore(
        client=client,
        knowledge_config=KnowledgeConfig(
            embedding_enabled=True, hybrid_search_top_k=12
        ),
    )
    store._embedding_provider = _FakeProvider()
    store.search_facts("default", "prime", limit=5)
    assert client.last_rpc_params is not None
    assert client.last_rpc_params["p_limit"] == max(5 * 4, 12)
    assert client.last_rpc_params["p_limit"] < 100


def test_search_table_falls_back_to_lexical_on_vector_failure():
    lexical = [{"id": "a", "statement": "prime numbers", "status": "approved"}]
    client = _FakeSearchClient(lexical_data=lexical, vector_data=[])
    client.rpc = lambda name, params: _FakeRpcQuery(_RaiseOnExecute())

    class _FakeProvider:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[0.1] * 1536]

    store = KnowledgeStore(
        client=client,
        knowledge_config=KnowledgeConfig(embedding_enabled=True),
    )
    store._embedding_provider = _FakeProvider()
    results = store.search_facts("default", "prime")
    assert len(results) == 1
    assert results[0]["id"] == "a"


def test_search_table_falls_back_to_lexical_limit_on_embedding_failure():
    """Embedding generation failures must still return up to ``limit`` lexical results."""
    lexical = [
        {"id": str(i), "statement": f"number {i}", "status": "approved"}
        for i in range(30)
    ]
    client = _FakeSearchClient(lexical_data=lexical, vector_data=[])

    class _FailingProvider:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("embedding service unavailable")

    store = KnowledgeStore(
        client=client,
        knowledge_config=KnowledgeConfig(embedding_enabled=True, hybrid_search_top_k=5),
    )
    store._embedding_provider = _FailingProvider()
    results = store.search_facts("default", "number", limit=10)
    assert len(results) == 10


def test_search_table_falls_back_to_lexical_limit_on_empty_embedding():
    """Empty query embeddings must still return up to ``limit`` lexical results."""
    lexical = [
        {"id": str(i), "statement": f"number {i}", "status": "approved"}
        for i in range(30)
    ]
    client = _FakeSearchClient(lexical_data=lexical, vector_data=[])

    class _EmptyProvider:
        async def embed(self, texts: list[str]) -> list[list[float]]:
            return [[]]

    store = KnowledgeStore(
        client=client,
        knowledge_config=KnowledgeConfig(embedding_enabled=True, hybrid_search_top_k=5),
    )
    store._embedding_provider = _EmptyProvider()
    results = store.search_facts("default", "number", limit=10)
    assert len(results) == 10


class _RaiseOnExecute:
    def execute(self):
        raise RuntimeError("vector search unavailable")


def test_knowledge_config_defaults():
    cfg = KnowledgeConfig()
    assert cfg.embedding_enabled is False
    assert cfg.embedding_provider == "openai"
    assert cfg.embedding_model == "text-embedding-3-small"
    assert cfg.hybrid_search_top_k == 20


def test_config_loads_knowledge_section(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[knowledge.embedding]
enabled = true
provider = "openai"
model = "text-embedding-3-small"
hybrid_search_top_k = 15
""".strip(),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.knowledge.embedding_enabled is True
    assert cfg.knowledge.embedding_provider == "openai"
    assert cfg.knowledge.hybrid_search_top_k == 15


def test_config_knowledge_defaults_when_missing():
    cfg = load_config()
    assert cfg.knowledge.embedding_enabled is False


def test_config_knowledge_defaults_when_toml_omits_knowledge(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[llm]
provider = "openai"
""".strip(),
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.knowledge.embedding_enabled is False
    assert cfg.knowledge.embedding_provider == "openai"
    assert cfg.knowledge.embedding_model == "text-embedding-3-small"
    assert cfg.knowledge.hybrid_search_top_k == 20
