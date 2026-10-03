-- AI Agent Content Manager SaaS
-- Run once in Supabase SQL Editor.

create extension if not exists pgcrypto;

create table if not exists public.tenants (
  id uuid primary key default gen_random_uuid(),
  name text not null default 'Новый магазин',
  slug text unique,
  owner_user_id uuid not null references auth.users(id) on delete cascade,
  plan text not null default 'trial',
  status text not null default 'active',
  created_at timestamptz not null default now()
);

create table if not exists public.memberships (
  user_id uuid not null references auth.users(id) on delete cascade,
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  role text not null default 'owner',
  created_at timestamptz not null default now(),
  primary key (user_id, tenant_id)
);

create table if not exists public.subscriptions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null unique references public.tenants(id) on delete cascade,
  provider text,
  provider_customer_id text,
  provider_subscription_id text,
  plan text not null default 'trial',
  status text not null default 'trialing',
  current_period_end timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.app_data (
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  entity text not null,
  record_id text not null,
  payload jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (tenant_id, entity, record_id)
);

create index if not exists idx_memberships_tenant on public.memberships(tenant_id);
create index if not exists idx_tenants_owner_user on public.tenants(owner_user_id);
create index if not exists idx_app_data_tenant_entity on public.app_data(tenant_id, entity);
create index if not exists idx_subscriptions_status on public.subscriptions(status);
alter table public.subscriptions add column if not exists provider_payment_id text;
alter table public.subscriptions add column if not exists provider_payment_method_id text;
alter table public.subscriptions add column if not exists next_billing_at timestamptz;
alter table public.subscriptions add column if not exists auto_renew boolean not null default false;
alter table public.subscriptions add column if not exists cancel_at_period_end boolean not null default false;
alter table public.subscriptions add column if not exists provider_subscription_id text;
create index if not exists idx_subscriptions_provider_payment on public.subscriptions(provider_payment_id);
create unique index if not exists idx_subscriptions_provider_payment_unique
on public.subscriptions(provider, provider_payment_id)
where provider_payment_id is not null;

create or replace function public.activate_paid_subscription(
  p_tenant_id uuid,
  p_plan text,
  p_provider text,
  p_provider_payment_id text,
  p_provider_payment_method_id text default null
)
returns boolean
language plpgsql
security definer
set search_path = ''
as $activate_paid_subscription$
declare
  current_sub public.subscriptions;
begin
  if p_plan not in ('starter','pro','business') then
    raise exception 'INVALID_PLAN';
  end if;
  if p_provider not in ('yookassa','tbank') then
    raise exception 'INVALID_PROVIDER';
  end if;
  if p_provider_payment_id is null or btrim(p_provider_payment_id) = '' then
    raise exception 'PAYMENT_ID_REQUIRED';
  end if;

  if not exists (select 1 from public.tenants where id = p_tenant_id) then
    raise exception 'TENANT_NOT_FOUND';
  end if;

  select * into current_sub
    from public.subscriptions
   where tenant_id = p_tenant_id
   for update;

  if not found then
    insert into public.subscriptions (
      tenant_id, plan, status, provider, provider_payment_id,
      provider_payment_method_id, current_period_end, next_billing_at
    )
    values (
      p_tenant_id, p_plan, 'active', p_provider, p_provider_payment_id,
      p_provider_payment_method_id, now() + interval '30 days',
      now() + interval '30 days'
    );
  elsif current_sub.provider = p_provider
    and current_sub.provider_payment_id = p_provider_payment_id then
    return true;
  else
    if exists (
      select 1 from public.subscriptions
       where provider = p_provider
         and provider_payment_id = p_provider_payment_id
         and tenant_id <> p_tenant_id
    ) then
      raise exception 'PAYMENT_ALREADY_BOUND';
    end if;

    update public.subscriptions
       set plan = p_plan,
           status = 'active',
           provider = p_provider,
           provider_payment_id = p_provider_payment_id,
           provider_payment_method_id = coalesce(
             p_provider_payment_method_id,
             provider_payment_method_id
           ),
           current_period_end = now() + interval '30 days',
           next_billing_at = now() + interval '30 days',
           updated_at = now()
     where tenant_id = p_tenant_id;
  end if;

  update public.tenants
     set plan = p_plan,
         status = 'active'
   where id = p_tenant_id;

  return true;
end;
$activate_paid_subscription$;

revoke all on function public.activate_paid_subscription(
  uuid,text,text,text,text
) from public;
grant execute on function public.activate_paid_subscription(
  uuid,text,text,text,text
) to service_role;

create or replace function public.is_tenant_member(target_tenant uuid)
returns boolean
language sql
security definer
set search_path = ''
as $
  select exists (
    select 1 from public.memberships
    where user_id = auth.uid() and tenant_id = target_tenant
  );
