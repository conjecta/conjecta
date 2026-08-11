-- Friends, friend-visible knowledge cards, and collaborative project membership.
-- Run after docs/supabase_tenant_schema.sql and docs/supabase_knowledge_cards_schema.sql.
-- Every statement is safe to run again.

begin;

-- ---------------------------------------------------------------------------
-- User profile display name (for friend lists and import provenance labels)
-- ---------------------------------------------------------------------------
alter table public.conjecta_users
    add column if not exists display_name text;

-- ---------------------------------------------------------------------------
-- Friendships (directed request; accepted = mutual friends)
-- ---------------------------------------------------------------------------
create table if not exists public.friendships (
    id uuid primary key default gen_random_uuid(),
    requester_id text not null references public.conjecta_users (id) on delete cascade,
    addressee_id text not null references public.conjecta_users (id) on delete cascade,
    status text not null default 'pending'
        check (status in ('pending', 'accepted', 'declined', 'blocked')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    check (requester_id <> addressee_id)
);

create unique index if not exists idx_friendships_pair
    on public.friendships (
        least(requester_id, addressee_id),
        greatest(requester_id, addressee_id)
    );

create index if not exists idx_friendships_requester
    on public.friendships (requester_id, status);

create index if not exists idx_friendships_addressee
    on public.friendships (addressee_id, status);

-- ---------------------------------------------------------------------------
-- Project members (collaborative projects; solo projects need no rows)
-- Knowledge for a project stays under owner_user_id (the lead).
-- ---------------------------------------------------------------------------
create table if not exists public.project_members (
    owner_user_id text not null,
    project_id text not null,
    user_id text not null references public.conjecta_users (id) on delete cascade,
    role text not null check (role in ('lead', 'collaborator')),
    added_by text,
    created_at timestamptz not null default now(),
    primary key (owner_user_id, project_id, user_id),
    foreign key (owner_user_id, project_id)
        references public.conjecta_projects (owner_user_id, id) on delete cascade
);

create index if not exists idx_project_members_user
    on public.project_members (user_id);

create index if not exists idx_project_members_project
    on public.project_members (owner_user_id, project_id);

-- ---------------------------------------------------------------------------
-- knowledge_cards.visibility may be private | friends | public | team
-- (team remains reserved; no DB check constraint historically)
-- ---------------------------------------------------------------------------
create index if not exists idx_knowledge_cards_visibility_status
    on public.knowledge_cards (visibility, status);

-- ---------------------------------------------------------------------------
-- Server-only access
-- ---------------------------------------------------------------------------
alter table public.friendships enable row level security;
alter table public.project_members enable row level security;

revoke all privileges on table public.friendships from anon, authenticated;
revoke all privileges on table public.project_members from anon, authenticated;

grant select, insert, update, delete on table public.friendships to service_role;
grant select, insert, update, delete on table public.project_members to service_role;

commit;
