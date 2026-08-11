import pytest
from unittest.mock import AsyncMock, patch

from math_agent.lean.result import LeanResult
from math_agent.web.knowledge_cards import KnowledgeCardService
from math_agent.web.project_store import ProjectStore, project_store_for_user


@pytest.mark.parametrize(
    "kind, add_kwargs",
    [
        ("fact", {"statement": "sqrt(2) is irrational"}),
        ("intuition", {"title": "Irrationality intuition", "body": "Body"}),
        ("trick", {"title": "Contradiction trick", "body": "Assume not."}),
    ],
)
def test_get_knowledge_item_by_kind(tmp_path, kind, add_kwargs):
    store = ProjectStore(tmp_path)
    add = getattr(store, f"add_{kind}")
    item = add("proj-1", **add_kwargs)
    found = store.get_knowledge_item("proj-1", item["id"], kind)
    assert found == item
    assert found is not item
    # Mutating the returned dict must not affect the stored item.
    found["mutated"] = True
    again = store.get_knowledge_item("proj-1", item["id"], kind)
    assert "mutated" not in again


def test_get_knowledge_item_graph_node(tmp_path):
    store = ProjectStore(tmp_path)
    nodes = store.add_knowledge_graph_nodes(
        "proj-1",
        [{"id": "n1", "ref": "ref-1", "title": "Node one", "kind": "theorem"}],
    )
    node = nodes[0]
    by_id = store.get_knowledge_item("proj-1", "n1", "graph_node")
    assert by_id == node
    assert by_id is not node
    assert store.get_knowledge_item("proj-1", "ref-1", "graph_node") == node
    assert store.get_knowledge_item("proj-1", "unknown", "graph_node") is None
    by_id["mutated"] = True
    again = store.get_knowledge_item("proj-1", "n1", "graph_node")
    assert "mutated" not in again


def test_get_knowledge_item_missing(tmp_path):
    store = ProjectStore(tmp_path)
    store.add_fact("proj-1", "sqrt(2) is irrational")
    assert store.get_knowledge_item("proj-1", "missing-id", "fact") is None
    assert store.get_knowledge_item("proj-1", "missing-id", "intuition") is None
    assert store.get_knowledge_item("proj-1", "missing-id", "trick") is None
    assert store.get_knowledge_item("proj-1", "missing-id", "graph_node") is None
    assert store.get_knowledge_item("proj-1", "missing-id", "invalid_kind") is None


def test_service_requires_user_id():
    with pytest.raises(ValueError, match="user_id"):
        KnowledgeCardService(user_id=None)


def test_publish_fact_as_private_card(tmp_path):
    store = project_store_for_user("u-test")
    store.root = tmp_path
    store.save_project("proj-1", {"name": "Test Project"})
    fact = store.add_fact("proj-1", "sqrt(2) is irrational", "Because its square is 2", "Euclid")
    fact_id = fact["id"]
    svc = KnowledgeCardService(user_id="u-test")
    svc.project_store = store
    result = svc.publish_from_project_item(
        "proj-1", fact_id, "fact",
        {"title": "Irrationality of sqrt(2)", "visibility": "private"}
    )
    assert result["card"]["visibility"] == "private"
    assert result["revision"]["title"] == "Irrationality of sqrt(2)"
    assert result["revision"]["statement"] == "sqrt(2) is irrational"


class FakeSupabaseClient:
    def __init__(self):
        self.tables = {}

    def table(self, name):
        return FakeTable(self.tables.setdefault(name, []))


class FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self._filters = []
        self._insert = None
        self._update = None
        self._order = None
        self._desc = False
        self._limit = None
        self._range = None

    def select(self, *_args):
        return self

    def insert(self, row):
        self._insert = row
        return self

    def update(self, updates):
        self._update = updates
        return self

    def eq(self, col, val):
        self._filters.append((col, val))
        return self

    def order(self, col, desc=False):
        self._order = col
        self._desc = desc
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def execute(self):
        filters = list(self._filters)
        insert = self._insert
        update = self._update
        order = self._order
        desc = self._desc
        limit = self._limit
        rng = self._range
        self._filters = []
        self._insert = None
        self._update = None
        self._order = None
        self._desc = False
        self._limit = None
        self._range = None

        data = list(self.rows)
        for col, val in filters:
            data = [r for r in data if r.get(col) == val]
        if order is not None:
            data = sorted(data, key=lambda r: r.get(order) or 0, reverse=desc)

        if insert is not None:
            row = dict(insert)
            self.rows.append(row)
            result = [row]
        elif update is not None:
            updated = []
            for row in self.rows:
                if all(row.get(col) == val for col, val in filters):
                    row.update(update)
                    updated.append(dict(row))
            result = updated
        else:
            result = data

        if limit is not None:
            result = result[:limit]
        if rng is not None:
            start, end = rng
            result = result[start : end + 1]
        return type("R", (), {"data": result})()


def test_publish_with_cloud_client():
    store = project_store_for_user("u-cloud")
    store.save_project("proj-1", {"name": "Cloud Project"})
    fact = store.add_fact("proj-1", "2 + 2 = 4", "Arithmetic", "Math")
    client = FakeSupabaseClient()
    svc = KnowledgeCardService(user_id="u-cloud", project_store=store, client=client)
    result = svc.publish_from_project_item(
        "proj-1", fact["id"], "fact",
        {"title": "Basic arithmetic", "visibility": "public"}
    )
    assert result["card"]["visibility"] == "public"
    assert client.tables["knowledge_cards"]
    assert client.tables["card_revisions"]


@pytest.mark.asyncio
async def test_import_card_without_reverification_for_reviewed_status(tmp_path):
    store = project_store_for_user("u-import")
    store.root = tmp_path
    store.save_project("proj-source", {"name": "Source"})
    store.save_project("proj-target", {"name": "Target"})
    fact = store.add_fact("proj-source", "Every group of prime order is cyclic", "Standard theorem", "Algebra")
    svc = KnowledgeCardService(user_id="u-import", project_store=store)
    card = svc.publish_from_project_item("proj-source", fact["id"], "fact", {"title": "Cyclic groups"})
    result = await svc.import_card_into_project(card["card"]["id"], "proj-target")
    assert result["imported"]["status"] == "reviewed"
    assert "knowledge-card:" in result["imported"]["source"]
    assert isinstance(result["imported"]["metadata"]["provenance"], dict)
    assert result["imported"]["metadata"]["provenance"]["card_id"] == card["card"]["id"]
    assert result["imported"]["metadata"]["provenance"]["imported_by"] == "u-import"
    assert "imported_at" in result["imported"]["metadata"]["provenance"]


@pytest.mark.asyncio
async def test_import_verified_card_reverified_to_verified(tmp_path, monkeypatch):
    store = project_store_for_user("u-import")
    store.root = tmp_path
    store.save_project("proj-source", {"name": "Source"})
    store.save_project("proj-target", {"name": "Target"})
    fact = store.add_fact("proj-source", "2 + 2 = 4", "Arithmetic", "Math")
    store.update_knowledge_item(
        "proj-source",
        fact["id"],
        "fact",
        {"formal_status": "verified", "lean_code": "example : 2 + 2 = 4 := rfl"},
    )
    svc = KnowledgeCardService(user_id="u-import", project_store=store)
    card = svc.publish_from_project_item("proj-source", fact["id"], "fact", {"title": "Basic arithmetic"})
    assert card["revision"]["lean_code"]

    fake_runner = AsyncMock()
    fake_runner.check_proof = AsyncMock(return_value=LeanResult(success=True, uses_sorry=False))

    with patch("math_agent.web.knowledge_cards.LeanRunner", return_value=fake_runner):
        result = await svc.import_card_into_project(card["card"]["id"], "proj-target")

    assert result["imported"]["status"] == "verified"
    assert result["imported"]["formal_status"] == "verified"
    assert result["imported"]["evidence"].startswith("formal-")
    assert isinstance(result["imported"]["metadata"]["provenance"], dict)
    assert result["imported"]["metadata"]["provenance"]["card_id"] == card["card"]["id"]
    assert "imported_at" in result["imported"]["metadata"]["provenance"]
    fake_runner.check_proof.assert_awaited_once_with("example : 2 + 2 = 4 := rfl")


