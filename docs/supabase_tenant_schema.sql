-- Conjecta tenant isolation schema (phone users, owned projects, and knowledge tenants).
-- Run after docs/supabase_knowledge_schema.sql. Every statement is safe to run again.

begin;

-- ---------------------------------------------------------------------------
-- Conjecta users (kept separate from tables owned by other applications)
-- ---------------------------------------------------------------------------
create table if not exists public.conjecta_users (
    id text primary key,
    phone text not null unique,
    phone_masked text not null,
    created_at timestamptz not null default now(),
    last_login_at timestamptz not null default now()
);

create index if not exists idx_conjecta_users_last_login_at
    on public.conjecta_users (last_login_at desc);

-- ---------------------------------------------------------------------------
-- Conjecta projects (the same project id may be used by different tenants)
-- ---------------------------------------------------------------------------
create table if not exists public.conjecta_projects (
    owner_user_id text not null references public.conjecta_users (id) on delete cascade,
    id text not null,
    name text not null,
    starred boolean not null default false,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    primary key (owner_user_id, id)
);

create index if not exists idx_conjecta_projects_owner
    on public.conjecta_projects (owner_user_id);

-- ---------------------------------------------------------------------------
-- Knowledge tenant keys (also present in the standalone knowledge migration)
-- ---------------------------------------------------------------------------
alter table public.facts add column if not exists user_id text;
alter table public.intuitions add column if not exists user_id text;
alter table public.tricks add column if not exists user_id text;

create index if not exists idx_facts_user_project
    on public.facts (user_id, project_id);
create index if not exists idx_intuitions_user_project
    on public.intuitions (user_id, project_id);
create index if not exists idx_tricks_user_project
    on public.tricks (user_id, project_id);

-- ---------------------------------------------------------------------------
-- Server-only access: service_role retains access and bypasses RLS. Direct
-- anon/authenticated access has no grants or policies.
-- ---------------------------------------------------------------------------
alter table public.conjecta_users enable row level security;
alter table public.conjecta_projects enable row level security;
alter table public.facts enable row level security;
alter table public.intuitions enable row level security;
alter table public.tricks enable row level security;

-- Remove only the three known permissive policies shipped by the legacy schema.
drop policy if exists "Allow anon full access on facts" on public.facts;
drop policy if exists "Allow anon full access on intuitions" on public.intuitions;
drop policy if exists "Allow anon full access on tricks" on public.tricks;

revoke all privileges on table public.conjecta_users from anon, authenticated;
revoke all privileges on table public.conjecta_projects from anon, authenticated;
revoke all privileges on table public.facts from anon, authenticated;
revoke all privileges on table public.intuitions from anon, authenticated;
revoke all privileges on table public.tricks from anon, authenticated;

grant select, insert, update, delete on table public.conjecta_users to service_role;
grant select, insert, update, delete on table public.conjecta_projects to service_role;
grant select, insert, update, delete on table public.facts to service_role;
grant select, insert, update, delete on table public.intuitions to service_role;
grant select, insert, update, delete on table public.tricks to service_role;

commit;