$$;

alter table public.tenants enable row level security;
alter table public.memberships enable row level security;
alter table public.subscriptions enable row level security;
alter table public.app_data enable row level security;

create or replace function public.is_tenant_writer(target_tenant uuid)
returns boolean
language sql
security definer
set search_path = ''
as $
  select exists (
    select 1 from public.memberships
    where user_id = auth.uid()
      and tenant_id = target_tenant
      and role in ('owner','admin','manager','editor')
  );
$$;

create or replace function public.is_tenant_admin(target_tenant uuid)
returns boolean
language sql
security definer
set search_path = ''
as $
  select exists (
    select 1 from public.memberships
    where user_id = auth.uid()
      and tenant_id = target_tenant
      and role in ('owner','admin')
  );
$$;

drop policy if exists "tenant members can read tenants" on public.tenants;
create policy "tenant members can read tenants" on public.tenants
for select to authenticated using ((select public.is_tenant_member(id)));

drop policy if exists "users can read own memberships" on public.memberships;
create policy "users can read own memberships" on public.memberships
for select to authenticated using (user_id = (select auth.uid()));

drop policy if exists "tenant members can read subscription" on public.subscriptions;
create policy "tenant members can read subscription" on public.subscriptions
for select to authenticated using ((select public.is_tenant_member(tenant_id)));

drop policy if exists "tenant members can read app data" on public.app_data;
create policy "tenant members can read app data" on public.app_data
for select to authenticated using ((select public.is_tenant_member(tenant_id)));

drop policy if exists "tenant members can insert app data" on public.app_data;
drop policy if exists "tenant writers can insert app data" on public.app_data;
create policy "tenant writers can insert app data" on public.app_data
for insert to authenticated with check ((select public.is_tenant_writer(tenant_id)));

drop policy if exists "tenant members can update app data" on public.app_data;
drop policy if exists "tenant writers can update app data" on public.app_data;
create policy "tenant writers can update app data" on public.app_data
for update to authenticated using ((select public.is_tenant_writer(tenant_id)))
with check ((select public.is_tenant_writer(tenant_id)));

drop policy if exists "tenant members can delete app data" on public.app_data;
drop policy if exists "tenant writers can delete app data" on public.app_data;
create policy "tenant writers can delete app data" on public.app_data
for delete to authenticated using ((select public.is_tenant_writer(tenant_id)));

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $
declare
  new_tenant uuid;
  store_name text;
  invite_tenant uuid;
  invite_role text;
  invite_id uuid;
begin
  -- If this email was invited before signup, attach the new account
  -- directly to that tenant instead of creating an orphan tenant.
  select id, tenant_id, role
    into invite_id, invite_tenant, invite_role
    from public.invitations
   where status = 'pending'
     and expires_at > now()
     and lower(email) = lower(coalesce(new.email, ''))
   order by created_at desc
   limit 1;

  if invite_id is not null then
    insert into public.memberships(user_id, tenant_id, role)
    values (new.id, invite_tenant, invite_role);
    update public.invitations
       set status = 'accepted'
     where id = invite_id;
    return new;
  end if;

  store_name := coalesce(nullif(new.raw_user_meta_data->>'store_name',''), 'Новый магазин');
  insert into public.tenants(name, owner_user_id, slug)
  values (
    store_name,
    new.id,
    lower(regexp_replace(coalesce(store_name, 'store'), '[^a-zA-Z0-9а-яА-ЯёЁ]+', '-', 'g')) || '-' || substr(new.id::text, 1, 8)
  )
  returning id into new_tenant;
  insert into public.memberships(user_id, tenant_id, role) values (new.id, new_tenant, 'owner');
  insert into public.subscriptions(tenant_id, plan, status) values (new_tenant, 'trial', 'trialing');
  return new;
end;
$;

drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created
after insert on auth.users
for each row execute procedure public.handle_new_user();

-- Team invitations and role administration.
create table if not exists public.invitations (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  email text not null,
  role text not null default 'manager',
  status text not null default 'pending',
  invited_by uuid not null references auth.users(id) on delete cascade,
  created_at timestamptz not null default now(),
  expires_at timestamptz not null default (now() + interval '7 days')
);

create index if not exists idx_invitations_tenant on public.invitations(tenant_id);
create index if not exists idx_invitations_email on public.invitations(lower(email));
create index if not exists idx_invitations_invited_by on public.invitations(invited_by);



