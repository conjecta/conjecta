from __future__ import annotations

import re
from pathlib import Path

from math_agent.knowledge.supabase import _FACT_FIELDS, _INTUITION_FIELDS, _TRICK_FIELDS


ROOT = Path(__file__).resolve().parents[1]
TENANT_SCHEMA = ROOT / "docs" / "supabase_tenant_schema.sql"
KNOWLEDGE_SCHEMA = ROOT / "docs" / "supabase_knowledge_schema.sql"


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def _columns_for(sql: str, table: str) -> set[str]:
    create = re.search(
        rf"create\s+table\s+if\s+not\s+exists\s+public\.{table}\s*\((.*?)\);",
        sql,
        flags=re.DOTALL,
    )
    assert create, f"missing idempotent create for {table}"
    columns = set(
        re.findall(r"^\s*([a-z_][a-z0-9_]*)\s+[a-z]", create.group(1), flags=re.MULTILINE)
    )
    columns.update(
        re.findall(
            rf"alter\s+table\s+public\.{table}\s+add\s+column\s+if\s+not\s+exists\s+([a-z_][a-z0-9_]*)",
            sql,
        )
    )
    return columns


def test_migrations_are_non_destructive_and_tenant_tables_are_conjecta_specific():
    tenant = _sql(TENANT_SCHEMA)
    knowledge = _sql(KNOWLEDGE_SCHEMA)

    assert not re.search(r"\bdrop\s+table\b", tenant + knowledge)
    assert "create table if not exists public.conjecta_users" in tenant
    assert "create table if not exists public.conjecta_projects" in tenant
    assert not re.search(r"public\.(users|projects)\b", tenant)


def test_every_cloud_knowledge_contract_field_exists_per_table():
    sql = _sql(KNOWLEDGE_SCHEMA)
    common = {"id", "project_id", "user_id", "created_at", "updated_at", "score", "embedding"}
    expected = {
        "facts": common | set(_FACT_FIELDS),
        "intuitions": common | set(_INTUITION_FIELDS),
        "tricks": common | set(_TRICK_FIELDS),
    }

    for table, fields in expected.items():
        missing = fields - _columns_for(sql, table)
        assert not missing, f"{table} schema is missing application fields: {sorted(missing)}"
        assert re.search(
            rf"alter\s+table\s+public\.{table}\s+add\s+column\s+if\s+not\s+exists\s+confidence\s+text\b",
            sql,
        )
        assert re.search(
            rf"alter\s+table\s+public\.{table}\s+add\s+column\s+if\s+not\s+exists\s+score\s+double\s+precision\b",
            sql,
        )


def test_legacy_status_is_candidate_before_candidate_default_and_not_null():
    sql = _sql(KNOWLEDGE_SCHEMA)

    for table in ("facts", "intuitions", "tricks"):
        add = re.search(
            rf"alter\s+table\s+public\.{table}\s+add\s+column\s+if\s+not\s+exists\s+status\s+text\s*;",
            sql,
        )
        backfill = re.search(
            rf"update\s+public\.{table}\s+set\s+status\s*=\s*'candidate'\s+where\s+status\s+is\s+null\s+or\s+btrim\(status\)\s*=\s*''\s*;",
            sql,
        )
        default = re.search(
            rf"alter\s+table\s+public\.{table}\s+alter\s+column\s+status\s+set\s+default\s+'candidate'\s*;",
            sql,
        )
        not_null = re.search(
            rf"alter\s+table\s+public\.{table}\s+alter\s+column\s+status\s+set\s+not\s+null\s*;",
            sql,
        )
        assert add and backfill and default and not_null
        assert add.start() < backfill.start() < default.start() < not_null.start()
        assert not re.search(
            rf"update\s+public\.{table}\s+set\s+status\s*=\s*'approved'",
            sql,
        )


def test_migrations_enable_rls_without_creating_permissive_anon_policies():
    sql = _sql(TENANT_SCHEMA) + "\n" + _sql(KNOWLEDGE_SCHEMA)

    for table in (
        "conjecta_users",
        "conjecta_projects",
        "facts",
        "intuitions",
        "tricks",
    ):
        assert f"alter table public.{table} enable row level security" in sql
        assert (
            f"revoke all privileges on table public.{table} from anon, authenticated"
            in sql
        )
        assert (
            f"grant select, insert, update, delete on table public.{table} to service_role"
            in sql
        )
    assert not re.search(r"\bcreate\s+policy\b", sql)
    assert not re.search(r"\bfor\s+all\s+to\s+anon\b", sql)
    assert not re.search(r"\busing\s*\(\s*true\s*\)", sql)
    dropped_policies = set(re.findall(r'drop\s+policy\s+if\s+exists\s+"([^"]+)"', sql))
    assert dropped_policies == {
        "allow anon full access on facts",
        "allow anon full access on intuitions",
        "allow anon full access on tricks",
    }


def test_embedding_columns_and_vector_extension_are_idempotent():
    sql = _sql(KNOWLEDGE_SCHEMA)

    assert "create extension if not exists vector" in sql
    for table in ("facts", "intuitions", "tricks"):
        assert re.search(
            rf"alter\s+table\s+public\.{table}\s+add\s+column\s+if\s+not\s+exists\s+embedding\s+vector\(1536\)",
            sql,
        )
        assert re.search(
            rf"create\s+index\s+if\s+not\s+exists\s+idx_{table}_embedding\s+on\s+public\.{table}\s+using\s+ivfflat\s+\(embedding\s+vector_cosine_ops\)",
            sql,
        )


def test_embedding_search_function_exists():
    sql = _sql(KNOWLEDGE_SCHEMA)

    assert "create or replace function public.match_knowledge_embeddings(" in sql


def test_all_schema_create_and_add_operations_are_idempotent():
    sql = _sql(TENANT_SCHEMA) + "\n" + _sql(KNOWLEDGE_SCHEMA)

    assert not re.search(r"\bcreate\s+table\s+(?!if\s+not\s+exists)", sql)
    assert not re.search(r"\bcreate\s+index\s+(?!if\s+not\s+exists)", sql)
    assert not re.search(r"\bcreate\s+extension\s+(?!if\s+not\s+exists)", sql)
    assert not re.search(r"\badd\s+column\s+(?!if\s+not\s+exists)", sql)
