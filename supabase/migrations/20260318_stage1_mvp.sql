-- Stage 1 MVP: 为 Supabase 准备最小可用表结构。
-- 说明：
-- 1. Better Auth 在本地开发默认会回退到 memory adapter。
-- 2. 当你补充 DATABASE_URL / SUPABASE_* 环境变量后，可以把认证和业务数据逐步切到这些表。

create extension if not exists pgcrypto;

create table if not exists users (
  id text primary key,
  name text,
  email text unique,
  email_verified boolean default false,
  image text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists sessions (
  id text primary key,
  user_id text not null references users(id) on delete cascade,
  token text not null unique,
  ip_address text,
  user_agent text,
  expires_at timestamptz not null,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists accounts (
  id text primary key,
  user_id text not null references users(id) on delete cascade,
  account_id text not null,
  provider_id text not null,
  access_token text,
  refresh_token text,
  id_token text,
  password text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists verifications (
  id text primary key,
  identifier text not null,
  value text not null,
  expires_at timestamptz not null,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists conversations (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  title text not null default '新的任务',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations(id) on delete cascade,
  user_id text not null,
  role text not null check (role in ('user', 'assistant')),
  content text not null,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists agent_runs (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations(id) on delete cascade,
  user_id text not null,
  prompt text not null,
  status text not null default 'running',
  result text,
  memory_context text,
  tool_calls jsonb default '[]'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists documents (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations(id) on delete cascade,
  user_id text not null,
  original_name text not null,
  content_type text,
  size bigint default 0,
  file_path text,
  status text not null default 'queued',
  extracted_text text,
  summary text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists background_jobs (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid not null references conversations(id) on delete cascade,
  document_id uuid references documents(id) on delete cascade,
  user_id text not null,
  type text not null,
  status text not null default 'queued',
  output jsonb,
  error text,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists user_preferences (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  key text not null,
  value text not null,
  source text not null default 'local',
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique (user_id, key)
);