@pytest.mark.asyncio
async def test_import_verified_card_reverification_fails_to_reviewed(tmp_path, monkeypatch):
    store = project_store_for_user("u-import")
    store.root = tmp_path
    store.save_project("proj-source", {"name": "Source"})
    store.save_project("proj-target", {"name": "Target"})
    fact = store.add_fact("proj-source", "2 + 2 = 4", "Arithmetic", "Math")
    store.update_knowledge_item(
        "proj-source",
        fact["id"],
        "fact",
        {"formal_status": "verified", "lean_code": "example : 2 + 2 = 4 := sorry"},
    )
    svc = KnowledgeCardService(user_id="u-import", project_store=store)
    card = svc.publish_from_project_item("proj-source", fact["id"], "fact", {"title": "Basic arithmetic"})
    assert card["revision"]["lean_code"]

    fake_runner = AsyncMock()
    fake_runner.check_proof = AsyncMock(return_value=LeanResult(success=False, uses_sorry=True))

    with patch("math_agent.web.knowledge_cards.LeanRunner", return_value=fake_runner):
        result = await svc.import_card_into_project(card["card"]["id"], "proj-target")

    assert result["imported"]["status"] == "reviewed"
    assert result["imported"]["formal_status"] == ""
    assert result["imported"]["evidence"] == ""


def test_export_markdown():
    store = project_store_for_user("u-export")
    store.save_project("proj-1", {"name": "Export"})
    fact = store.add_fact("proj-1", "Euclid's theorem", "Infinitely many primes", "Number theory")
    svc = KnowledgeCardService(user_id="u-export", project_store=store)
    card = svc.publish_from_project_item("proj-1", fact["id"], "fact", {"title": "Euclid"})
    md = svc.export_card(card["card"]["id"], "markdown")
    assert "# Euclid" in md
    assert "Euclid's theorem" in md


def test_export_latex():
    store = project_store_for_user("u-export")
    store.save_project("proj-1", {"name": "Export"})
    fact = store.add_fact("proj-1", "Euclid's theorem", "Infinitely many primes", "Number theory")
    svc = KnowledgeCardService(user_id="u-export", project_store=store)
    card = svc.publish_from_project_item("proj-1", fact["id"], "fact", {"title": "Euclid"})
    latex = svc.export_card(card["card"]["id"], "latex")
    assert "\\section*{Euclid}" in latex
    assert "Euclid's theorem" in latex


def test_export_bibtex():
    store = project_store_for_user("u-export")
    store.save_project("proj-1", {"name": "Export"})
    fact = store.add_fact("proj-1", "Euclid's theorem", "Infinitely many primes", "Number theory")
    svc = KnowledgeCardService(user_id="u-export", project_store=store)
    card = svc.publish_from_project_item("proj-1", fact["id"], "fact", {"title": "Euclid"})
    bib = svc.export_card(card["card"]["id"], "bibtex")
    assert bib.startswith("@misc{conjecta")
    # First line should open the entry and continue with a comma, not close the brace.
    first_line = bib.splitlines()[0]
    assert first_line.endswith(",")
    assert not first_line.endswith("},")
    assert "title = {Euclid}" in bib
    assert "year = {" in bib


def test_export_lean_with_code():
    store = project_store_for_user("u-export")
    store.save_project("proj-1", {"name": "Export"})
    fact = store.add_fact("proj-1", "2 + 2 = 4", "Arithmetic", "Math")
    store.update_knowledge_item(
        "proj-1", fact["id"], "fact",
        {"lean_code": "example : 2 + 2 = 4 := rfl"},
    )
    svc = KnowledgeCardService(user_id="u-export", project_store=store)
    card = svc.publish_from_project_item("proj-1", fact["id"], "fact", {"title": "Arithmetic"})
    lean = svc.export_card(card["card"]["id"], "lean")
    assert lean == "example : 2 + 2 = 4 := rfl"


