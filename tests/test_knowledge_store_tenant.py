from __future__ import annotations

from math_agent.knowledge.supabase import KnowledgeStore


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, store: dict, table: str):
        self.store = store
        self.table = table
        self._op = "select"
        self._payload = None
        self._filters: dict[str, str] = {}
        self._in_filters: dict[str, set[str]] = {}
        self._limit = 100
        self._offset = 0

    def select(self, *_a, **_k):
        self._op = "select"
        return self

    def insert(self, row):
        self._op = "insert"
        self._payload = row
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def in_(self, key, values):
        self._in_filters[key] = set(values)
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, n):
        self._limit = n
        return self

    def offset(self, n):
        self._offset = n
        return self

    def or_(self, *_a, **_k):
        return self

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self._op == "insert":
            if isinstance(self._payload, list):
                for r in self._payload:
                    rows.append(dict(r))
                return _FakeExecute([dict(r) for r in self._payload])
            rows.append(dict(self._payload))
            return _FakeExecute([dict(self._payload)])
        matched = [
            dict(r)
            for r in rows
            if all(r.get(k) == v for k, v in self._filters.items())
            and all(r.get(k) in values for k, values in self._in_filters.items())
        ]
        return _FakeExecute(matched[self._offset : self._offset + self._limit])


class _FakeClient:
    def __init__(self):
        self._store: dict[str, list] = {}

    def table(self, name: str):
        return _FakeQuery(self._store, name)


def test_knowledge_store_scopes_by_user_id():
    client = _FakeClient()
    a = KnowledgeStore(user_id="u_a", client=client)
    b = KnowledgeStore(user_id="u_b", client=client)
    a.add_fact("default", "fact-a", why="wa")
    b.add_fact("default", "fact-b", why="wb")
    assert [r["statement"] for r in a.list_facts("default")] == ["fact-a"]
    assert [r["statement"] for r in b.list_facts("default")] == ["fact-b"]
    assert all(r["user_id"] == "u_a" for r in a.list_facts("default"))


def test_cloud_search_filters_untrusted_status_but_listing_keeps_all():
    client = _FakeClient()
    store = KnowledgeStore(user_id="u_a", client=client)
    store.add_many(
        "default",
        [
            {"statement": "shared candidate", "status": "candidate"},
            {"statement": "shared rejected", "status": "rejected"},
            {"statement": "shared approved", "status": "approved"},
            {"statement": "shared verified", "status": "verified"},
        ],
        [],
        [],
    )

    assert {row["status"] for row in store.list_facts("default")} == {
        "candidate",
        "rejected",
        "approved",
        "verified",
    }
    assert [row["status"] for row in store.search_facts("default", "shared")] == [
        "approved",
        "verified",
    ]


def test_cloud_manual_knowledge_is_explicitly_approved():
    store = KnowledgeStore(user_id="u_a", client=_FakeClient())

    item = store.add_fact("default", "manual fact")

    assert item["status"] == "approved"


def test_cloud_search_reranks_long_problem_query():
    client = _FakeClient()
    store = KnowledgeStore(user_id="u_a", client=client)
    store.add_many(
        "default",
        [
            {"statement": "偶数的平方可以被四整除", "status": "approved"},
            {"statement": "素数有无穷多个", "status": "approved"},
        ],
        [],
        [],
    )

    matches = store.search_facts(
        "default",
        "请证明任意偶数的平方能够被 4 整除",
        limit=1,
    )

    assert [row["statement"] for row in matches] == ["偶数的平方可以被四整除"]
