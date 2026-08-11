-- Solve user feedback for the admin operations dashboard.
-- Run after docs/supabase_operations_schema.sql. Idempotent.

begin;

create table if not exists public.conjecta_solve_feedback (
    id uuid primary key default gen_random_uuid(),
    user_id text not null,
    session_id text references public.conjecta_solve_runs (id) on delete set null,
    rating text not null check (rating in ('satisfied', 'unsatisfied')),
    comment text not null default '',
    outcome text not null check (outcome in ('completed', 'failed')),
    problem_preview text not null default '',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create unique index if not exists idx_conjecta_solve_feedback_user_session
    on public.conjecta_solve_feedback (user_id, session_id)
    where session_id is not null;

create index if not exists idx_conjecta_solve_feedback_created
    on public.conjecta_solve_feedback (created_at desc);

alter table public.conjecta_solve_feedback enable row level security;

revoke all privileges on table public.conjecta_solve_feedback from anon, authenticated;
grant select, insert, update, delete on table public.conjecta_solve_feedback to service_role;

commit;
