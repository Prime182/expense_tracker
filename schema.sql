-- Expense Tracker schema for Supabase.
-- Run once: Supabase dashboard -> SQL Editor -> New query -> paste -> Run.
-- Safe to run more than once; every statement is guarded.

-- Accounts live in Supabase's built-in auth.users. Every table below hangs off it,
-- and Row Level Security makes the database itself enforce that one person can only
-- ever read or write their own rows.

create table if not exists categories (
  id       bigint generated always as identity primary key,
  user_id  uuid not null references auth.users(id) on delete cascade,
  name     text not null check (length(trim(name)) between 1 and 60),
  unique (user_id, name)
);

create table if not exists subcategories (
  id           bigint generated always as identity primary key,
  user_id      uuid not null references auth.users(id) on delete cascade,
  category_id  bigint not null references categories(id) on delete cascade,
  name         text not null check (length(trim(name)) between 1 and 60),
  unique (category_id, name)
);

create table if not exists expenses (
  id              bigint generated always as identity primary key,
  user_id         uuid not null references auth.users(id) on delete cascade,
  txn_date        date not null,
  amount          numeric(14,2) not null check (amount > 0),
  category_id     bigint not null references categories(id) on delete restrict,
  subcategory_id  bigint references subcategories(id) on delete set null,
  description     text not null default '',
  payment_method  text not null check (payment_method in ('Cash','UPI','Debit','Others')),
  created_at      timestamptz not null default now()
);

create table if not exists limits (
  id           bigint generated always as identity primary key,
  user_id      uuid not null references auth.users(id) on delete cascade,
  kind         text not null check (kind in ('daily','monthly','category')),
  category_id  bigint references categories(id) on delete cascade,
  amount       numeric(14,2) not null check (amount > 0)
);

create index if not exists expenses_user_date on expenses (user_id, txn_date);
create index if not exists categories_user on categories (user_id);
create index if not exists subcategories_user on subcategories (user_id);
create unique index if not exists limits_unique on limits (user_id, kind, coalesce(category_id, 0));

-- Row Level Security: without a policy, no row is visible to anyone.
alter table categories    enable row level security;
alter table subcategories enable row level security;
alter table expenses      enable row level security;
alter table limits        enable row level security;

do $$
declare t text;
begin
  foreach t in array array['categories','subcategories','expenses','limits'] loop
    execute format('drop policy if exists own_rows on %I', t);
    execute format(
      'create policy own_rows on %I for all to authenticated
         using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()))', t);
  end loop;
end $$;
