-- Read-only verification for docs/supabase_retention_schema.sql.
-- Nothing here writes or deletes; every statement is a SELECT.
--
-- NOTE: the Supabase SQL Editor only renders the result of the LAST statement
-- in a batch. Run the two blocks below separately (highlight one and press
-- Run), or you will only ever see the second one's output.

-- ===========================================================================
-- BLOCK A — run this on its own.
--
-- Kept separate because it reads cron.job, which does not exist when pg_cron
-- was unavailable at migration time. In that case this block errors with
-- "schema cron does not exist" — that error IS the finding: the nightly job
-- was never registered and conjecta_prune_telemetry() must be driven by an
-- external scheduler instead.
--
-- Zero rows with no error means pg_cron exists but the job is not registered.
-- One row with active = true is the healthy result.
-- ===========================================================================
select jobid, schedule, active, command
from cron.job
where jobname = 'conjecta-prune-telemetry';

-- ===========================================================================
-- BLOCK B — run this on its own. One statement, so the editor shows it all.
-- ===========================================================================
with cutoff as (
    select now() - (public.conjecta_retention_days() || ' days')::interval as ts
)
select
    1 as ord,
    'retention_days' as check_name,
    public.conjecta_retention_days()::text as value,
    '' as detail
union all
select
    2,
    'prune_function_installed',
    (to_regprocedure('public.conjecta_prune_telemetry(integer)') is not null)::text,
    'false means the migration did not apply'
union all
select
    3,
    'database_size',
    pg_size_pretty(pg_database_size(current_database())),
    'Free plan 500MB / Pro plan 8GB disk'
union all
select
    4,
    'conjecta_llm_usage',
    (select count(*) filter (where created_at < (select ts from cutoff))
     from public.conjecta_llm_usage)::text,
    'prunable of ' || (select count(*) from public.conjecta_llm_usage)::text
        || ' rows, ' || pg_size_pretty(pg_total_relation_size('public.conjecta_llm_usage'))
union all
select
    5,
    'conjecta_usage_events',
    (select count(*) filter (where created_at < (select ts from cutoff))
     from public.conjecta_usage_events)::text,
    'prunable of ' || (select count(*) from public.conjecta_usage_events)::text
        || ' rows, ' || pg_size_pretty(pg_total_relation_size('public.conjecta_usage_events'))
union all
select
    6,
    'conjecta_solve_runs',
    (select count(*) filter (
        where started_at < (select ts from cutoff) and status <> 'running')
     from public.conjecta_solve_runs)::text,
    'prunable of ' || (select count(*) from public.conjecta_solve_runs)::text
        || ' rows, ' || pg_size_pretty(pg_total_relation_size('public.conjecta_solve_runs'))
union all
-- Never pruned; listed so you can confirm it is not being touched.
select
    7,
    'conjecta_usage_daily (kept forever)',
    '0',
    (select count(*) from public.conjecta_usage_daily)::text || ' rows retained'
union all
-- Left alone deliberately so run recovery can still resume them. A large or
-- old count here is a run-recovery problem, not a retention one.
select
    8,
    'solve_runs still running',
    (select count(*) from public.conjecta_solve_runs where status = 'running')::text,
    coalesce(
        (select 'oldest ' || min(started_at)::text
         from public.conjecta_solve_runs where status = 'running'),
        'none stuck'
    )
order by ord;

-- ===========================================================================
-- If BLOCK B shows a prunable count above p_batch_limit (50k), call the prune
-- repeatedly until it returns 0 rather than waiting for the nightly job to
-- catch up one batch per day:
--
--   select public.conjecta_prune_telemetry();
--
-- DELETE only marks tuples dead; run VACUUM afterwards to actually return the
-- space if the backlog was large:
--
--   vacuum (analyze) public.conjecta_llm_usage;
-- ===========================================================================
