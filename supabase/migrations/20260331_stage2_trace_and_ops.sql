-- Stage 2 / 3: trace、工具日志、审计、成本统计与工具目录补齐。

alter table if exists agent_runs
  add column if not exists memory_source text,
  add column if not exists structured_output jsonb,
  add column if not exists prompt_version text,
  add column if not exists model_usage jsonb;

alter table if exists background_jobs
  add column if not exists attempt_count integer not null default 0;

create table if not exists agent_steps (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references agent_runs(id) on delete cascade,
  conversation_id uuid not null references conversations(id) on delete cascade,
  user_id text not null,
  name text not null,
  sequence integer not null,
  status text not null default 'running',
  input jsonb default '{}'::jsonb,
  output jsonb,
  error text,
  metadata jsonb default '{}'::jsonb,
  started_at timestamptz default now(),
  completed_at timestamptz,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists tool_calls (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references agent_runs(id) on delete cascade,
  conversation_id uuid not null references conversations(id) on delete cascade,
  user_id text not null,
  name text not null,
  arguments jsonb default '{}'::jsonb,
  status text not null default 'completed',
  result jsonb,
  error text,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists run_events (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references agent_runs(id) on delete cascade,
  conversation_id uuid not null references conversations(id) on delete cascade,
  user_id text not null,
  type text not null,
  sequence integer not null,
  payload jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists memory_writebacks (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  conversation_id uuid references conversations(id) on delete set null,
  source text not null,
  summary text,
  items jsonb default '[]'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists prompt_versions (
  id uuid primary key default gen_random_uuid(),
  key text not null,
  version text not null,
  content text not null,
  is_active boolean not null default true,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique (key, version)
);

create table if not exists mcp_servers (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  transport text not null,
  status text not null,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists mcp_server_connections (
  id uuid primary key default gen_random_uuid(),
  server_name text not null,
  user_id text,
  status text not null,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists tool_registry (
  id uuid primary key default gen_random_uuid(),
  server_name text not null,
  name text not null unique,
  description text,
  input_schema jsonb default '{}'::jsonb,
  status text not null default 'available',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists audit_logs (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  action text not null,
  resource_type text not null,
  resource_id text,
  status text not null default 'success',
  detail jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists model_usage_logs (
  id uuid primary key default gen_random_uuid(),
  run_id uuid not null references agent_runs(id) on delete cascade,
  conversation_id uuid not null references conversations(id) on delete cascade,
  user_id text not null,
  model text not null,
  provider text not null,
  stage text not null,
  prompt_tokens integer not null default 0,
  completion_tokens integer not null default 0,
  total_tokens integer not null default 0,
  estimated_cost numeric(18,8) not null default 0,
  metadata jsonb default '{}'::jsonb,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists rate_limit_events (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  scope text not null,
  allowed boolean not null,
  "limit" integer not null,
  window_seconds integer not null,
  count_in_window integer not null,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists workspaces (
  id uuid primary key default gen_random_uuid(),
  slug text not null unique,
  name text not null,
  owner_user_id text not null,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create table if not exists workspace_members (
  id uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id) on delete cascade,
  user_id text not null,
  role text not null default 'owner',
  created_at timestamptz default now(),
  updated_at timestamptz default now(),
  unique (workspace_id, user_id)
);

create table if not exists app_runtime_store (
  id text primary key,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
