-- Conjecta knowledge cards schema.
-- Run after docs/supabase_tenant_schema.sql.

begin;

create table if not exists public.knowledge_cards (
    id uuid primary key default gen_random_uuid(),
    owner_user_id text not null references public.conjecta_users(id) on delete cascade,
    project_id text not null,
    source_item_id text not null,
    source_item_kind text not null,
    latest_revision_id uuid,
    visibility text not null default 'private',
    status text not null default 'draft',
    citation_count int not null default 0,
    star_count int not null default 0,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.card_revisions (
    id uuid primary key default gen_random_uuid(),
    card_id uuid not null references public.knowledge_cards(id) on delete cascade,
    revision_number int not null,
    title text not null,
    statement text not null,
    body text default '',
    formal_status text default '',
    lean_name text default '',
    lean_code text default '',
    evidence_id text default '',
    source_run_session_id text default '',
    source_run_share_token text default '',
    tags text[] default '{}',
    domain text default '',
    metadata jsonb default '{}',
    created_at timestamptz not null default now(),
    unique(card_id, revision_number)
);

create table if not exists public.card_reactions (
    card_id uuid not null references public.knowledge_cards(id) on delete cascade,
    user_id text not null,
    kind text not null,
    created_at timestamptz not null default now(),
    primary key (card_id, user_id, kind)
);

create table if not exists public.card_comments (
    id uuid primary key default gen_random_uuid(),
    card_id uuid not null references public.knowledge_cards(id) on delete cascade,
    parent_comment_id uuid references public.card_comments(id) on delete cascade,
    author_user_id text not null,
    body text not null,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create index if not exists idx_knowledge_cards_owner on public.knowledge_cards (owner_user_id);
create index if not exists idx_knowledge_cards_visibility on public.knowledge_cards (visibility);
create index if not exists idx_knowledge_cards_status on public.knowledge_cards (status);
create index if not exists idx_card_revisions_card on public.card_revisions (card_id);
create index if not exists idx_card_comments_card on public.card_comments (card_id);

alter table public.knowledge_cards enable row level security;
alter table public.card_revisions enable row level security;
alter table public.card_reactions enable row level security;
alter table public.card_comments enable row level security;

revoke all privileges on table public.knowledge_cards from anon, authenticated;
revoke all privileges on table public.card_revisions from anon, authenticated;
revoke all privileges on table public.card_reactions from anon, authenticated;
revoke all privileges on table public.card_comments from anon, authenticated;

grant select, insert, update, delete on table public.knowledge_cards to service_role;
grant select, insert, update, delete on table public.card_revisions to service_role;
grant select, insert, update, delete on table public.card_reactions to service_role;
grant select, insert, update, delete on table public.card_comments to service_role;

commit;
