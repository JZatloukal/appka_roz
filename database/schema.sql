-- Rozanov Sales Manager database schema for Neon Postgres.
-- Apply with:
--   psql "$DATABASE_URL" -f database/schema.sql

create table if not exists sales (
    id bigint primary key,
    customer text not null default 'Neznámý klient',
    amount integer not null check (amount >= 0),
    note text not null default '',
    created_at timestamptz not null,
    inserted_at timestamptz not null default now()
);

create table if not exists achievements (
    achievement_key text primary key,
    unlocked_at timestamptz,
    value integer,
    minutes numeric(10, 2),
    reflection text,
    metadata jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now()
);

create table if not exists completed_challenges (
    completion_key text primary key,
    challenge_id text not null,
    title text not null,
    period text not null check (period in ('daily', 'weekly')),
    completed_at timestamptz not null,
    xp integer not null default 0,
    metadata jsonb not null default '{}'::jsonb
);

create table if not exists rewards (
    reward_id text primary key,
    status text not null check (status in ('locked', 'available', 'discussing', 'delivered')),
    unlocked_at timestamptz,
    requested_at timestamptz,
    delivered_at timestamptz,
    email_sent_at timestamptz,
    metadata jsonb not null default '{}'::jsonb,
    updated_at timestamptz not null default now()
);

create table if not exists xp_entries (
    id text primary key,
    title text not null,
    xp integer not null default 0,
    category text not null,
    created_at timestamptz not null,
    metadata jsonb not null default '{}'::jsonb
);

create table if not exists appearance (
    user_key text primary key default 'default',
    theme text not null default 'rose',
    updated_at timestamptz not null default now()
);

create table if not exists voice_state (
    user_key text primary key default 'default',
    voice_sales_counter integer not null default 0,
    voice_unlocked boolean not null default false,
    voice_play_count integer not null default 0,
    voice_clip_filename text,
    last_voice_clip_filename text,
    updated_at timestamptz not null default now()
);

create table if not exists analytics_events (
    id bigserial primary key,
    event_type text not null,
    app_user text,
    path text,
    method text,
    ip_hash text,
    user_agent text,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_sales_created_at on sales (created_at desc);
create index if not exists idx_sales_customer on sales (customer);
create index if not exists idx_completed_challenges_completed_at on completed_challenges (completed_at desc);
create index if not exists idx_xp_entries_created_at on xp_entries (created_at desc);
create index if not exists idx_analytics_events_created_at on analytics_events (created_at desc);
create index if not exists idx_analytics_events_type on analytics_events (event_type);