def test_export_lean_without_code():
    store = project_store_for_user("u-export")
    store.save_project("proj-1", {"name": "Export"})
    fact = store.add_fact("proj-1", "Euclid's theorem", "Infinitely many primes", "Number theory")
    svc = KnowledgeCardService(user_id="u-export", project_store=store)
    card = svc.publish_from_project_item("proj-1", fact["id"], "fact", {"title": "Euclid"})
    lean = svc.export_card(card["card"]["id"], "lean")
    assert lean == "-- no Lean code available\n"


def test_export_unsupported_format_raises_value_error():
    store = project_store_for_user("u-export")
    store.save_project("proj-1", {"name": "Export"})
    fact = store.add_fact("proj-1", "Euclid's theorem", "Infinitely many primes", "Number theory")
    svc = KnowledgeCardService(user_id="u-export", project_store=store)
    card = svc.publish_from_project_item("proj-1", fact["id"], "fact", {"title": "Euclid"})
    with pytest.raises(ValueError, match="Unsupported export format"):
        svc.export_card(card["card"]["id"], "docx")


def test_export_missing_card_raises_value_error():
    svc = KnowledgeCardService(user_id="u-export")
    with pytest.raises(ValueError, match="Card not found"):
        svc.export_card("missing-card-id", "markdown")


def test_metadata_dict_preserved_by_add_many(tmp_path):
    store = ProjectStore(tmp_path)
    store.save_project("proj-1", {"name": "Metadata Test"})
    inserted = store.add_many(
        "proj-1",
        [{
            "statement": "Test fact",
            "source": "test",
            "source_type": "manual",
            "status": "approved",
            "metadata": {"provenance": {"card_id": "card-123"}, "tags": ["a", "b"]},
        }],
        [],
        [],
    )["facts"]
    assert len(inserted) == 1
    assert inserted[0]["metadata"] == {"provenance": {"card_id": "card-123"}, "tags": ["a", "b"]}
    found = store.get_knowledge_item("proj-1", inserted[0]["id"], "fact")
    assert found["metadata"] == {"provenance": {"card_id": "card-123"}, "tags": ["a", "b"]}


def test_metadata_string_kept_as_string(tmp_path):
    store = ProjectStore(tmp_path)
    store.save_project("proj-1", {"name": "Metadata Test"})
    inserted = store.add_many(
        "proj-1",
        [{
            "statement": "Test fact",
            "source": "test",
            "source_type": "manual",
            "status": "approved",
            "metadata": '{"legacy": true}',
        }],
        [],
        [],
    )["facts"]
    assert len(inserted) == 1
    assert inserted[0]["metadata"] == '{"legacy": true}'



def test_create_revision_local_updates_latest_revision(tmp_path):
    store = project_store_for_user("u-rev")
    store.root = tmp_path
    store.save_project("proj-1", {"name": "Test Project"})
    fact = store.add_fact("proj-1", "sqrt(2) is irrational", "Because its square is 2", "Euclid")
    svc = KnowledgeCardService(user_id="u-rev", project_store=store)
    card = svc.publish_from_project_item(
        "proj-1", fact["id"], "fact",
        {"title": "Irrationality of sqrt(2)", "visibility": "private"}
    )
    card_id = card["card"]["id"]
    result = svc.create_revision(card_id, {"title": "Updated title", "body": "Updated body"})
    assert result["revision"]["revision_number"] == 2
    assert result["card"]["latest_revision_id"] == result["revision"]["id"]
    fetched = svc.get_card(card_id)
    assert fetched["revision"]["title"] == "Updated title"
    assert fetched["revision"]["body"] == "Updated body"
    assert fetched["revision"]["statement"] == "sqrt(2) is irrational"


