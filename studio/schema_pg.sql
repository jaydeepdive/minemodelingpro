-- MMP Studio — Postgres schema (DigitalOcean Managed Postgres / App Platform)
--
-- There is no row-level security here: every query goes through studio/server.py,
-- which authenticates the session cookie and scopes each statement to the signed-in
-- account (admins excepted). Object keys are prefixed with the owner's id, so the
-- storage layer is scoped the same way.
--
-- Safe to run repeatedly.

create extension if not exists "pgcrypto";

-- ------------------------------------------------------------------- accounts
create table if not exists users (
  id            uuid primary key default gen_random_uuid(),
  email         text not null,
  pw            text not null,                      -- scrypt, see server.hash_pw
  name          text,
  role          text not null default 'client',     -- 'client' | 'admin'
  quota_bytes   bigint not null default 2147483648, -- 2 GB
  token_version integer not null default 1,         -- bump to invalidate sessions
  disabled      boolean not null default false,
  created_at    timestamptz not null default now(),
  last_seen     timestamptz
);
create unique index if not exists users_email_key on users (lower(email));

-- invitations: this platform is client-basis, so accounts are issued, not opened
create table if not exists invites (
  code       text primary key,
  email      text,
  role       text not null default 'client',
  note       text,
  created_by uuid references users(id) on delete set null,
  used_by    uuid references users(id) on delete set null,
  used_at    timestamptz,
  expires_at timestamptz,
  created_at timestamptz not null default now()
);

-- ------------------------------------------------------------------- projects
create table if not exists projects (
  id         uuid primary key default gen_random_uuid(),
  owner      uuid not null references users(id) on delete cascade,
  slug       text not null,
  name       text not null,
  company    text,
  region     text,
  commodity  text,
  is_public  boolean not null default false,
  attach_to  text,                                  -- slug of a public MMP project to build on
  params     jsonb not null default '{}'::jsonb,
  status     text not null default 'new',            -- new|parsing|building|ready|error
  error      text,
  stats      jsonb,
  model_key  text,
  public_key text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create unique index if not exists projects_owner_slug on projects (owner, slug);
create index if not exists projects_public on projects (is_public) where is_public;

create table if not exists datasets (
  id         uuid primary key default gen_random_uuid(),
  project_id uuid not null references projects(id) on delete cascade,
  owner      uuid not null references users(id) on delete cascade,
  name       text,
  files      jsonb not null default '[]'::jsonb,     -- [{filename, key, bytes}]
  options    jsonb not null default '{}'::jsonb,     -- {epsg, utm_zone, hemisphere, datum}
  summary    jsonb,
  data_key   text,
  bytes      bigint not null default 0,
  status     text not null default 'new',            -- new|parsing|ready|error
  error      text,
  created_at timestamptz not null default now()
);
create index if not exists datasets_project on datasets (project_id);

create table if not exists jobs (
  id          uuid primary key default gen_random_uuid(),
  kind        text not null,                         -- import|build|publish|delete
  owner       uuid references users(id) on delete cascade,
  project_id  uuid,
  dataset_id  uuid,
  payload     jsonb not null default '{}'::jsonb,
  status      text not null default 'queued',        -- queued|running|done|error
  log         text,
  attempts    integer not null default 0,
  created_at  timestamptz not null default now(),
  started_at  timestamptz,
  finished_at timestamptz
);
create index if not exists jobs_queue on jobs (status, created_at);
create index if not exists jobs_project on jobs (project_id, created_at desc);

-- a small audit trail: who signed in, what was published, what was deleted
create table if not exists events (
  id         bigserial primary key,
  at         timestamptz not null default now(),
  user_id    uuid,
  kind       text not null,
  detail     jsonb
);
create index if not exists events_at on events (at desc);
