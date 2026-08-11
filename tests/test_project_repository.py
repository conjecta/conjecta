from __future__ import annotations

from math_agent.web.project_repository import PROJECTS_TABLE, ProjectRepository


class _FakeExecute:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, store: dict[str, list[dict]], table: str):
        self.store = store
        self.table = table
        self._op = "select"
        self._payload = None
        self._filters: dict[str, object] = {}
        self._limit = 100
        self._order_column: str | None = None
        self._order_desc = False

    def select(self, *_args, **_kwargs):
        self._op = "select"
        return self

    def insert(self, row):
        self._op = "insert"
        self._payload = dict(row)
        return self

    def update(self, row):
        self._op = "update"
        self._payload = dict(row)
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def order(self, column, **kwargs):
        self._order_column = column
        self._order_desc = bool(kwargs.get("desc"))
        return self

    def limit(self, value):
        self._limit = value
        return self

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self._op == "insert":
            rows.append(dict(self._payload))
            return _FakeExecute([dict(self._payload)])
        if self._op == "update":
            updated = []
            for row in rows:
                if all(row.get(key) == value for key, value in self._filters.items()):
                    row.update(self._payload)
                    updated.append(dict(row))
            return _FakeExecute(updated)
        matched = [
            dict(row)
            for row in rows
            if all(row.get(key) == value for key, value in self._filters.items())
        ]
        if self._order_column:
            matched.sort(
                key=lambda row: str(row.get(self._order_column) or ""),
                reverse=self._order_desc,
            )
        return _FakeExecute(matched[: self._limit])


class _FakeClient:
    def __init__(self):
        self.store: dict[str, list[dict]] = {}
        self.table_calls: list[str] = []

    def table(self, name: str):
        self.table_calls.append(name)
        return _FakeQuery(self.store, name)


def test_project_repository_uses_only_conjecta_projects_table():
    assert PROJECTS_TABLE == "conjecta_projects"
    client = _FakeClient()
    repository = ProjectRepository(client=client)

    created = repository.upsert_project("u_a", "default", {"name": "A"})

    assert created["project"]["name"] == "A"
    assert set(client.table_calls) == {PROJECTS_TABLE}
    assert "projects" not in client.store


def test_project_repository_isolates_equal_project_ids_by_owner():
    client = _FakeClient()
    repository = ProjectRepository(client=client)
    repository.upsert_project("u_a", "default", {"name": "Tenant A"})
    repository.upsert_project("u_b", "default", {"name": "Tenant B"})

    project_a = repository.get_project("u_a", "default")
    project_b = repository.get_project("u_b", "default")
    repository.set_starred("u_a", "default", True)

    assert project_a is not None and project_a["project"]["name"] == "Tenant A"
    assert project_b is not None and project_b["project"]["name"] == "Tenant B"
    assert repository.get_project("u_a", "default")["starred"] is True
    assert repository.get_project("u_b", "default")["starred"] is False
    assert len(client.store[PROJECTS_TABLE]) == 2