def test_create_revision_local_preserves_existing_fields(tmp_path):
    store = project_store_for_user("u-rev")
    store.root = tmp_path
    store.save_project("proj-1", {"name": "Test Project"})
    fact = store.add_fact("proj-1", "sqrt(2) is irrational", "Because its square is 2", "Euclid")
    svc = KnowledgeCardService(user_id="u-rev", project_store=store)
    card = svc.publish_from_project_item(
        "proj-1", fact["id"], "fact",
        {"title": "Irrationality of sqrt(2)", "visibility": "private", "tags": ["a", "b"]}
    )
    card_id = card["card"]["id"]
    result = svc.create_revision(card_id, {})
    assert result["revision"]["revision_number"] == 2
    assert result["revision"]["title"] == "Irrationality of sqrt(2)"
    assert result["revision"]["tags"] == ["a", "b"]


def test_create_revision_rejects_non_owner(tmp_path):
    store = project_store_for_user("u-owner")
    store.root = tmp_path
    store.save_project("proj-1", {"name": "Test Project"})
    fact = store.add_fact("proj-1", "sqrt(2) is irrational", "Because its square is 2", "Euclid")
    owner_svc = KnowledgeCardService(user_id="u-owner", project_store=store)
    card = owner_svc.publish_from_project_item(
        "proj-1", fact["id"], "fact",
        {"title": "Irrationality of sqrt(2)", "visibility": "public"}
    )
    other_svc = KnowledgeCardService(user_id="u-other", project_store=store)
    with pytest.raises(ValueError, match="Not authorized to edit this card"):
        other_svc.create_revision(card["card"]["id"], {"title": "Hacked"})


def test_create_revision_missing_card():
    svc = KnowledgeCardService(user_id="u-rev")
    with pytest.raises(ValueError, match="Card not found"):
        svc.create_revision("missing-card-id", {"title": "x"})


def test_publish_card_local_makes_private_public(tmp_path):
    store = project_store_for_user("u-pub")
    store.root = tmp_path
    store.save_project("proj-1", {"name": "Test Project"})
    fact = store.add_fact("proj-1", "sqrt(2) is irrational", "Because its square is 2", "Euclid")
    svc = KnowledgeCardService(user_id="u-pub", project_store=store)
    card = svc.publish_from_project_item(
        "proj-1", fact["id"], "fact",
        {"title": "Irrationality of sqrt(2)", "visibility": "private"}
    )
    card_id = card["card"]["id"]
    result = svc.publish_card(card_id, "public")
    assert result["card"]["visibility"] == "public"
    assert result["card"]["status"] == "published"
    fetched = svc.get_card(card_id)
    assert fetched["card"]["visibility"] == "public"
    public_cards = svc.list_public_cards()
    assert any(c["card"]["id"] == card_id for c in public_cards)


def test_publish_card_local_makes_public_private(tmp_path):
    store = project_store_for_user("u-pub")
    store.root = tmp_path
    store.save_project("proj-1", {"name": "Test Project"})
    fact = store.add_fact("proj-1", "sqrt(2) is irrational", "Because its square is 2", "Euclid")
    svc = KnowledgeCardService(user_id="u-pub", project_store=store)
    card = svc.publish_from_project_item(
        "proj-1", fact["id"], "fact",
        {"title": "Irrationality of sqrt(2)", "visibility": "public"}
    )
    card_id = card["card"]["id"]
    result = svc.publish_card(card_id, "private")
    assert result["card"]["visibility"] == "private"
    assert result["card"]["status"] == "draft"


def test_publish_card_rejects_non_owner(tmp_path):
    store = project_store_for_user("u-owner")
    store.root = tmp_path
    store.save_project("proj-1", {"name": "Test Project"})
    fact = store.add_fact("proj-1", "sqrt(2) is irrational", "Because its square is 2", "Euclid")
    owner_svc = KnowledgeCardService(user_id="u-owner", project_store=store)
    card = owner_svc.publish_from_project_item(
        "proj-1", fact["id"], "fact",
        {"title": "Irrationality of sqrt(2)", "visibility": "public"}
    )
    other_svc = KnowledgeCardService(user_id="u-other", project_store=store)
    with pytest.raises(ValueError, match="Not authorized to publish this card"):
        other_svc.publish_card(card["card"]["id"], "private")


