"""Cloud-backed knowledge store for facts, intuitions, and tricks."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from math_agent.config import KnowledgeConfig
from math_agent.knowledge.embeddings import (
    EmbeddingProvider,
    SyncEmbeddingProvider,
    create_embedding_provider,
)
from math_agent.knowledge.supabase_client import SupabaseConfig, create_supabase_client
from math_agent.knowledge.trust import KnowledgeTrustPolicy
from math_agent.search.text_retrieval import query_terms, rank_rows

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from postgrest.exceptions import APIError
    from supabase import Client
else:
    Client = Any

try:
    from postgrest.exceptions import APIError
except Exception:  # pragma: no cover - optional dependency
    class APIError(Exception):  # type: ignore[no-redef]
        pass


# Re-export for callers that imported SupabaseConfig from this module.
__all__ = ["KnowledgeStore", "SupabaseConfig"]

_TRUSTED_STATUSES = tuple(sorted(KnowledgeTrustPolicy.SOLVE_RETRIEVAL))


_COMMON_MEMORY_FIELDS = [
    "source",
    "source_type",
    "source_ref",
    "source_title",
    "evidence",
    "confidence",
    "score",
    "status",
    "domain",
    "tags",
    "created_by",
    "review_note",
]
_FACT_FIELDS = [
    "statement", "why", "statement_zh", "why_zh", "formal_status", "lean_name",
    *_COMMON_MEMORY_FIELDS,
]
_INTUITION_FIELDS = ["title", "body", "title_zh", "body_zh", "kind", *_COMMON_MEMORY_FIELDS]
_TRICK_FIELDS = [
    "title",
    "body",
    "title_zh",
    "body_zh",
    "category",
    "applicability",
    "failure_mode",
    *_COMMON_MEMORY_FIELDS,
]


@dataclass(frozen=True)
class _LegacySupabaseConfig:
    """Deprecated alias kept for type checkers; use supabase_client.SupabaseConfig."""

    url: str
    key: str


class KnowledgeStore:
    """Cloud-backed knowledge store for facts, intuitions, and tricks.

    When ``user_id`` is set, every query/insert is tenant-scoped.
    """

    _TABLES = {
        "facts": "facts",
        "intuitions": "intuitions",
        "tricks": "tricks",
    }

    def __init__(
        self,
        config: SupabaseConfig | None = None,
        *,
        user_id: str | None = None,
        client: Any | None = None,
        knowledge_config: KnowledgeConfig | None = None,
    ) -> None:
        self.user_id = (user_id or "").strip() or None
        self.knowledge_config = knowledge_config or KnowledgeConfig()
        self._embedding_provider: EmbeddingProvider | None = None
        self._embedding_failed = False
        if client is not None:
            self.client = client
            return
        if config is not None:
            try:
                from supabase import create_client
            except ImportError as exc:
                raise RuntimeError(
                    "Supabase knowledge store requires the optional 'supabase' package."
                ) from exc
            self.client: Client = create_client(config.url, config.key)
            return
        self.client = create_supabase_client(
            prefer_service_role=True,
            require_service_role=True,
        )

    def _embedding(self) -> EmbeddingProvider | None:
        if self._embedding_provider is not None:
            return self._embedding_provider
        if self._embedding_failed or not self.knowledge_config.embedding_enabled:
            return None
        try:
            self._embedding_provider = create_embedding_provider({
                "enabled": True,
                "provider": self.knowledge_config.embedding_provider,
                "model": self.knowledge_config.embedding_model,
                "api_key": self.knowledge_config.embedding_api_key or None,
            })
        except Exception as exc:
            log.warning("Embedding provider creation failed: %s", exc)
            self._embedding_provider = None
            self._embedding_failed = True
        return self._embedding_provider

    def _require_user_id(self, user_id: str | None = None) -> str | None:
        uid = (user_id or self.user_id or "").strip() or None
        return uid

    # ------------------------------------------------------------------
    # Facts
    # ------------------------------------------------------------------
    def list_facts(
        self, project_id: str, *, limit: int = 200, offset: int = 0, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self._list_table(
            self._TABLES["facts"], project_id, limit=limit, offset=offset, user_id=user_id
        )

    def add_fact(
        self,
        project_id: str,
        statement: str,
        why: str = "",
        source: str = "",
        *,
        status: str = "approved",
        source_type: str = "manual",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "project_id": project_id,
            "statement": statement,
            "why": why,
            "source": source,
            "status": status,
            "source_type": source_type,
        }
        uid = self._require_user_id(user_id)
        if uid:
            row["user_id"] = uid
        return self._insert(self._TABLES["facts"], row)

    # ------------------------------------------------------------------
    # Intuitions
    # ------------------------------------------------------------------
    def list_intuitions(
        self, project_id: str, *, limit: int = 200, offset: int = 0, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self._list_table(
            self._TABLES["intuitions"], project_id, limit=limit, offset=offset, user_id=user_id
        )

    def add_intuition(
        self,
        project_id: str,
        title: str,
        body: str,
        kind: str = "",
        source: str = "",
        *,
        status: str = "approved",
        source_type: str = "manual",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "project_id": project_id,
            "title": title,
            "body": body,
            "kind": kind,
            "source": source,
            "status": status,
            "source_type": source_type,
        }
        uid = self._require_user_id(user_id)
        if uid:
            row["user_id"] = uid
        return self._insert(self._TABLES["intuitions"], row)

    # ------------------------------------------------------------------
    # Tricks
    # ------------------------------------------------------------------
    def list_tricks(
        self, project_id: str, *, limit: int = 200, offset: int = 0, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self._list_table(
            self._TABLES["tricks"], project_id, limit=limit, offset=offset, user_id=user_id
        )

    def _list_table(
        self,
        table: str,
        project_id: str,
        *,
        limit: int = 200,
        offset: int = 0,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        try:
            q = (
                self.client.table(table)
                .select("*")
                .eq("project_id", project_id)
            )
            uid = self._require_user_id(user_id)
            if uid:
                q = q.eq("user_id", uid)
            resp = q.order("created_at", desc=False).limit(limit).offset(offset).execute()
            return resp.data or []
        except APIError as exc:
            if _is_missing_table_error(exc):
                return []
            raise

    def add_trick(
        self,
        project_id: str,
        title: str,
        body: str,
        category: str = "",
        source: str = "",
        *,
        status: str = "approved",
        source_type: str = "manual",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {
            "project_id": project_id,
            "title": title,
            "body": body,
            "category": category,
            "source": source,
            "status": status,
            "source_type": source_type,
        }
        uid = self._require_user_id(user_id)
        if uid:
            row["user_id"] = uid
        return self._insert(self._TABLES["tricks"], row)

    # ------------------------------------------------------------------
    # Mutation helpers — update, delete, score
    # ------------------------------------------------------------------
    def update_item(
        self,
        project_id: str,
        item_id: str,
        kind: str,
        fields: dict[str, Any],
        *,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Patch a single knowledge item. `kind` must be 'fact', 'intuition', or 'trick'."""
        table = self._TABLES.get(f"{kind}s")
        if table is None:
            raise ValueError(f"Unknown knowledge kind: {kind!r}")
        patch = {k: v for k, v in fields.items() if k not in ("id", "project_id", "created_at", "user_id")}
        if not patch:
            return {}
        patch["updated_at"] = datetime.now(timezone.utc).isoformat()
        q = self.client.table(table).update(patch).eq("project_id", project_id).eq("id", item_id)
        uid = self._require_user_id(user_id)
        if uid:
            q = q.eq("user_id", uid)
        resp = q.execute()
        data = resp.data or []
        return data[0] if data else {}

    def delete_item(
        self, project_id: str, item_id: str, kind: str, *, user_id: str | None = None
    ) -> None:
        """Delete a single knowledge item by id."""
        table = self._TABLES.get(f"{kind}s")
        if table is None:
            raise ValueError(f"Unknown knowledge kind: {kind!r}")
        q = self.client.table(table).delete().eq("project_id", project_id).eq("id", item_id)
        uid = self._require_user_id(user_id)
        if uid:
            q = q.eq("user_id", uid)
        q.execute()

    def set_score(
        self,
        project_id: str,
        item_id: str,
        kind: str,
        score: float,
        *,
        user_id: str | None = None,
    ) -> None:
        """Persist a quality score for a knowledge item (stored as 'score' column)."""
        table = self._TABLES.get(f"{kind}s")
        if table is None:
            return
        try:
            q = (
                self.client.table(table)
                .update({"score": float(score)})
                .eq("project_id", project_id)
                .eq("id", item_id)
            )
            uid = self._require_user_id(user_id)
            if uid:
                q = q.eq("user_id", uid)
            q.execute()
        except Exception:
            pass  # score column may not exist in older schema; silently skip

    # ------------------------------------------------------------------
    # Search helpers (full table scan via ILIKE; good for small stores)
    # ------------------------------------------------------------------
    def search_facts(
        self, project_id: str, query: str, *, limit: int = 20, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self._search_table(
            self._TABLES["facts"], project_id, query, ["statement", "why"], limit, user_id=user_id
        )

    def search_intuitions(
        self, project_id: str, query: str, *, limit: int = 20, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self._search_table(
            self._TABLES["intuitions"], project_id, query, ["title", "body"], limit, user_id=user_id
        )

    def search_tricks(
        self, project_id: str, query: str, *, limit: int = 20, user_id: str | None = None
    ) -> list[dict[str, Any]]:
        return self._search_table(
            self._TABLES["tricks"], project_id, query, ["title", "body"], limit, user_id=user_id
        )

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------
    def add_many(
        self,
        project_id: str,
        facts: list[dict[str, str]],
        intuitions: list[dict[str, str]],
        tricks: list[dict[str, str]],
        *,
        user_id: str | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        uid = self._require_user_id(user_id)
        fact_rows = [
            self._row(self._TABLES["facts"], project_id, f, _FACT_FIELDS, user_id=uid)
            for f in facts
        ]
        intuition_rows = [
            self._row(
                self._TABLES["intuitions"],
                project_id,
                i,
                _INTUITION_FIELDS,
                user_id=uid,
            )
            for i in intuitions
        ]
        trick_rows = [
            self._row(
                self._TABLES["tricks"],
                project_id,
                t,
                _TRICK_FIELDS,
                user_id=uid,
            )
            for t in tricks
        ]

        inserted: dict[str, list[dict[str, Any]]] = {"facts": [], "intuitions": [], "tricks": []}
        if fact_rows:
            inserted["facts"] = self._insert_many(self._TABLES["facts"], fact_rows)
        if intuition_rows:
            inserted["intuitions"] = self._insert_many(
                self._TABLES["intuitions"], intuition_rows
            )
        if trick_rows:
            inserted["tricks"] = self._insert_many(self._TABLES["tricks"], trick_rows)
        return inserted

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _insert(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        row = self._with_timestamps(row)
        resp = self.client.table(table).insert(row).execute()
        data = resp.data or []
        return data[0] if data else row

    def _insert_many(
        self, table: str, rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        rows = [self._with_timestamps(r) for r in rows]
        resp = self.client.table(table).insert(rows).execute()
        return resp.data or []

    def _row(
        self,
        table: str,
        project_id: str,
        item: dict[str, str],
        fields: list[str],
        *,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        row: dict[str, Any] = {"project_id": project_id}
        if user_id:
            row["user_id"] = user_id
        for f in fields:
            row[f] = item.get(f, "")
        if "status" in fields and not row.get("status"):
            row["status"] = "candidate"
        return row

    def _with_timestamps(self, row: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).isoformat()
        row.setdefault("created_at", now)
        row.setdefault("updated_at", now)
        return row

    def _hybrid_rank(
        self,
        lexical_items: list[dict[str, Any]],
        vector_items: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        scored: dict[str, tuple[dict[str, Any], float]] = {}
        for rank, item in enumerate(lexical_items):
            key = item.get("id")
            if key:
                scored[key] = (item, 1.0 / (rank + 1))
        for rank, item in enumerate(vector_items):
            key = item.get("id")
            if not key:
                continue
            if key in scored:
                scored[key] = (scored[key][0], scored[key][1] + 1.0 / (rank + 1))
            else:
                scored[key] = (item, 1.0 / (rank + 1))
        sorted_items = sorted(scored.values(), key=lambda x: -x[1])
        return [item for item, _ in sorted_items[:limit]]

    def _vector_search(
        self,
        table: str,
        project_id: str,
        query_embedding: list[float],
        limit: int,
        *,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        uid = self._require_user_id(user_id)
        try:
            resp = self.client.rpc(
                "match_knowledge_embeddings",
                {
                    "p_table": table,
                    "p_query_embedding": query_embedding,
                    "p_project_id": project_id,
                    "p_user_id": uid,
                    "p_statuses": list(_TRUSTED_STATUSES),
                    "p_limit": max(limit * 4, self.knowledge_config.hybrid_search_top_k),
                },
            ).execute()
        except Exception as exc:
            log.debug("Vector search failed for %s: %s", table, exc)
            return []
        data = resp.data
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("data", []) if "data" in data else [data]
        return []

    def _search_table(
        self,
        table: str,
        project_id: str,
        query: str,
        columns: list[str],
        limit: int,
        *,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        terms = query_terms(query, limit=8)
        if not terms:
            return []
        builder = (
            self.client.table(table)
            .select("*")
            .eq("project_id", project_id)
            .in_("status", list(_TRUSTED_STATUSES))
            .limit(max(limit * 20, 100))
        )
        uid = self._require_user_id(user_id)
        if uid:
            builder = builder.eq("user_id", uid)
        or_clauses = ",".join(
            f"{column}.ilike.%{term}%"
            for term in terms
            for column in columns
        )
        resp = builder.or_(or_clauses).execute()
        provider = self._embedding()

        if provider is None:
            lexical_items = rank_rows(resp.data or [], query, columns, limit=limit)
            return lexical_items[:limit]

        lexical_items = rank_rows(
            resp.data or [],
            query,
            columns,
            limit=max(limit, self.knowledge_config.hybrid_search_top_k),
        )

        query_embedding: list[float] = []
        if isinstance(provider, SyncEmbeddingProvider):
            try:
                embeddings = provider.embed_sync([query])
                query_embedding = embeddings[0] if embeddings else []
            except Exception as exc:
                log.warning("Embedding generation failed: %s", exc)
                return lexical_items[:limit]
        else:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                try:
                    embeddings = asyncio.run(provider.embed([query]))
                    query_embedding = embeddings[0] if embeddings else []
                except Exception as exc:
                    log.warning("Embedding generation failed: %s", exc)
                    return lexical_items[:limit]
            else:
                log.warning(
                    "Async embedding provider %r invoked from a running event loop; "
                    "falling back to lexical search.",
                    provider.__class__.__name__,
                )
                return lexical_items[:limit]

        if not query_embedding:
            return lexical_items[:limit]

        vector_items = self._vector_search(
            table, project_id, query_embedding, limit, user_id=user_id
        )
        return self._hybrid_rank(lexical_items, vector_items, limit)


def _is_missing_table_error(exc: APIError) -> bool:
    """Detect PostgREST 'table not found in schema cache' errors."""
    if hasattr(exc, "code") and exc.code == "PGRST205":
        return True
    msg = str(exc)
    return "PGRST205" in msg or "in the schema cache" in msg
