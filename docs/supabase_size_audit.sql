-- Where is the database size actually going?
--
-- Run this when the retention checks show tiny telemetry tables but a large
-- database_size. Retention only covers the three append-only telemetry tables;
-- anything else that grew has a different cause and needs a different fix.
--
-- IMPORTANT: this Supabase project is shared with other applications. Tables
-- like global_claim_pool, global_evidence_pool, ai4math_agent_runs, palm_*,
-- research_session_state, and flow_state are NOT Conjecta's and must not be
-- pruned, altered, or vacuumed on Conjecta's behalf. Conjecta owns exactly the
-- tables created by docs/supabase_*_schema.sql; BLOCK D below separates them.
-- Note that `users`, `sessions`, and `refresh_tokens` in auth.* belong to
-- Supabase itself — Conjecta's user table is `conjecta_users`.
--
-- Read-only. Run each block on its own — the Supabase SQL Editor renders only
-- the last statement in a batch.

-- ===========================================================================
-- BLOCK A — biggest relations, application tables first.
--
-- total_size includes indexes and TOAST (large values stored out-of-line,
-- e.g. 1536-dim embeddings on facts/intuitions/tricks).
-- ===========================================================================
select
    n.nspname || '.' || c.relname as relation,
    pg_size_pretty(pg_total_relation_size(c.oid)) as total_size,
    pg_size_pretty(pg_relation_size(c.oid)) as table_only,
    pg_size_pretty(pg_indexes_size(c.oid)) as indexes,
    pg_size_pretty(
        coalesce(pg_total_relation_size(c.reltoastrelid), 0)
    ) as toast,
    c.reltuples::bigint as approx_rows
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where c.relkind in ('r', 'm', 'p')
  and n.nspname not in ('pg_catalog', 'information_schema', 'pg_toast')
order by pg_total_relation_size(c.oid) desc
limit 25;

-- ===========================================================================
-- BLOCK B — per-schema totals.
--
-- On Supabase a large share of database_size is often NOT your data:
-- storage.objects, auth.*, realtime.*, and especially extensions like
-- pg_stat_statements or vector index bloat all land here. If public/ is small
-- and the total is large, retention is not the lever you need.
-- ===========================================================================
select
    n.nspname as schema_name,
    pg_size_pretty(sum(pg_total_relation_size(c.oid))) as total_size,
    count(*) as relations
from pg_class c
join pg_namespace n on n.oid = c.relnamespace
where c.relkind in ('r', 'm', 'p')
group by n.nspname
order by sum(pg_total_relation_size(c.oid)) desc;

-- ===========================================================================
-- BLOCK C — dead tuples awaiting VACUUM.
--
-- DELETE only marks rows dead; the space returns after autovacuum runs. A
-- large dead_rows count means the size shown above overstates live data.
-- ===========================================================================
select
    relname as table_name,
    n_live_tup as live_rows,
    n_dead_tup as dead_rows,
    last_autovacuum,
    last_autoanalyze
from pg_stat_user_tables
where n_dead_tup > 0
order by n_dead_tup desc
limit 20;

-- ===========================================================================
-- BLOCK D — Conjecta's own footprint, isolated from the co-tenant apps.
--
-- Use this rather than database_size when judging whether Conjecta needs a
-- retention or storage change: the plan quota is shared across every app in
-- this project, but only these rows are Conjecta's to act on.
-- ===========================================================================
with conjecta_tables(name) as (
    values
        ('conjecta_users'), ('conjecta_projects'), ('conjecta_solve_runs'),
        ('conjecta_llm_usage'), ('conjecta_usage_daily'),
        ('conjecta_usage_events'), ('conjecta_solve_feedback'),
        ('facts'), ('intuitions'), ('tricks'),
        ('knowledge_cards'), ('card_revisions'), ('card_comments'),
        ('card_reactions'), ('friendships'), ('project_members')
)
select
    t.name as table_name,
    case
        when to_regclass('public.' || t.name) is null then 'not created'
        else pg_size_pretty(pg_total_relation_size('public.' || t.name))
    end as total_size,
    coalesce(s.n_live_tup, 0) as live_rows,
    coalesce(s.n_dead_tup, 0) as dead_rows
from conjecta_tables t
left join pg_stat_user_tables s on s.relname = t.name
order by
    coalesce(pg_total_relation_size(to_regclass('public.' || t.name)), 0) desc;

-- Conjecta's total, for comparison against pg_database_size().
with conjecta_tables(name) as (
    values
        ('conjecta_users'), ('conjecta_projects'), ('conjecta_solve_runs'),
        ('conjecta_llm_usage'), ('conjecta_usage_daily'),
        ('conjecta_usage_events'), ('conjecta_solve_feedback'),
        ('facts'), ('intuitions'), ('tricks'),
        ('knowledge_cards'), ('card_revisions'), ('card_comments'),
        ('card_reactions'), ('friendships'), ('project_members')
)
select
    pg_size_pretty(
        sum(coalesce(pg_total_relation_size(to_regclass('public.' || name)), 0))
    ) as conjecta_total,
    pg_size_pretty(pg_database_size(current_database())) as database_total
from conjecta_tables;
