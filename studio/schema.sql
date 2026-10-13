-- MineModelingPro Studio — Supabase schema
-- Accounts, uploaded drill data, private projects, build jobs and publishing.
-- Run this once in the Supabase SQL editor (it is idempotent).

create extension if not exists "pgcrypto";

-- ---------------------------------------------------------------- profiles
create table if not exists public.profiles (
  id          uuid primary key references auth.users on delete cascade,
  email       text,
  name        text,
  storage_bytes bigint not null default 0,
  quota_bytes bigint not null default 536870912,          -- 512 MB per account
  created_at  timestamptz not null default now()
);

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  insert into public.profiles (id, email, name)
  values (new.id, new.email, coalesce(new.raw_user_meta_data->>'name', split_part(new.email,'@',1)))
  on conflict (id) do nothing;
  return new;
end $$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function public.handle_new_user();

-- ---------------------------------------------------------------- projects
do $$ begin
  create type project_status as enum ('draft','queued','building','ready','error');
exception when duplicate_object then null; end $$;

create table if not exists public.projects (
  id          uuid primary key default gen_random_uuid(),
  owner       uuid not null references auth.users on delete cascade,
  slug        text not null,
  name        text not null,
  company     text,
  region      text,
  commodity   text,
  is_public   boolean not null default false,            -- published to the public gallery
  attach_to   text,                                      -- public project slug to merge with
  params      jsonb not null default '{}'::jsonb,        -- see minemodelingpro.params
  status      project_status not null default 'draft',
  stats       jsonb not null default '{}'::jsonb,        -- holes / intervals / blocks / metal
  model_path  text,                                      -- storage path of model.json
  error       text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now(),
  unique (owner, slug)
);
create index if not exists projects_owner_idx on public.projects(owner);
create index if not exists projects_public_idx on public.projects(is_public) where is_public;

-- ---------------------------------------------------------------- datasets
create table if not exists public.datasets (
  id          uuid primary key default gen_random_uuid(),
  owner       uuid not null references auth.users on delete cascade,
  project_id  uuid references public.projects on delete cascade,
  name        text,
  files       jsonb not null default '[]'::jsonb,        -- [{path, filename, bytes}]
  options     jsonb not null default '{}'::jsonb,        -- {epsg, utm_zone, hemisphere, datum}
  summary     jsonb,                                     -- counts / tables / notes from the importer
  data_path   text,                                      -- storage path of the parsed dataset JSON
  status      text not null default 'uploaded',          -- uploaded | parsing | ready | error
  error       text,
  created_at  timestamptz not null default now()
);
create index if not exists datasets_owner_idx on public.datasets(owner);
create index if not exists datasets_project_idx on public.datasets(project_id);

-- ---------------------------------------------------------------- jobs
create table if not exists public.jobs (
  id          uuid primary key default gen_random_uuid(),
  owner       uuid not null references auth.users on delete cascade,
  kind        text not null,                             -- import | build | publish | delete
  project_id  uuid references public.projects on delete cascade,
  dataset_id  uuid references public.datasets on delete cascade,
  payload     jsonb not null default '{}'::jsonb,
  status      text not null default 'queued',            -- queued | running | done | error
  log         text,
  attempts    int not null default 0,
  created_at  timestamptz not null default now(),
  started_at  timestamptz,
  finished_at timestamptz
);
create index if not exists jobs_queue_idx on public.jobs(status, created_at);
create index if not exists jobs_owner_idx on public.jobs(owner);

-- ---------------------------------------------------------------- row level security
alter table public.profiles enable row level security;
alter table public.projects enable row level security;
alter table public.datasets enable row level security;
alter table public.jobs     enable row level security;

drop policy if exists "profiles self" on public.profiles;
create policy "profiles self" on public.profiles
  for all using (auth.uid() = id) with check (auth.uid() = id);

drop policy if exists "projects owner" on public.projects;
create policy "projects owner" on public.projects
  for all using (auth.uid() = owner) with check (auth.uid() = owner);

drop policy if exists "projects public read" on public.projects;
create policy "projects public read" on public.projects
  for select using (is_public);

drop policy if exists "datasets owner" on public.datasets;
create policy "datasets owner" on public.datasets
  for all using (auth.uid() = owner) with check (auth.uid() = owner);

drop policy if exists "jobs owner" on public.jobs;
create policy "jobs owner" on public.jobs
  for all using (auth.uid() = owner) with check (auth.uid() = owner);

-- the worker connects with the service-role key, which bypasses RLS.

-- ---------------------------------------------------------------- storage
insert into storage.buckets (id, name, public, file_size_limit)
values ('uploads','uploads', false, 209715200)                      -- 200 MB per file
on conflict (id) do update set file_size_limit = excluded.file_size_limit;
insert into storage.buckets (id, name, public, file_size_limit)
values ('models','models', false, 209715200)
on conflict (id) do nothing;
insert into storage.buckets (id, name, public, file_size_limit)
values ('public-models','public-models', true, 209715200)
on conflict (id) do nothing;

-- every object lives under <user id>/..., so the first path segment is the owner
drop policy if exists "uploads own folder" on storage.objects;
create policy "uploads own folder" on storage.objects
  for all using (bucket_id = 'uploads' and (storage.foldername(name))[1] = auth.uid()::text)
  with check (bucket_id = 'uploads' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists "models own folder read" on storage.objects;
create policy "models own folder read" on storage.objects
  for select using (bucket_id = 'models' and (storage.foldername(name))[1] = auth.uid()::text);

drop policy if exists "public models read" on storage.objects;
create policy "public models read" on storage.objects
  for select using (bucket_id = 'public-models');

-- ---------------------------------------------------------------- housekeeping
create or replace function public.touch_project() returns trigger
language plpgsql as $$ begin new.updated_at = now(); return new; end $$;
drop trigger if exists projects_touch on public.projects;
create trigger projects_touch before update on public.projects
  for each row execute function public.touch_project();

-- a project's storage usage, used to enforce the per-account quota
create or replace function public.account_usage(uid uuid) returns bigint
language sql security definer set search_path = public, storage as $$
  select coalesce(sum((metadata->>'size')::bigint), 0)
  from storage.objects
  where bucket_id in ('uploads','models') and (storage.foldername(name))[1] = uid::text
$$;
