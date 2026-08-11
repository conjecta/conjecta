-- Conjecta cloud knowledge schema.
-- This migration supports both fresh databases and legacy tables already in use.

begin;

-- ---------------------------------------------------------------------------
-- Base tables. Legacy installations already have these columns.
-- ---------------------------------------------------------------------------
create table if not exists public.facts (
    id uuid primary key default gen_random_uuid(),
    project_id text not null default 'default',
    statement text not null,
    why text default '',
    source text default '',
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create table if not exists public.intuitions (
    id uuid primary key default gen_random_uuid(),
    project_id text not null default 'default',
    title text not null,
    body text default '',
    kind text default '',
    source text default '',
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create table if not exists public.tricks (
    id uuid primary key default gen_random_uuid(),
    project_id text not null default 'default',
    title text not null,
    body text default '',
    category text default '',
    source text default '',
    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

-- ---------------------------------------------------------------------------
-- Facts: tenant, formalization, provenance, review lifecycle, and score.
-- ---------------------------------------------------------------------------
alter table public.facts add column if not exists user_id text;
alter table public.facts add column if not exists formal_status text default '';
alter table public.facts add column if not exists lean_name text default '';
alter table public.facts add column if not exists source_type text default '';
alter table public.facts add column if not exists source_ref text default '';
alter table public.facts add column if not exists source_title text default '';
alter table public.facts add column if not exists evidence text default '';
alter table public.facts add column if not exists confidence text default '';
alter table public.facts add column if not exists status text;
alter table public.facts add column if not exists domain text default '';
alter table public.facts add column if not exists tags text default '';
alter table public.facts add column if not exists created_by text default '';
alter table public.facts add column if not exists review_note text default '';
alter table public.facts add column if not exists score double precision;
alter table public.facts add column if not exists statement_zh text default '';
alter table public.facts add column if not exists why_zh text default '';

-- Legacy rows have not passed the current review lifecycle. Keep them
-- untrusted until they are explicitly reviewed, just like newly inserted rows.
update public.facts
set status = 'candidate'
where status is null or btrim(status) = '';
alter table public.facts alter column status set default 'candidate';
alter table public.facts alter column status set not null;

-- ---------------------------------------------------------------------------
-- Intuitions: tenant, provenance, review lifecycle, and score.
-- ---------------------------------------------------------------------------
alter table public.intuitions add column if not exists user_id text;
alter table public.intuitions add column if not exists source_type text default '';
alter table public.intuitions add column if not exists source_ref text default '';
alter table public.intuitions add column if not exists source_title text default '';
alter table public.intuitions add column if not exists evidence text default '';
alter table public.intuitions add column if not exists confidence text default '';
alter table public.intuitions add column if not exists status text;
alter table public.intuitions add column if not exists domain text default '';
alter table public.intuitions add column if not exists tags text default '';
alter table public.intuitions add column if not exists created_by text default '';
alter table public.intuitions add column if not exists review_note text default '';
alter table public.intuitions add column if not exists score double precision;
alter table public.intuitions add column if not exists title_zh text default '';
alter table public.intuitions add column if not exists body_zh text default '';

update public.intuitions
set status = 'candidate'
where status is null or btrim(status) = '';
alter table public.intuitions alter column status set default 'candidate';
alter table public.intuitions alter column status set not null;

-- ---------------------------------------------------------------------------
-- Tricks: tenant, applicability/failure guidance, provenance, and lifecycle.
-- ---------------------------------------------------------------------------
alter table public.tricks add column if not exists user_id text;
alter table public.tricks add column if not exists applicability text default '';
alter table public.tricks add column if not exists failure_mode text default '';
alter table public.tricks add column if not exists source_type text default '';
alter table public.tricks add column if not exists source_ref text default '';
alter table public.tricks add column if not exists source_title text default '';
alter table public.tricks add column if not exists evidence text default '';
alter table public.tricks add column if not exists confidence text default '';
alter table public.tricks add column if not exists status text;
alter table public.tricks add column if not exists domain text default '';
alter table public.tricks add column if not exists tags text default '';
alter table public.tricks add column if not exists created_by text default '';
alter table public.tricks add column if not exists review_note text default '';
alter table public.tricks add column if not exists score double precision;
alter table public.tricks add column if not exists title_zh text default '';
alter table public.tricks add column if not exists body_zh text default '';

update public.tricks
set status = 'candidate'
where status is null or btrim(status) = '';
alter table public.tricks alter column status set default 'candidate';
alter table public.tricks alter column status set not null;

-- ---------------------------------------------------------------------------
-- Idempotent indexes used by tenant/project listing and trusted search.
-- ---------------------------------------------------------------------------
create index if not exists idx_facts_project_id
    on public.facts (project_id);
create index if not exists idx_intuitions_project_id
    on public.intuitions (project_id);
create index if not exists idx_tricks_project_id
    on public.tricks (project_id);

create index if not exists idx_facts_user_project
    on public.facts (user_id, project_id);
create index if not exists idx_intuitions_user_project
    on public.intuitions (user_id, project_id);
create index if not exists idx_tricks_user_project
    on public.tricks (user_id, project_id);

create index if not exists idx_facts_user_project_status
    on public.facts (user_id, project_id, status);
create index if not exists idx_intuitions_user_project_status
    on public.intuitions (user_id, project_id, status);
create index if not exists idx_tricks_user_project_status
    on public.tricks (user_id, project_id, status);

-- ---------------------------------------------------------------------------
-- Optional semantic search: pgvector extension + embedding column.
-- Requires pgvector to be installed; skips creation if the extension already exists.
-- ---------------------------------------------------------------------------
create extension if not exists vector;

alter table public.facts add column if not exists embedding vector(1536);
alter table public.intuitions add column if not exists embedding vector(1536);
alter table public.tricks add column if not exists embedding vector(1536);

create index if not exists idx_facts_embedding
    on public.facts using ivfflat (embedding vector_cosine_ops);
create index if not exists idx_intuitions_embedding
    on public.intuitions using ivfflat (embedding vector_cosine_ops);
create index if not exists idx_tricks_embedding
    on public.tricks using ivfflat (embedding vector_cosine_ops);

-- ---------------------------------------------------------------------------
-- Semantic search helper. Returns matching rows ordered by cosine distance.
-- ---------------------------------------------------------------------------
create or replace function public.match_knowledge_embeddings(
    p_table text,
    p_query_embedding vector(1536),
    p_project_id text,
    p_user_id text default null,
    p_statuses text[] default array['approved', 'reviewed', 'verified'],
    p_limit int default 20
)
returns jsonb
language plpgsql
as $$
declare
    sql_query text;
    result jsonb;
begin
    if p_table not in ('facts', 'intuitions', 'tricks') then
        raise exception 'invalid table: %', p_table;
    end if;

    sql_query := format(
        'select coalesce(jsonb_agg(to_jsonb(t.*) - ''embedding''), ''[]''::jsonb) from (
            select * from public.%I
            where project_id = $1
              and status = any($2)
              and ($3 is null or user_id = $3)
            order by embedding <=> $4
            limit $5
        ) t',
        p_table
    );
    execute sql_query into result using p_project_id, p_statuses, p_user_id, p_query_embedding, p_limit;
    return result;
end;
$$;

-- ---------------------------------------------------------------------------
-- Server-only access. Existing legacy anon policies are removed by name.
-- ---------------------------------------------------------------------------
alter table public.facts enable row level security;
alter table public.intuitions enable row level security;
alter table public.tricks enable row level security;

drop policy if exists "Allow anon full access on facts" on public.facts;
drop policy if exists "Allow anon full access on intuitions" on public.intuitions;
drop policy if exists "Allow anon full access on tricks" on public.tricks;

revoke all privileges on table public.facts from anon, authenticated;
revoke all privileges on table public.intuitions from anon, authenticated;
revoke all privileges on table public.tricks from anon, authenticated;

grant select, insert, update, delete on table public.facts to service_role;
grant select, insert, update, delete on table public.intuitions to service_role;
grant select, insert, update, delete on table public.tricks to service_role;

commit;
