-- Conjecta billing usage schema.
-- Run after docs/supabase_tenant_schema.sql so public.conjecta_users exists.
-- Every statement is safe to run again.

begin;

-- ---------------------------------------------------------------------------
-- Encrypted API key storage on existing user rows.
-- ---------------------------------------------------------------------------
alter table public.conjecta_users
    add column if not exists api_keys_encrypted text,
    add column if not exists api_keys_updated_at timestamptz;

-- ---------------------------------------------------------------------------
-- Daily per-user usage aggregates.
-- ---------------------------------------------------------------------------
create table if not exists public.conjecta_usage_daily (
    user_id text not null references public.conjecta_users(id) on delete cascade,
    date date not null,
    prompt_tokens bigint not null default 0,
    completion_tokens bigint not null default 0,
    total_tokens bigint not null default 0,
    estimated_cost_usd numeric(18, 12) not null default 0,
    updated_at timestamptz not null default now(),
    primary key (user_id, date)
);

create index if not exists idx_conjecta_usage_daily_date
    on public.conjecta_usage_daily (date desc);

-- ---------------------------------------------------------------------------
-- Per-LLM-call usage events.
-- ---------------------------------------------------------------------------
create table if not exists public.conjecta_usage_events (
    event_id uuid primary key default gen_random_uuid(),
    user_id text not null references public.conjecta_users(id) on delete cascade,
    prompt_tokens bigint not null default 0,
    completion_tokens bigint not null default 0,
    total_tokens bigint not null default 0,
    cost_usd numeric(18, 12) not null default 0,
    provider text,
    model text,
    source text not null default 'platform',
    created_at timestamptz not null default now()
);

create index if not exists idx_conjecta_usage_events_user_created
    on public.conjecta_usage_events (user_id, created_at desc);

-- ---------------------------------------------------------------------------
-- Atomic usage recording: increment daily counters and append an event.
-- ---------------------------------------------------------------------------
create or replace function public.increment_usage(
    p_user_id text,
    p_prompt_tokens bigint,
    p_completion_tokens bigint,
    p_cost_usd numeric(18, 12),
    p_provider text,
    p_model text,
    p_source text default 'platform'
)
returns void
language plpgsql
as $$
declare
    -- Derive the billing date from the event timestamp so the event row and the
    -- daily aggregate always align.
    v_event_time timestamptz := now();
    v_billing_date date := (v_event_time at time zone 'UTC')::date;
    v_prompt_tokens bigint := coalesce(p_prompt_tokens, 0);
    v_completion_tokens bigint := coalesce(p_completion_tokens, 0);
    -- Total tokens are computed as prompt + completion so the recorded total
    -- always matches the pricing model, regardless of any provider-reported total.
    v_total_tokens bigint := v_prompt_tokens + v_completion_tokens;
    v_cost_usd numeric(18, 12) := coalesce(p_cost_usd, 0);
    v_source text := coalesce(p_source, 'platform');
begin
    insert into public.conjecta_usage_events (
        user_id, prompt_tokens, completion_tokens, total_tokens,
        cost_usd, provider, model, source, created_at
    ) values (
        p_user_id, v_prompt_tokens, v_completion_tokens, v_total_tokens,
        v_cost_usd, p_provider, p_model, v_source, v_event_time
    );

    -- Only platform-key usage counts toward the free quota and daily aggregates.
    -- User-key usage is recorded as an event but excluded from billing counters.
    if v_source = 'platform' then
        insert into public.conjecta_usage_daily (
            user_id, date, prompt_tokens, completion_tokens, total_tokens,
            estimated_cost_usd, updated_at
        ) values (
            p_user_id, v_billing_date, v_prompt_tokens, v_completion_tokens, v_total_tokens,
            v_cost_usd, v_event_time
        )
        on conflict (user_id, date)
        do update set
            prompt_tokens = conjecta_usage_daily.prompt_tokens + excluded.prompt_tokens,
            completion_tokens = conjecta_usage_daily.completion_tokens + excluded.completion_tokens,
            total_tokens = conjecta_usage_daily.total_tokens + excluded.total_tokens,
            estimated_cost_usd = conjecta_usage_daily.estimated_cost_usd + excluded.estimated_cost_usd,
            updated_at = v_event_time;
    end if;
end;
$$;

-- ---------------------------------------------------------------------------
-- Server-only access: service_role retains access and bypasses RLS.
-- ---------------------------------------------------------------------------
alter table public.conjecta_usage_daily enable row level security;
alter table public.conjecta_usage_events enable row level security;

revoke all privileges on table public.conjecta_usage_daily from anon, authenticated;
revoke all privileges on table public.conjecta_usage_events from anon, authenticated;

grant select, insert, update, delete on table public.conjecta_usage_daily to service_role;
grant select, insert, update, delete on table public.conjecta_usage_events to service_role;

revoke all privileges on function public.increment_usage(text, bigint, bigint, numeric(18, 12), text, text, text) from public;
grant execute on function public.increment_usage(text, bigint, bigint, numeric(18, 12), text, text, text) to service_role;

commit;
