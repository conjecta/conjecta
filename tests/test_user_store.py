from __future__ import annotations

from math_agent.web.jwt_auth import user_id_for_phone
from math_agent.web.user_store import USERS_TABLE, UserStore


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
        self._limit = 100
        self._offset = 0
        self._order_desc = False
        self._selected_fields: set[str] | None = None

    def select(self, *args, **_kwargs):
        self._op = "select"
        if args and args[0] != "*":
            self._selected_fields = {
                field.strip() for field in str(args[0]).split(",") if field.strip()
            }
        return self

    def insert(self, row):
        self._op = "insert"
        self._payload = row
        return self

    def update(self, row):
        self._op = "update"
        self._payload = row
        return self

    def eq(self, key, value):
        self._filters[key] = value
        return self

    def order(self, *_args, **_kwargs):
        self._order_desc = bool(_kwargs.get("desc"))
        return self

    def limit(self, n):
        self._limit = n
        return self

    def offset(self, n):
        self._offset = n
        return self

    def execute(self):
        rows = self.store.setdefault(self.table, [])
        if self._op == "insert":
            rows.append(dict(self._payload))
            return _FakeExecute([dict(self._payload)])
        if self._op == "update":
            updated = []
            for row in rows:
                if all(row.get(k) == v for k, v in self._filters.items()):
                    row.update(self._payload)
                    updated.append(dict(row))
            return _FakeExecute(updated)
        # select
        matched = [dict(r) for r in rows if all(r.get(k) == v for k, v in self._filters.items())]
        if self._order_desc and matched and "last_login_at" in matched[0]:
            matched.sort(key=lambda r: r.get("last_login_at") or "", reverse=True)
        sliced = matched[self._offset : self._offset + self._limit]
        if self._selected_fields is not None:
            sliced = [
                {key: value for key, value in row.items() if key in self._selected_fields}
                for row in sliced
            ]
        return _FakeExecute(sliced)


class _FakeClient:
    def __init__(self):
        self._store: dict[str, list] = {}

    def table(self, name: str):
        return _FakeQuery(self._store, name)


def test_upsert_login_inserts_then_updates_last_login():
    assert USERS_TABLE == "conjecta_users"
    client = _FakeClient()
    store = UserStore(client=client)
    phone = "13812345678"
    first = store.upsert_login(phone)
    assert first["id"] == user_id_for_phone(phone)
    assert first["phone"] == phone
    assert first["phone_masked"] == "138****5678"
    assert first["created_at"]
    created = first["created_at"]
    second = store.upsert_login(phone)
    assert second["id"] == first["id"]
    assert len(client._store[USERS_TABLE]) == 1
    assert "users" not in client._store
    assert second.get("created_at") == created


def test_list_users_returns_phone_and_masked_fields():
    client = _FakeClient()
    store = UserStore(client=client)
    store.upsert_login("13812345678")
    store.upsert_login("13900001111")
    users = store.list_users()
    assert set(client._store) == {USERS_TABLE}
    assert len(users) == 2
    by_phone = {u["phone"]: u for u in users}
    assert set(by_phone) == {"13812345678", "13900001111"}
    assert by_phone["13812345678"]["phone_masked"] == "138****5678"
    assert by_phone["13900001111"]["phone_masked"] == "139****1111"
    for u in users:
        assert "id" in u
        assert "phone" in u
        assert "phone_masked" in u
