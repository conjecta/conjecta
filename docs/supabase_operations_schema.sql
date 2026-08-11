-- Conjecta operations telemetry. Run after supabase_tenant_schema.sql.
-- Tables are server-only: the backend service role is the sole reader/writer.

begin;

create table if not exists public.conjecta_solve_runs (
    id text primary key,
    user_id text not null references public.conjecta_users (id) on delete cascade,
    project_id text not null default 'default',
    problem text not null default '',
    mode text not null default 'auto',
    model text not null default '',
    status text not null default 'running',
    started_at timestamptz not null default now(),
    finished_at timestamptz
);

create index if not exists idx_conjecta_solve_runs_started
    on public.conjecta_solve_runs (started_at desc);
create index if not exists idx_conjecta_solve_runs_user_started
    on public.conjecta_solve_runs (user_id, started_at desc);

create table if not exists public.conjecta_llm_usage (
    id bigint generated always as identity primary key,
    user_id text references public.conjecta_users (id) on delete cascade,
    session_id text references public.conjecta_solve_runs (id) on delete set null,
    operation text not null default 'solve',
    provider text not null,
    model text not null,
    input_tokens bigint not null default 0,
    output_tokens bigint not null default 0,
    total_tokens bigint not null default 0,
    cached_tokens bigint not null default 0,
    reasoning_tokens bigint not null default 0,
    created_at timestamptz not null default now()
);

create index if not exists idx_conjecta_llm_usage_created
    on public.conjecta_llm_usage (created_at desc);
create index if not exists idx_conjecta_llm_usage_user_created
    on public.conjecta_llm_usage (user_id, created_at desc);
create index if not exists idx_conjecta_llm_usage_session
    on public.conjecta_llm_usage (session_id);

alter table public.conjecta_solve_runs enable row level security;
alter table public.conjecta_llm_usage enable row level security;

revoke all privileges on table public.conjecta_solve_runs from anon, authenticated;
revoke all privileges on table public.conjecta_llm_usage from anon, authenticated;
revoke all privileges on sequence public.conjecta_llm_usage_id_seq from anon, authenticated;

grant select, insert, update, delete on table public.conjecta_solve_runs to service_role;
grant select, insert, update, delete on table public.conjecta_llm_usage to service_role;
grant usage, select on sequence public.conjecta_llm_usage_id_seq to service_role;

commit;