def test_publish_card_rejects_invalid_visibility(tmp_path):
    store = project_store_for_user("u-pub")
    store.root = tmp_path
    store.save_project("proj-1", {"name": "Test Project"})
    fact = store.add_fact("proj-1", "sqrt(2) is irrational", "Because its square is 2", "Euclid")
    svc = KnowledgeCardService(user_id="u-pub", project_store=store)
    card = svc.publish_from_project_item(
        "proj-1", fact["id"], "fact",
        {"title": "Irrationality of sqrt(2)", "visibility": "private"}
    )
    with pytest.raises(ValueError, match="Invalid visibility"):
        svc.publish_card(card["card"]["id"], "unlisted")


def test_create_revision_with_cloud_client():
    store = project_store_for_user("u-cloud-rev")
    store.save_project("proj-1", {"name": "Cloud Project"})
    fact = store.add_fact("proj-1", "2 + 2 = 4", "Arithmetic", "Math")
    client = FakeSupabaseClient()
    svc = KnowledgeCardService(user_id="u-cloud-rev", project_store=store, client=client)
    card = svc.publish_from_project_item(
        "proj-1", fact["id"], "fact",
        {"title": "Basic arithmetic", "visibility": "private"}
    )
    card_id = card["card"]["id"]
    result = svc.create_revision(card_id, {"title": "Updated arithmetic", "body": "New body"})
    assert result["revision"]["revision_number"] == 2
    assert result["card"]["latest_revision_id"] == result["revision"]["id"]
    assert client.tables["card_revisions"][-1]["title"] == "Updated arithmetic"
    assert client.tables["knowledge_cards"][0]["latest_revision_id"] == result["revision"]["id"]


def test_publish_card_with_cloud_client():
    store = project_store_for_user("u-cloud-pub")
    store.save_project("proj-1", {"name": "Cloud Project"})
    fact = store.add_fact("proj-1", "2 + 2 = 4", "Arithmetic", "Math")
    client = FakeSupabaseClient()
    svc = KnowledgeCardService(user_id="u-cloud-pub", project_store=store, client=client)
    card = svc.publish_from_project_item(
        "proj-1", fact["id"], "fact",
        {"title": "Basic arithmetic", "visibility": "private"}
    )
    result = svc.publish_card(card["card"]["id"], "public")
    assert result["card"]["visibility"] == "public"
    assert result["card"]["status"] == "published"
    assert client.tables["knowledge_cards"][0]["visibility"] == "public"
    assert client.tables["knowledge_cards"][0]["status"] == "published"


def test_publish_from_turn_with_cloud_client():
    store = project_store_for_user("u-cloud-turn")
    store.save_project("proj-1", {"name": "Cloud Project"})
    turn = store.add_turn(
        "proj-1",
        {
            "problem": "Prove sqrt(2) is irrational",
            "answer": "Suppose not ...",
            "attachments": [],
            "verification_status": "verified",
            "lean_proofs": ["proof one", "proof two"],
        },
    )
    client = FakeSupabaseClient()
    svc = KnowledgeCardService(user_id="u-cloud-turn", project_store=store, client=client)
    result = svc.publish_from_turn("proj-1", turn["id"], {"visibility": "public"})
    assert result["card"]["source_item_kind"] == "turn"
    assert result["card"]["source_item_id"] == turn["id"]
    assert result["revision"]["formal_status"] == "verified"
    assert result["revision"]["lean_code"] == "proof one\n\nproof two"
    assert client.tables["knowledge_cards"]
    assert client.tables["card_revisions"]


def test_publish_from_turn_missing_turn(tmp_path):
    store = ProjectStore(tmp_path)
    svc = KnowledgeCardService(user_id="u-turn-missing", project_store=store)
    with pytest.raises(ValueError, match="Source turn not found"):
        svc.publish_from_turn("proj-1", "missing-turn", {})
