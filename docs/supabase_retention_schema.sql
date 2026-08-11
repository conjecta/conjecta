-- Conjecta telemetry retention. Run after supabase_operations_schema.sql and
-- supabase_billing_schema.sql.
--
-- Why: conjecta_llm_usage and conjecta_usage_events are append-only detail
-- tables written once per LLM call (~25 rows per solve). Nothing deletes from
-- them, so they grow without bound. Measured against production data at
-- ~392 bytes per row including indexes:
--
--     300 solves/day   ~7.5k rows/day    ~2.8 MB/day    8 GB in ~8 years
--   3,000 solves/day    ~75k rows/day     ~28 MB/day    8 GB in ~10 months
--  30,000 solves/day   ~750k rows/day    ~281 MB/day    8 GB in ~1 month
--
-- At the upper end that fills a Pro plan's 8 GB disk in about a month, which
-- puts the database into read-only mode and takes the whole app down.
--
-- Scope note: this covers only the three telemetry tables. It is not a
-- database-size fix — see docs/supabase_size_audit.sql to find what is
-- actually consuming space before assuming retention is the lever.
--
-- The per-call rows are only used for recent debugging and admin dashboards.
-- Long-term accounting lives in conjecta_usage_daily, which is aggregated,
-- tiny, and deliberately kept forever.

begin;

-- ---------------------------------------------------------------------------
-- Retention window. Change here to adjust both tables at once.
-- ---------------------------------------------------------------------------
create or replace function public.conjecta_retention_days()
returns integer
language sql
immutable
as $$
    select 30;
$$;

-- ---------------------------------------------------------------------------
-- Delete in bounded batches so a large backlog cannot hold a long transaction
-- or bloat WAL. Returns the number of rows removed.
-- ---------------------------------------------------------------------------
create or replace function public.conjecta_prune_telemetry(
    p_batch_limit integer default 50000
)
returns bigint
language plpgsql
security definer
set search_path = public
as $$
declare
    cutoff timestamptz := now() - (public.conjecta_retention_days() || ' days')::interval;
    removed bigint := 0;
    batch bigint := 0;
begin
    with doomed as (
        select id
        from public.conjecta_llm_usage
        where created_at < cutoff
        order by created_at
        limit p_batch_limit
    )
    delete from public.conjecta_llm_usage u
    using doomed
    where u.id = doomed.id;
    get diagnostics batch = row_count;
    removed := removed + batch;

    with doomed as (
        select event_id
        from public.conjecta_usage_events
        where created_at < cutoff
        order by created_at
        limit p_batch_limit
    )
    delete from public.conjecta_usage_events e
    using doomed
    where e.event_id = doomed.event_id;
    get diagnostics batch = row_count;
    removed := removed + batch;

    -- Finished solve runs older than the window: the answer text already lives
    -- in the per-tenant project store, and unfinished runs are left alone so
    -- run recovery can still see them.
    --
    -- Must run after conjecta_llm_usage above: that table's session_id is a FK
    -- with ON DELETE SET NULL, so deleting runs first would blank the session
    -- link on any usage rows still inside the retention window.
    with doomed as (
        select id
        from public.conjecta_solve_runs
        where started_at < cutoff
          and status <> 'running'
        order by started_at
        limit p_batch_limit
    )
    delete from public.conjecta_solve_runs r
    using doomed
    where r.id = doomed.id;
    get diagnostics batch = row_count;
    removed := removed + batch;

    return removed;
end;
$$;

revoke all privileges on function public.conjecta_prune_telemetry(integer) from public, anon, authenticated;
grant execute on function public.conjecta_prune_telemetry(integer) to service_role;

-- ---------------------------------------------------------------------------
-- Schedule. pg_cron is available on Supabase but must be enabled explicitly;
-- if the extension is missing, the job is skipped and the function can still
-- be invoked manually (or from a deploy hook).
-- ---------------------------------------------------------------------------
do $$
begin
    if exists (select 1 from pg_available_extensions where name = 'pg_cron') then
        create extension if not exists pg_cron;

        if exists (select 1 from cron.job where jobname = 'conjecta-prune-telemetry') then
            perform cron.unschedule('conjecta-prune-telemetry');
        end if;

        -- 03:17 UTC: off the hour so it does not contend with other jobs.
        perform cron.schedule(
            'conjecta-prune-telemetry',
            '17 3 * * *',
            $cron$select public.conjecta_prune_telemetry();$cron$
        );
    else
        raise notice
            'pg_cron unavailable; call public.conjecta_prune_telemetry() from an external scheduler';
    end if;
end;
$$;

commit;

-- ---------------------------------------------------------------------------
-- One-off backfill for an existing deployment. Safe to re-run: it deletes at
-- most p_batch_limit rows per table per call, so repeat until it returns 0.
--
--   select public.conjecta_prune_telemetry();
-- ---------------------------------------------------------------------------
