from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RETENTION_SCHEMA = ROOT / "docs" / "supabase_retention_schema.sql"
OPERATIONS_SCHEMA = ROOT / "docs" / "supabase_operations_schema.sql"
BILLING_SCHEMA = ROOT / "docs" / "supabase_billing_schema.sql"


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def test_retention_prunes_every_append_only_detail_table():
    """The unbounded tables are the per-call detail ones; without a retention
    job they fill the plan's disk and put the database into read-only mode."""
    sql = _sql(RETENTION_SCHEMA)
    for table in (
        "conjecta_llm_usage",
        "conjecta_usage_events",
        "conjecta_solve_runs",
    ):
        assert f"from public.{table}" in sql, f"{table} is never pruned"


def test_retention_never_touches_the_aggregate_table():
    """conjecta_usage_daily is the long-term accounting record: it is small,
    aggregated, and must survive pruning."""
    sql = _sql(RETENTION_SCHEMA)
    assert "from public.conjecta_usage_daily" not in sql
    assert "delete from public.conjecta_usage_daily" not in sql


def test_runs_are_pruned_after_usage_rows():
    """conjecta_llm_usage.session_id references conjecta_solve_runs with
    ON DELETE SET NULL, so deleting runs first would blank the session link on
    usage rows still inside the retention window."""
    assert (
        "on delete set null" in _sql(OPERATIONS_SCHEMA)
    ), "FK behavior changed; revisit the prune ordering"
    sql = _sql(RETENTION_SCHEMA)
    assert sql.index("from public.conjecta_llm_usage") < sql.index(
        "from public.conjecta_solve_runs"
    )


def test_running_solves_are_never_pruned():
    """Run recovery resumes rows still marked running; pruning them would
    strand in-flight work after a restart."""
    sql = _sql(RETENTION_SCHEMA)
    assert "status <> 'running'" in sql


def test_deletes_are_batched():
    """An unbounded delete on a large backlog holds a long transaction and
    bloats WAL."""
    sql = _sql(RETENTION_SCHEMA)
    assert "p_batch_limit" in sql
    assert sql.count("limit p_batch_limit") == 3


def test_prune_function_is_server_only():
    sql = _sql(RETENTION_SCHEMA)
    assert "revoke all privileges on function public.conjecta_prune_telemetry" in sql
    assert "grant execute on function public.conjecta_prune_telemetry(integer) to service_role" in sql


def test_schedule_degrades_when_pg_cron_is_unavailable():
    """Supabase exposes pg_cron but it is not enabled by default; a missing
    extension must not fail the migration."""
    sql = _sql(RETENTION_SCHEMA)
    assert "pg_available_extensions" in sql
    assert "raise notice" in sql


def test_migration_is_idempotent():
    sql = _sql(RETENTION_SCHEMA)
    assert "create or replace function public.conjecta_prune_telemetry" in sql
    assert "create extension if not exists pg_cron" in sql
    # Re-running must not stack duplicate cron jobs.
    assert "cron.unschedule" in sql


def test_pruning_is_confined_to_conjecta_owned_tables():
    """The Supabase project is shared with other applications (global_*_pool,
    ai4math_*, palm_*, research_session_state, ...). A retention job that
    reached beyond conjecta_* would delete another team's data."""
    sql = _sql(RETENTION_SCHEMA)
    touched = set(
        re.findall(r"(?:from|delete\s+from|join|update)\s+public\.([a-z_]+)", sql)
    )
    foreign = {name for name in touched if not name.startswith("conjecta_")}
    assert not foreign, f"retention reaches outside Conjecta's tables: {sorted(foreign)}"


def test_pruned_columns_match_the_real_primary_keys():
    """A renamed key would make the delete silently match nothing."""
    operations = _sql(OPERATIONS_SCHEMA)
    billing = _sql(BILLING_SCHEMA)
    assert re.search(r"id\s+bigint\s+generated\s+always\s+as\s+identity\s+primary\s+key", operations)
    assert re.search(r"event_id\s+uuid\s+primary\s+key", billing)
    sql = _sql(RETENTION_SCHEMA)
    assert "where u.id = doomed.id" in sql
    assert "where e.event_id = doomed.event_id" in sql
    assert "where r.id = doomed.id" in sql
