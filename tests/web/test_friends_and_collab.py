"""Tests for friends graph and collaborative project access."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from math_agent.web.friends import FriendsService, are_friends
from math_agent.web.knowledge_cards import KnowledgeCardService
from math_agent.web.project_access import ProjectAccess, ProjectAccessService, resolve_project_access
from math_agent.web.project_store import project_store_for_user
from math_agent.agent.react_state import ProjectContext


class FakeSupabaseClient:
    def __init__(self):
        self.tables: dict[str, list] = {}

    def table(self, name):
        return FakeTable(self.tables.setdefault(name, []))


class FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self._filters = []
        self._insert = None
        self._upsert = None
        self._update = None
        self._delete = False
        self._limit = None
        self._order = None
        self._desc = False

    def select(self, *_args):
        return self

    def insert(self, row):
        self._insert = row
        return self

    # Primary-key columns used to emulate ON CONFLICT for upsert().
    _PK_COLUMNS = ("owner_user_id", "project_id", "user_id", "requester_id", "addressee_id", "id")

    def upsert(self, row):
        self._upsert = row
        return self

    def update(self, updates):
        self._update = updates
        return self

    def delete(self):
        self._delete = True
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def in_(self, col, values):
        self._filters.append((col, ("__in__", list(values))))
        return self

    def order(self, col, desc=False):
        self._order = col
        self._desc = desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, *_args):
        return self

    def execute(self):
        if self._insert is not None:
            row = dict(self._insert)
            if "id" not in row:
                row["id"] = f"id-{len(self.rows)+1}"
            self.rows.append(row)
            return FakeResponse([row])

        if self._upsert is not None:
            row = dict(self._upsert)
            keys = [c for c in self._PK_COLUMNS if c in row]
            for existing in self.rows:
                if keys and all(existing.get(c) == row.get(c) for c in keys):
                    existing.update(row)
                    return FakeResponse([dict(existing)])
            if "id" not in row:
                row["id"] = f"id-{len(self.rows)+1}"
            self.rows.append(row)
            return FakeResponse([row])

        def _matches(row):
            for col, val in self._filters:
                if isinstance(val, tuple) and len(val) == 2 and val[0] == "__in__":
                    if row.get(col) not in val[1]:
                        return False
                elif row.get(col) != val:
                    return False
            return True

        matched = [r for r in self.rows if _matches(r)]
        if self._delete:
            for r in matched:
                self.rows.remove(r)
            return FakeResponse(matched)
        if self._update is not None:
            for r in matched:
                r.update(self._update)
            return FakeResponse(matched)
        if self._order:
            matched = sorted(
                matched,
                key=lambda r: r.get(self._order) or "",
                reverse=self._desc,
            )
        if self._limit is not None:
            matched = matched[: self._limit]
        return FakeResponse(matched)


class FakeResponse:
    def __init__(self, data):
        self.data = data


def _seed_users(client: FakeSupabaseClient, *user_ids: str) -> None:
    for uid in user_ids:
        client.tables.setdefault("conjecta_users", []).append(
            {
                "id": uid,
                "phone": f"1{uid[-10:].zfill(10)}" if len(uid) >= 4 else "13800138000",
                "phone_masked": "138****0000",
                "display_name": uid,
            }
        )


def test_friend_request_accept_and_are_friends():
    client = FakeSupabaseClient()
    _seed_users(client, "u_alice", "u_bob")
    alice = FriendsService("u_alice", client=client)
    bob = FriendsService("u_bob", client=client)
    created = alice.request_friend(user_id="u_bob")
    fid = created["friendship"]["id"]
    assert created["created"] is True
    bob.accept_request(fid)
    assert alice.are_friends("u_bob")
    assert are_friends(client, "u_alice", "u_bob")
    assert bob.list_friends()[0]["user_id"] == "u_alice"


def test_friends_card_access_requires_friendship(tmp_path):
    client = FakeSupabaseClient()
    _seed_users(client, "u_alice", "u_bob", "u_carol")
    store = project_store_for_user("u_alice")
    store.root = tmp_path
    store.save_project("proj-1", {"name": "A"})
    fact = store.add_fact("proj-1", "lemma", "why", "src")
    alice_cards = KnowledgeCardService("u_alice", project_store=store, client=client)
    published = alice_cards.publish_from_project_item(
        "proj-1", fact["id"], "fact", {"title": "Lemma", "visibility": "friends"}
    )
    card_id = published["card"]["id"]

    bob = KnowledgeCardService("u_bob", project_store=store, client=client)
    assert bob.get_card(card_id) is None

    FriendsService("u_alice", client=client).request_friend(user_id="u_bob")
    req = client.tables["friendships"][0]
    FriendsService("u_bob", client=client).accept_request(req["id"])
    assert bob.get_card(card_id) is not None
    assert KnowledgeCardService("u_carol", project_store=store, client=client).get_card(card_id) is None


@pytest.mark.asyncio
async def test_import_provenance_includes_source_owner(tmp_path):
    client = FakeSupabaseClient()
    _seed_users(client, "u_alice", "u_bob")
    client.tables["conjecta_users"][0]["display_name"] = "Alice"
    store_a = project_store_for_user("u_alice")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    store_a.root = tmp_path / "a"
    store_a.save_project("src", {"name": "Src"})
    store_b = project_store_for_user("u_bob")
    store_b.root = tmp_path / "b"
    store_b.save_project("dst", {"name": "Dst"})
    fact = store_a.add_fact("src", "2+2=4", "arith", "math")
    alice = KnowledgeCardService("u_alice", project_store=store_a, client=client)
    card = alice.publish_from_project_item(
        "src", fact["id"], "fact", {"title": "Arith", "visibility": "friends"}
    )
    FriendsService("u_alice", client=client).request_friend(user_id="u_bob")
    FriendsService("u_bob", client=client).accept_request(client.tables["friendships"][0]["id"])
    bob = KnowledgeCardService("u_bob", project_store=store_b, client=client)
    result = await bob.import_card_into_project(card["card"]["id"], "dst")
    prov = result["imported"]["metadata"]["provenance"]
    assert prov["source_owner_user_id"] == "u_alice"
    assert prov["source_owner_display_name"] == "Alice"
    assert prov["source_project_id"] == "src"
    assert prov["card_id"] == card["card"]["id"]


def test_project_access_lead_and_collaborator():
    client = FakeSupabaseClient()
    _seed_users(client, "u_lead", "u_collab")
    client.tables.setdefault("conjecta_projects", []).append(
        {
            "owner_user_id": "u_lead",
            "id": "research-1",
            "name": "Research",
            "starred": False,
            "payload": {"id": "research-1", "name": "Research"},
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    svc = ProjectAccessService(client=client)
    lead = svc.resolve("u_lead", "research-1")
    assert lead.role == "lead"
    assert lead.knowledge_tenant_id == "u_lead"
    svc.add_member(lead, "u_collab")
    collab = svc.resolve("u_collab", "research-1", owner_user_id="u_lead")
    assert collab.role == "collaborator"
    assert collab.can_edit_knowledge
    assert not collab.can_manage_members
    with pytest.raises(HTTPException):
        svc.add_member(collab, "u_other")
    listed = svc.list_accessible_projects("u_collab")
    assert any(p["id"] == "research-1" and p["role"] == "collaborator" for p in listed)


def test_personal_project_not_materialized_on_read_only_resolve():
    """Read-only resolve returns lead access without writing a cloud row."""
    client = FakeSupabaseClient()
    _seed_users(client, "u_alice")
    client.tables.setdefault("conjecta_projects", [])
    client.tables.setdefault("project_members", [])
    svc = ProjectAccessService(client=client)
    access = svc.resolve("u_alice", "default")
    assert access.role == "lead"
    assert access.owner_user_id == "u_alice"
    assert access.project_id == "default"
    assert not svc.repo.owns_project("u_alice", "default")


def test_personal_project_materialized_on_write_path():
    """Write-path resolve (create_if_missing=True) materializes the cloud row."""
    client = FakeSupabaseClient()
    _seed_users(client, "u_alice")
    client.tables.setdefault("conjecta_projects", [])
    client.tables.setdefault("project_members", [])
    svc = ProjectAccessService(client=client)
    access = svc.resolve("u_alice", "default", create_if_missing=True)
    assert access.role == "lead"
    assert svc.repo.owns_project("u_alice", "default")
    listed = svc.list_accessible_projects("u_alice")
    assert any(p["id"] == "default" and p["role"] == "lead" for p in listed)


def test_resolve_project_access_fail_closed_on_cloud_error(monkeypatch):
    """A cloud failure must not silently fall back to a personal lead namespace."""
    import math_agent.web.project_access as pa

    monkeypatch.setattr(pa, "service_role_configured", lambda: True)

    class BrokenService:
        def __init__(self, client=None):
            pass

        def resolve(self, *args, **kwargs):
            raise RuntimeError("supabase down")

    monkeypatch.setattr(pa, "ProjectAccessService", BrokenService)
    with pytest.raises(HTTPException) as excinfo:
        resolve_project_access("u_alice", "research-1", owner_user_id="u_lead")
    assert excinfo.value.status_code == 503


def test_add_member_idempotent_and_by_phone():
    client = FakeSupabaseClient()
    _seed_users(client, "u_lead", "u_collab")
    client.tables.setdefault("conjecta_projects", []).append(
        {
            "owner_user_id": "u_lead",
            "id": "research-1",
            "name": "Research",
            "starred": False,
            "payload": {"id": "research-1", "name": "Research"},
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    svc = ProjectAccessService(client=client)
    lead = svc.resolve("u_lead", "research-1")

    first = svc.add_member(lead, "u_collab")
    assert first["created"] is True
    second = svc.add_member(lead, "u_collab")
    assert second["created"] is False
    members = [r for r in client.tables["project_members"] if r["user_id"] == "u_collab"]
    assert len(members) == 1

    phone = "13912345678"
    client.tables["conjecta_users"][1]["phone"] = phone
    svc.remove_member(lead, "u_collab")
    by_phone = svc.add_member(lead, phone=phone)
    assert by_phone["created"] is True
    assert by_phone["member"]["user_id"] == "u_collab"


def test_list_members_batches_profiles():
    client = FakeSupabaseClient()
    _seed_users(client, "u_lead", "u_one", "u_two")
    client.tables.setdefault("conjecta_projects", []).append(
        {
            "owner_user_id": "u_lead",
            "id": "research-1",
            "name": "Research",
            "starred": False,
            "payload": {"id": "research-1", "name": "Research"},
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    svc = ProjectAccessService(client=client)
    lead = svc.resolve("u_lead", "research-1")
    svc.add_member(lead, "u_one")
    svc.add_member(lead, "u_two")
    members = svc.list_members(lead)
    labels = {m["user_id"]: m["label"] for m in members}
    assert labels == {"u_lead": "u_lead", "u_one": "u_one", "u_two": "u_two"}
    roles = {m["user_id"]: m["role"] for m in members}
    assert roles["u_lead"] == "lead"
    assert roles["u_one"] == "collaborator"


def test_foreign_project_still_404_without_membership():
    client = FakeSupabaseClient()
    _seed_users(client, "u_alice", "u_bob")
    client.tables.setdefault("conjecta_projects", []).append(
        {
            "owner_user_id": "u_alice",
            "id": "research-1",
            "name": "Research",
            "starred": False,
            "payload": {"id": "research-1", "name": "Research"},
            "updated_at": "2026-01-01T00:00:00Z",
        }
    )
    client.tables.setdefault("project_members", [])
    svc = ProjectAccessService(client=client)
    with pytest.raises(HTTPException) as excinfo:
        svc.resolve("u_bob", "research-1", owner_user_id="u_alice")
    assert excinfo.value.status_code == 404
    assert excinfo.value.detail == "Project not found."


def test_resolve_project_access_local_solo():
    access = resolve_project_access("u_solo", "default")
    assert access == ProjectAccess(
        actor_user_id="u_solo",
        owner_user_id="u_solo",
        project_id="default",
        role="lead",
    )


def test_agent_project_context_binds_lead_user_id():
    access = ProjectAccess(
        actor_user_id="u_collab",
        owner_user_id="u_lead",
        project_id="research-1",
        role="collaborator",
    )
    ctx = ProjectContext(project_id=access.project_id, user_id=access.knowledge_tenant_id)
    assert ctx.user_id == "u_lead"
    assert ctx.project_id == "research-1"