-- Restrict SECURITY DEFINER tenant helpers to authenticated callers.
revoke all on function public.is_tenant_member(uuid) from public;
grant execute on function public.is_tenant_member(uuid) to authenticated;
revoke all on function public.is_tenant_writer(uuid) from public;
grant execute on function public.is_tenant_writer(uuid) to authenticated;
revoke all on function public.is_tenant_admin(uuid) from public;
grant execute on function public.is_tenant_admin(uuid) to authenticated;

alter table public.invitations enable row level security;

drop policy if exists "members can read team memberships" on public.memberships;
drop policy if exists "users can read own memberships" on public.memberships;
drop policy if exists "members or users can read memberships" on public.memberships;
create policy "members or users can read memberships" on public.memberships
for select to authenticated using (
  (select public.is_tenant_member(tenant_id))
  or user_id = (select auth.uid())
);

drop policy if exists "admins can read invitations" on public.invitations;
drop policy if exists "invitees can read own invitations" on public.invitations;
drop policy if exists "admins or invitees can read invitations" on public.invitations;
create policy "admins or invitees can read invitations" on public.invitations
for select to authenticated using (
  (select public.is_tenant_admin(tenant_id))
  or (
    lower(email) = lower(coalesce((select auth.jwt()->>'email'), ''))
    and status = 'pending'
    and expires_at > now()
  )
);

drop policy if exists "admins can create invitations" on public.invitations;
create policy "admins can create invitations" on public.invitations
for insert to authenticated with check (
  (select public.is_tenant_admin(tenant_id))
  and invited_by = auth.uid()
  and role in ('admin','manager','editor','viewer')
);

drop policy if exists "admins can cancel invitations" on public.invitations;
create policy "admins can cancel invitations" on public.invitations
for update to authenticated
using ((select public.is_tenant_admin(tenant_id)))
with check ((select public.is_tenant_admin(tenant_id)));

drop policy if exists "admins can delete invitations" on public.invitations;
create policy "admins can delete invitations" on public.invitations
for delete to authenticated
using ((select public.is_tenant_admin(tenant_id)));



create table if not exists public.billing_checkout_sessions (
  id uuid primary key default gen_random_uuid(),
  tenant_id uuid not null references public.tenants(id) on delete cascade,
  provider text not null,
  provider_order_id text not null,
  provider_payment_id text,
  plan text not null,
  status text not null default 'created',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(provider, provider_order_id)
);
create index if not exists idx_billing_checkout_tenant on public.billing_checkout_sessions(tenant_id);
create index if not exists idx_billing_checkout_payment on public.billing_checkout_sessions(provider_payment_id);
alter table public.billing_checkout_sessions enable row level security;
create policy "tenant members can read own checkout sessions"
on public.billing_checkout_sessions for select to authenticated
using ((select public.is_tenant_member(tenant_id)));


-- Invitation acceptance by the invited account.
create or replace function public.accept_invitation(invite_id uuid)
returns uuid
language plpgsql
security definer
set search_path = ''
as $
declare
  inv public.invitations;
  uid uuid := auth.uid();
  user_email text := lower(coalesce(auth.jwt()->>'email',''));
begin
  if uid is null then raise exception 'AUTH_REQUIRED'; end if;
  select * into inv from public.invitations
  where id = invite_id and status = 'pending' and expires_at > now()
    and lower(email) = user_email
  for update;
  if not found then raise exception 'INVITATION_NOT_FOUND_OR_EXPIRED'; end if;
  if exists (
    select 1
      from public.memberships
     where user_id = uid
       and tenant_id = inv.tenant_id
  ) then
    update public.invitations set status='accepted' where id=invite_id;
    return inv.tenant_id;
  end if;

  insert into public.memberships(user_id, tenant_id, role)
  values(uid, inv.tenant_id, inv.role);
  update public.invitations set status='accepted' where id=invite_id;
  return inv.tenant_id;
end;
$$;


-- Owners/admins may update their tenant profile; ordinary members remain read-only.
drop policy if exists "tenant admins can update tenant" on public.tenants;
create policy "tenant admins can update tenant" on public.tenants
for update to authenticated
using ((select public.is_tenant_admin(id)))
with check ((select public.is_tenant_admin(id)));

-- Keep invitation roles constrained at database level.
alter table public.invitations drop constraint if exists invitations_role_check;
alter table public.invitations add constraint invitations_role_check
check (role in ('admin','manager','editor','viewer'));


-- Subscription renewal foundation (webhook/worker can use these fields later).
create index if not exists idx_subscriptions_next_billing on public.subscriptions(next_billing_at);


-- Security hardening for callable RPCs.
revoke all on function public.accept_invitation(uuid) from public;
grant execute on function public.accept_invitation(uuid) to authenticated;

-- Owners/admins may manage existing member roles through a security-definer RPC.
create or replace function public.set_member_role(target_tenant uuid, target_user uuid, new_role text)
returns boolean
language plpgsql
security definer
set search_path = ''
as $
declare
  target_current_role text;
