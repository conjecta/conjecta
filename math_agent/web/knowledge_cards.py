from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from math_agent.agent.formal_evidence import formal_evidence_id
from math_agent.config import load_config
from math_agent.knowledge.supabase_client import (
    create_supabase_client,
    is_transient_supabase_error,
    run_supabase,
)
from math_agent.lean.runner import LeanRunner
from math_agent.web.friends import are_friends
from math_agent.web.project_store import ProjectStore, project_store_for_user
from math_agent.web.user_store import UserStore

_ALLOWED_VISIBILITY = frozenset({"private", "friends", "public", "team"})


class KnowledgeCardService:
    """Publish, discover, and import verified project knowledge as cards.

    Uses Supabase-backed cloud tables when configured, otherwise falls back to
    the per-user ProjectStore append-only JSONL event log.
    """

    def __init__(
        self,
        user_id: str,
        project_store: ProjectStore | None = None,
        client: Any | None = None,
    ) -> None:
        if not user_id or not str(user_id).strip():
            raise ValueError("user_id is required")
        self.user_id = str(user_id)
        self.project_store = project_store or project_store_for_user(self.user_id)
        self._client_override = client
        self._client_disabled = False
        if client is None:
            try:
                # Probe configuration once; subsequent access uses the property
                # so run_supabase retries can refresh a dead httpx session.
                create_supabase_client(
                    prefer_service_role=True, require_service_role=False
                )
            except Exception:
                self._client_disabled = True

    @property
    def client(self) -> Any | None:
        if self._client_override is not None:
            return self._client_override
        if self._client_disabled:
            return None
        try:
            return create_supabase_client(
                prefer_service_role=True, require_service_role=False
            )
        except Exception:
            self._client_disabled = True
            return None

    @client.setter
    def client(self, value: Any | None) -> None:
        self._client_override = value
        if value is not None:
            self._client_disabled = False

    def publish_from_project_item(
        self,
        project_id: str,
        item_id: str,
        kind: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        item = self.project_store.get_knowledge_item(project_id, item_id, kind)
        if item is None:
            raise ValueError("Source knowledge item not found")
        if self.client is not None:
            return self._publish_cloud(project_id, item_id, kind, item, payload)
        return self._publish_local(project_id, item_id, kind, item, payload)

    def publish_from_turn(
        self,
        project_id: str,
        turn_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        turn = next(
            (
                t
                for t in self.project_store.list_turns(project_id)
                if isinstance(t, dict) and str(t.get("id") or "") == str(turn_id)
            ),
            None,
        )
        if turn is None:
            raise ValueError("Source turn not found")
        item = {
            "title": str(payload.get("title") or (turn.get("problem") or "")[:80]),
            "statement": str(payload.get("statement") or turn.get("answer") or ""),
            "body": str(payload.get("body") or ""),
            "formal_status": str(turn.get("verification_status") or ""),
            "lean_code": "\n\n".join(
                str(proof) for proof in (turn.get("lean_proofs") or [])
            ),
        }
        if self.client is not None:
            return self._publish_cloud(project_id, turn_id, "turn", item, payload)
        return self._publish_local(project_id, turn_id, "turn", item, payload)

    def _publish_cloud(
        self,
        project_id: str,
        item_id: str,
        kind: str,
        item: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        card_id = uuid.uuid4()
        revision_id = uuid.uuid4()
        revision_number = 1
        title = str(
            payload.get("title")
            or item.get("title")
            or item.get("statement")
            or "Untitled"
        )
        statement = str(payload.get("statement") or item.get("statement") or "")
        body = str(payload.get("body") or item.get("why") or item.get("body") or "")
        visibility = _normalize_visibility(payload.get("visibility"))
        status = "published" if visibility != "private" else "draft"
        now = _now()
        card_row = {
            "id": str(card_id),
            "owner_user_id": self.user_id,
            "project_id": project_id,
            "source_item_id": item_id,
            "source_item_kind": kind,
            "latest_revision_id": str(revision_id),
            "visibility": visibility,
            "status": status,
            "citation_count": 0,
            "star_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        revision_row = {
            "id": str(revision_id),
            "card_id": str(card_id),
            "revision_number": revision_number,
            "title": title,
            "statement": statement,
            "body": body,
            "formal_status": str(item.get("formal_status") or ""),
            "lean_name": str(item.get("lean_name") or ""),
            "lean_code": str(item.get("lean_code") or ""),
            "evidence_id": str(item.get("evidence") or ""),
            "source_run_session_id": str(payload.get("source_run_session_id") or ""),
            "source_run_share_token": str(payload.get("source_run_share_token") or ""),
            "tags": payload.get("tags") or [],
            "domain": str(payload.get("domain") or item.get("domain") or ""),
            "metadata": {"source_item_id": item_id, "source_item_kind": kind},
        }
        card_resp = self.client.table("knowledge_cards").insert(card_row).execute()
        revision_resp = (
            self.client.table("card_revisions").insert(revision_row).execute()
        )
        card = (card_resp.data or [card_row])[0]
        revision = (revision_resp.data or [revision_row])[0]
        return {"card": card, "revision": revision}

    def _publish_local(
        self,
        project_id: str,
        item_id: str,
        kind: str,
        item: dict[str, Any],
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        card_id = f"kc-{uuid.uuid4().hex}"
        revision_id = f"cr-{uuid.uuid4().hex}"
        revision_number = 1
        title = str(
            payload.get("title")
            or item.get("title")
            or item.get("statement")
            or "Untitled"
        )
        statement = str(payload.get("statement") or item.get("statement") or "")
        body = str(payload.get("body") or item.get("why") or item.get("body") or "")
        revision = {
            "id": revision_id,
            "card_id": card_id,
            "revision_number": revision_number,
            "title": title,
            "statement": statement,
            "body": body,
            "formal_status": str(item.get("formal_status") or ""),
            "lean_name": str(item.get("lean_name") or ""),
            "lean_code": str(item.get("lean_code") or ""),
            "evidence_id": str(item.get("evidence") or ""),
            "source_run_session_id": str(payload.get("source_run_session_id") or ""),
            "source_run_share_token": str(payload.get("source_run_share_token") or ""),
            "tags": payload.get("tags") or [],
            "domain": str(payload.get("domain") or item.get("domain") or ""),
            "metadata": {"source_item_id": item_id, "source_item_kind": kind},
        }
        visibility = _normalize_visibility(payload.get("visibility"))
        card = {
            "id": card_id,
            "owner_user_id": self.user_id,
            "project_id": project_id,
            "source_item_id": item_id,
            "source_item_kind": kind,
            "latest_revision_id": revision_id,
            "visibility": visibility,
            "status": "published" if visibility != "private" else "draft",
            "citation_count": 0,
            "star_count": 0,
            "created_at": _now(),
            "updated_at": _now(),
        }
        self.project_store._append_event({
            "type": "knowledge_card_created",
            "project_id": project_id,
            "card": card,
            "revision": revision,
        })
        return {"card": card, "revision": revision}

    def get_card(self, card_id: str) -> dict[str, Any] | None:
        """Return a card with its latest revision, or None if not found/inaccessible."""
        if self.client is not None:
            return self._get_card_cloud(card_id)
        return self._get_card_local(card_id)

    def export_card(self, card_id: str, format: str) -> str:
        detail = self.get_card(card_id)
        if detail is None:
            raise ValueError("Card not found")
        revision = detail["revision"]
        title = revision["title"]
        statement = revision["statement"]
        body = revision["body"]
        card_url = f"/share/knowledge/{card_id}"
        if format == "markdown":
            return f"# {title}\n\n**Statement.** {statement}\n\n{body}\n\n*Source: {card_url}*\n"
        if format == "latex":
            return f"\\section*{{{title}}}\n\\textbf{{Statement.}} {statement}\n\n{body}\n\n% Source: {card_url}\n"
        if format == "bibtex":
            key = f"conjecta{card_id[:8]}"
            return f"@misc{{{key},\n  title = {{{title}}},\n  howpublished = {{\\url{{{card_url}}}}},\n  year = {{{datetime.now(timezone.utc).year}}}\n}}\n"
        if format == "lean":
            return revision.get("lean_code") or "-- no Lean code available\n"
        raise ValueError(f"Unsupported export format: {format}")

    def _get_card_cloud(self, card_id: str) -> dict[str, Any] | None:
        resp = (
            self.client.table("knowledge_cards")
            .select("*")
            .eq("id", card_id)
            .limit(1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            return None
        card = dict(rows[0])
        if not self._can_access_card(card):
            return None
        rev_resp = (
            self.client.table("card_revisions")
            .select("*")
            .eq("id", card.get("latest_revision_id"))
            .limit(1)
            .execute()
        )
        rev_rows = rev_resp.data or []
        revision = dict(rev_rows[0]) if rev_rows else None
        return {"card": card, "revision": revision}

    def _build_local_card_index(
        self,
    ) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any] | None], dict[str, int], list[str]]:
        """Replay the local event log to compute the latest state of every card."""
        cards: dict[str, dict[str, Any]] = {}
        revisions: dict[str, dict[str, Any] | None] = {}
        citations: dict[str, int] = {}
        created_order: list[str] = []
        for event in self.project_store._iter_events():
            etype = event.get("type")
            if etype == "knowledge_card_created":
                card = event.get("card") or {}
                cid = str(card.get("id"))
                if cid not in cards:
                    created_order.append(cid)
                cards[cid] = dict(card)
                rev = event.get("revision")
                revisions[cid] = dict(rev) if rev else revisions.get(cid)
            elif etype == "knowledge_card_revision_created":
                cid = str(event.get("card_id"))
                card = event.get("card") or {}
                if str(card.get("id")) == cid and cid in cards:
                    cards[cid] = dict(card)
                    rev = event.get("revision")
                    if rev:
                        revisions[cid] = dict(rev)
            elif etype == "knowledge_card_published":
                cid = str(event.get("card_id"))
                card = event.get("card") or {}
                if str(card.get("id")) == cid and cid in cards:
                    cards[cid] = dict(card)
            elif etype == "knowledge_card_citation_incremented":
                cid = str(event.get("card_id"))
                citations[cid] = citations.get(cid, 0) + 1
        return cards, revisions, citations, created_order

    def _get_card_local(self, card_id: str) -> dict[str, Any] | None:
        cards, revisions, citations, _ = self._build_local_card_index()
        cid = str(card_id)
        card = cards.get(cid)
        if card is None:
            return None
        if not self._can_access_card(card):
            return None
        if citations.get(cid):
            card = dict(card)
            card["citation_count"] = int(card.get("citation_count") or 0) + citations[cid]
        return {"card": card, "revision": revisions.get(cid)}

    def list_my_cards(
        self, limit: int = 200, offset: int = 0
    ) -> list[dict[str, Any]]:
        """Return cards owned by the service user."""
        if self.client is not None:
            return self._list_my_cards_cloud(limit, offset)
        return self._list_my_cards_local(limit, offset)

    def _list_my_cards_cloud(self, limit: int, offset: int) -> list[dict[str, Any]]:
        resp = (
            self.client.table("knowledge_cards")
            .select("*")
            .eq("owner_user_id", self.user_id)
            .order("created_at", desc=True)
            .range(offset, offset + limit - 1)
            .execute()
        )
        return [self._enrich_card_cloud(row) for row in resp.data or []]

    def _list_my_cards_local(self, limit: int, offset: int) -> list[dict[str, Any]]:
        cards, revisions, citations, created_order = self._build_local_card_index()
        result: list[dict[str, Any]] = []
        for cid in reversed(created_order):
            card = cards[cid]
            if str(card.get("owner_user_id")) != self.user_id:
                continue
            if citations.get(cid):
                card = dict(card)
                card["citation_count"] = int(card.get("citation_count") or 0) + citations[cid]
            result.append({"card": card, "revision": revisions.get(cid)})
        return result[offset : offset + limit]

    def list_public_cards(
        self,
        query: str = "",
        tags: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return publicly visible cards with optional text/tag filtering."""
        tags = tags or []
        if self.client is not None:
            return self._list_public_cards_cloud(query, tags, limit, offset)
        return self._list_public_cards_local(query, tags, limit, offset)

    def _list_public_cards_cloud(
        self, query: str, tags: list[str], limit: int, offset: int
    ) -> list[dict[str, Any]]:
        resp = (
            self.client.table("knowledge_cards")
            .select("*")
            .eq("visibility", "public")
            .eq("status", "published")
            .order("created_at", desc=True)
            .execute()
        )
        cards: list[dict[str, Any]] = []
        for row in resp.data or []:
            card = dict(row)
            rev_resp = (
                self.client.table("card_revisions")
                .select("*")
                .eq("id", card.get("latest_revision_id"))
                .limit(1)
                .execute()
            )
            revision = (rev_resp.data or [None])[0]
            if not self._matches_public_query(card, revision, query, tags):
                continue
            cards.append({"card": card, "revision": revision})
        return cards[offset : offset + limit]

    def _list_public_cards_local(
        self, query: str, tags: list[str], limit: int, offset: int
    ) -> list[dict[str, Any]]:
        cards, revisions, citations, created_order = self._build_local_card_index()
        result: list[dict[str, Any]] = []
        for cid in reversed(created_order):
            card = cards[cid]
            if str(card.get("visibility")) != "public":
                continue
            if str(card.get("status")) != "published":
                continue
            revision = revisions.get(cid) or {}
            if not self._matches_public_query(card, revision, query, tags):
                continue
            if citations.get(cid):
                card = dict(card)
                card["citation_count"] = int(card.get("citation_count") or 0) + citations[cid]
            result.append({"card": card, "revision": revision})
        return result[offset : offset + limit]

    def _enrich_card_cloud(self, card: dict[str, Any]) -> dict[str, Any]:
        rev_resp = (
            self.client.table("card_revisions")
            .select("*")
            .eq("id", card.get("latest_revision_id"))
            .limit(1)
            .execute()
        )
        revision = (rev_resp.data or [None])[0]
        return {"card": card, "revision": revision}

    def _can_access_card(self, card: dict[str, Any]) -> bool:
        visibility = str(card.get("visibility") or "private")
        owner = str(card.get("owner_user_id") or "")
        if self.user_id == "anonymous":
            return visibility == "public"
        if visibility == "public" or owner == self.user_id:
            return True
        if not owner:
            # Owner-less rows are a data anomaly; only trust them in local
            # single-user mode (no cloud client). In cloud mode, deny.
            return self.client is None
        if visibility == "friends" and self.client is not None:
            return are_friends(self.client, self.user_id, owner)
        return False

    def list_friend_cards(
        self,
        *,
        query: str = "",
        tags: list[str] | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Browse friends-visibility cards published by accepted friends."""
        tags = tags or []
        if self.user_id == "anonymous":
            return []
        if self.client is not None:
            return self._list_friend_cards_cloud(query, tags, limit, offset)
        return self._list_friend_cards_local(query, tags, limit, offset)

    def _friend_ids(self) -> list[str]:
        if self.client is None:
            return []
        from math_agent.web.friends import FriendsService

        try:
            return FriendsService(user_id=self.user_id, client=self.client).friend_ids()
        except Exception:
            return []

    def _list_friend_cards_cloud(
        self, query: str, tags: list[str], limit: int, offset: int
    ) -> list[dict[str, Any]]:
        friend_ids = set(self._friend_ids())
        if not friend_ids:
            return []
        try:
            resp = run_supabase(
                lambda: (
                    self.client.table("knowledge_cards")
                    .select("*")
                    .eq("visibility", "friends")
                    .eq("status", "published")
                    .order("created_at", desc=True)
                    .execute()
                )
            )
        except Exception as exc:
            if is_transient_supabase_error(exc):
                raise HTTPException(
                    status_code=503,
                    detail="服务暂时遇到问题，请稍后重试。",
                ) from exc
            raise
        cards: list[dict[str, Any]] = []
        for row in resp.data or []:
            card = dict(row)
            if str(card.get("owner_user_id") or "") not in friend_ids:
                continue
            enriched = self._enrich_card_cloud(card)
            if not self._matches_public_query(
                enriched["card"], enriched.get("revision"), query, tags
            ):
                continue
            cards.append(enriched)
        return cards[offset : offset + limit]

    def _list_friend_cards_local(
        self, query: str, tags: list[str], limit: int, offset: int
    ) -> list[dict[str, Any]]:
        # Local JSONL cannot see other users' stores; friends gallery needs cloud.
        return []

    def _matches_public_query(
        self,
        card: dict[str, Any],
        revision: Any,
        query: str,
        tags: list[str],
    ) -> bool:
        revision = revision or {}
        if tags:
            rev_tags = [str(t).lower() for t in (revision.get("tags") or [])]
            if not all(tag.strip().lower() in rev_tags for tag in tags if tag.strip()):
                return False
        if query:
            q = query.strip().lower()
            text = " ".join(
                [
                    str(revision.get("title") or ""),
                    str(revision.get("statement") or ""),
                    str(revision.get("body") or ""),
                    str(card.get("domain") or ""),
                ]
            ).lower()
            if q not in text:
                return False
        return True

    def create_revision(
        self, card_id: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a new revision of an existing card. Owner-only."""
        card_detail = self.get_card(card_id)
        if card_detail is None:
            raise ValueError("Card not found")
        card = card_detail["card"]
        if str(card.get("owner_user_id")) != self.user_id:
            raise ValueError("Not authorized to edit this card")
        current_revision = card_detail.get("revision") or {}
        if self.client is not None:
            return self._create_revision_cloud(card, payload, current_revision)
        return self._create_revision_local(card, payload, current_revision)

    def _create_revision_cloud(
        self,
        card: dict[str, Any],
        payload: dict[str, Any],
        current_revision: dict[str, Any],
    ) -> dict[str, Any]:
        card_id = str(card["id"])
        rev_resp = (
            self.client.table("card_revisions")
            .select("revision_number")
            .eq("card_id", card_id)
            .order("revision_number", desc=True)
            .limit(1)
            .execute()
        )
        rows = rev_resp.data or []
        next_number = int(rows[0].get("revision_number") or 0) + 1 if rows else 1
        revision_id = uuid.uuid4()
        now = _now()
        revision_row = {
            "id": str(revision_id),
            "card_id": card_id,
            "revision_number": next_number,
            "title": str(payload.get("title") or current_revision.get("title") or "Untitled"),
            "statement": str(payload.get("statement") or current_revision.get("statement") or ""),
            "body": str(payload.get("body") or current_revision.get("body") or ""),
            "formal_status": str(current_revision.get("formal_status") or ""),
            "lean_name": str(current_revision.get("lean_name") or ""),
            "lean_code": str(current_revision.get("lean_code") or ""),
            "evidence_id": str(current_revision.get("evidence_id") or ""),
            "source_run_session_id": str(current_revision.get("source_run_session_id") or ""),
            "source_run_share_token": str(current_revision.get("source_run_share_token") or ""),
            "tags": payload.get("tags") if payload.get("tags") is not None else (current_revision.get("tags") or []),
            "domain": str(payload.get("domain") or current_revision.get("domain") or ""),
            "metadata": dict(current_revision.get("metadata") or {}),
        }
        insert_resp = self.client.table("card_revisions").insert(revision_row).execute()
        revision = (insert_resp.data or [revision_row])[0]
        self.client.table("knowledge_cards").update({
            "latest_revision_id": str(revision_id),
            "updated_at": now,
        }).eq("id", card_id).execute()
        card["latest_revision_id"] = str(revision_id)
        card["updated_at"] = now
        return {"card": card, "revision": revision}

    def _create_revision_local(
        self,
        card: dict[str, Any],
        payload: dict[str, Any],
        current_revision: dict[str, Any],
    ) -> dict[str, Any]:
        card_id = str(card["id"])
        next_number = int(current_revision.get("revision_number") or 1) + 1
        revision_id = f"cr-{uuid.uuid4().hex}"
        revision = {
            "id": revision_id,
            "card_id": card_id,
            "revision_number": next_number,
            "title": str(payload.get("title") or current_revision.get("title") or "Untitled"),
            "statement": str(payload.get("statement") or current_revision.get("statement") or ""),
            "body": str(payload.get("body") or current_revision.get("body") or ""),
            "formal_status": str(current_revision.get("formal_status") or ""),
            "lean_name": str(current_revision.get("lean_name") or ""),
            "lean_code": str(current_revision.get("lean_code") or ""),
            "evidence_id": str(current_revision.get("evidence_id") or ""),
            "source_run_session_id": str(current_revision.get("source_run_session_id") or ""),
            "source_run_share_token": str(current_revision.get("source_run_share_token") or ""),
            "tags": payload.get("tags") if payload.get("tags") is not None else (current_revision.get("tags") or []),
            "domain": str(payload.get("domain") or current_revision.get("domain") or ""),
            "metadata": dict(current_revision.get("metadata") or {}),
        }
        card_update = dict(card)
        card_update["latest_revision_id"] = revision_id
        card_update["updated_at"] = _now()
        card_update["revision"] = revision
        self.project_store._append_event({
            "type": "knowledge_card_revision_created",
            "project_id": str(card.get("project_id") or "default"),
            "card_id": card_id,
            "card": card_update,
            "revision": revision,
        })
        return {"card": card_update, "revision": revision}

    def publish_card(self, card_id: str, visibility: str) -> dict[str, Any]:
        """Change a card's visibility. Owner-only."""
        visibility = _normalize_visibility(visibility)
        if visibility == "team":
            raise ValueError("Invalid visibility")
        card_detail = self.get_card(card_id)
        if card_detail is None:
            raise ValueError("Card not found")
        card = card_detail["card"]
        if str(card.get("owner_user_id")) != self.user_id:
            raise ValueError("Not authorized to publish this card")
        if self.client is not None:
            return self._publish_card_cloud(card, visibility)
        return self._publish_card_local(card, visibility)

    def _publish_card_cloud(
        self, card: dict[str, Any], visibility: str
    ) -> dict[str, Any]:
        card_id = str(card["id"])
        now = _now()
        status = "published" if visibility != "private" else "draft"
        self.client.table("knowledge_cards").update({
            "visibility": visibility,
            "status": status,
            "updated_at": now,
        }).eq("id", card_id).execute()
        card["visibility"] = visibility
        card["status"] = status
        card["updated_at"] = now
        return {"card": card}

    def _publish_card_local(
        self, card: dict[str, Any], visibility: str
    ) -> dict[str, Any]:
        card_id = str(card["id"])
        status = "published" if visibility != "private" else "draft"
        card_update = dict(card)
        card_update["visibility"] = visibility
        card_update["status"] = status
        card_update["updated_at"] = _now()
        self.project_store._append_event({
            "type": "knowledge_card_published",
            "project_id": str(card.get("project_id") or "default"),
            "card_id": card_id,
            "card": card_update,
        })
        return {"card": card_update}

    async def import_card_into_project(
        self, card_id: str, target_project_id: str
    ) -> dict[str, Any]:
        card = await asyncio.to_thread(self.get_card, card_id)
        if card is None:
            raise ValueError("Card not found")
        try:
            await asyncio.to_thread(self.project_store.get_project, target_project_id)
        except HTTPException as exc:
            if exc.status_code == 404:
                raise ValueError("Target project not found") from exc
            raise
        card_row = card.get("card") if isinstance(card.get("card"), dict) else {}
        revision = self._get_latest_revision(card)
        status = "reviewed"
        evidence_id = ""
        if revision.get("formal_status") == "verified" and revision.get("lean_code"):
            evidence_id = await self._reverify(revision["lean_code"], target_project_id)
            if evidence_id:
                status = "verified"
        source_owner_id = str(card_row.get("owner_user_id") or "")
        source_profile = await asyncio.to_thread(self._source_owner_profile, source_owner_id)
        fact = {
            "statement": revision["statement"],
            "why": revision["body"],
            "source": f"knowledge-card:{card_id}",
            "source_type": "card_import",
            "status": status,
            "evidence": evidence_id,
            "formal_status": "verified" if status == "verified" else "",
            "lean_name": revision.get("lean_name", ""),
            "domain": revision.get("domain", ""),
            "tags": ",".join(revision.get("tags") or []),
            "metadata": {
                "provenance": {
                    "card_id": card_id,
                    "revision_id": revision["id"],
                    "imported_by": self.user_id,
                    "imported_at": _now(),
                    "source_owner_user_id": source_owner_id,
                    "source_owner_display_name": source_profile.get("display_name", ""),
                    "source_owner_phone_masked": source_profile.get("phone_masked", ""),
                    "source_project_id": str(card_row.get("project_id") or ""),
                }
            },
        }
        inserted = (await asyncio.to_thread(self.project_store.add_many, target_project_id, [fact], [], []))["facts"]
        await asyncio.to_thread(self._increment_citation, card_id)
        return {"imported": inserted[0] if inserted else {}}

    def _get_latest_revision(
        self, card: dict[str, Any] | str
    ) -> dict[str, Any]:
        if isinstance(card, str):
            card_id = card
            card = self.get_card(card_id)
            if card is None:
                raise ValueError("Card not found")
        revision = card.get("revision") or {}
        if not revision:
            raise ValueError("Card has no revision")
        return revision

    def _increment_citation(self, card_id: str) -> None:
        if self.client is not None:
            resp = (
                self.client.table("knowledge_cards")
                .select("citation_count")
                .eq("id", card_id)
                .execute()
            )
            rows = resp.data or []
            if rows:
                current = int(rows[0].get("citation_count") or 0)
                self.client.table("knowledge_cards").update(
                    {"citation_count": current + 1}
                ).eq("id", card_id).execute()
            return
        self.project_store._append_event({
            "type": "knowledge_card_citation_incremented",
            "card_id": card_id,
        })

    async def _reverify(self, lean_code: str, target_project_id: str) -> str:
        """Re-run Lean verification in the target project context.

        Returns a formal evidence id on success, or an empty string on failure.
        """
        try:
            config = load_config().lean
            runner = LeanRunner(config)
            result = await runner.check_proof(lean_code)
            if result.success and not result.uses_sorry:
                return formal_evidence_id(
                    action_name="lean_check",
                    target_claim=f"imported into {target_project_id}",
                    artifact=lean_code,
                )
            return ""
        except Exception:
            return ""

    def _source_owner_profile(self, owner_user_id: str) -> dict[str, str]:
        if not owner_user_id:
            return {"display_name": "", "phone_masked": ""}
        if owner_user_id == self.user_id:
            # Prefer cloud profile when available; else empty labels.
            pass
        if self.client is None:
            return {"display_name": "", "phone_masked": ""}
        try:
            profile = UserStore(client=self.client).get_profile(owner_user_id) or {}
        except Exception:
            profile = {}
        return {
            "display_name": str(profile.get("display_name") or "").strip(),
            "phone_masked": str(profile.get("phone_masked") or ""),
        }


def _normalize_visibility(raw: Any) -> str:
    visibility = str(raw or "private").strip().lower() or "private"
    if visibility not in _ALLOWED_VISIBILITY:
        raise ValueError("Invalid visibility")
    return visibility


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