begin
  if not public.is_tenant_admin(target_tenant) then
    raise exception 'NOT_AUTHORIZED';
  end if;
  if new_role not in ('admin','manager','editor','viewer') then
    raise exception 'INVALID_ROLE';
  end if;
  if target_user = auth.uid() then
    raise exception 'CANNOT_CHANGE_OWN_ROLE';
  end if;
  select role into target_current_role
    from public.memberships
   where user_id = target_user and tenant_id = target_tenant
   for update;
  if not found then
    raise exception 'MEMBER_NOT_FOUND';
  end if;
  if target_current_role = 'owner' then
    raise exception 'CANNOT_CHANGE_OWNER_ROLE';
  end if;
  update public.memberships
     set role = new_role
   where user_id = target_user and tenant_id = target_tenant;
  return true;
end;
$$;

revoke all on function public.set_member_role(uuid,uuid,text) from public;
grant execute on function public.set_member_role(uuid,uuid,text) to authenticated;


-- Atomic app_data persistence for fresh installs.
create or replace function public.save_app_data_batch(
  p_tenant_id uuid,
  p_entity text,
  p_rows jsonb
)
returns boolean
language plpgsql
security invoker
set search_path = ''
as $save_app_data_batch$
declare
  item jsonb;
  record_id_value text;
  expected_updated_at timestamptz;
  current_updated_at timestamptz;
  is_new boolean;
  is_deleted boolean;
  seen_ids text[] := '{}';
begin
  if auth.uid() is null then
    raise exception 'AUTH_REQUIRED';
  end if;

  if not public.is_tenant_writer(p_tenant_id) then
    raise exception 'NOT_AUTHORIZED';
  end if;

  if p_entity is null or btrim(p_entity) = '' or p_entity !~ '^[A-Za-z0-9_-]+$' then
    raise exception 'INVALID_ENTITY';
  end if;

  if p_rows is null or jsonb_typeof(p_rows) <> 'array' then
    raise exception 'INVALID_ROWS';
  end if;

  for item in select value from jsonb_array_elements(p_rows)
  loop
    if jsonb_typeof(item) <> 'object' then
      raise exception 'INVALID_ROW';
    end if;

    record_id_value := btrim(coalesce(item->>'record_id',''));
    if record_id_value = '' then
      raise exception 'RECORD_ID_REQUIRED';
    end if;

    if record_id_value = any(seen_ids) then
      raise exception 'DUPLICATE_RECORD_ID';
    end if;
    seen_ids := array_append(seen_ids, record_id_value);

    is_new := coalesce((item->>'is_new')::boolean, false);
    is_deleted := coalesce((item->>'is_deleted')::boolean, false);

    if is_new and is_deleted then
      raise exception 'INVALID_ROW_STATE';
    end if;

    if is_new then
      if nullif(item->>'expected_updated_at','') is not null then
        raise exception 'NEW_ROW_HAS_VERSION';
      end if;

      if exists (
        select 1
        from public.app_data
        where tenant_id = p_tenant_id
          and entity = p_entity
          and record_id = record_id_value
      ) then
        raise exception 'RECORD_ALREADY_EXISTS';
      end if;

      insert into public.app_data(tenant_id, entity, record_id, payload)
      values (
        p_tenant_id,
        p_entity,
        record_id_value,
        coalesce(item->'payload','{}'::jsonb)
      );

    elsif nullif(item->>'expected_updated_at','') is null then
      raise exception 'EXPECTED_VERSION_REQUIRED';

    else
      expected_updated_at := (item->>'expected_updated_at')::timestamptz;

      select updated_at
        into current_updated_at
        from public.app_data
       where tenant_id = p_tenant_id
         and entity = p_entity
         and record_id = record_id_value
       for update;

      if not found then
        raise exception 'RECORD_NOT_FOUND';
      end if;

      if current_updated_at <> expected_updated_at then
        raise exception 'DATA_CONFLICT';
      end if;

      if is_deleted then
        delete from public.app_data
        where tenant_id = p_tenant_id
          and entity = p_entity
          and record_id = record_id_value;
      else
        update public.app_data
           set payload = coalesce(item->'payload','{}'::jsonb),
               updated_at = now()
         where tenant_id = p_tenant_id
           and entity = p_entity
           and record_id = record_id_value;
      end if;
    end if;
  end loop;

  return true;
end;
$save_app_data_batch$;

revoke all on function public.save_app_data_batch(uuid,text,jsonb) from public;
revoke all on function public.save_app_data_batch(uuid,text,jsonb) from anon;
grant execute on function public.save_app_data_batch(uuid,text,jsonb) to authenticated;
