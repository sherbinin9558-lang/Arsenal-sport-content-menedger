-- Prevent invited signups from creating an orphan tenant.
-- The auth trigger attaches a newly created account to its pending invitation.
-- Existing-account invitation acceptance remains idempotent.

create or replace function public.handle_new_user()
returns trigger
language plpgsql
security definer
set search_path = ''
as $$
declare
  new_tenant uuid;
  store_name text;
  invite_tenant uuid;
  invite_role text;
  invite_id uuid;
begin
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

  insert into public.memberships(user_id, tenant_id, role)
  values (new.id, new_tenant, 'owner');

  insert into public.subscriptions(tenant_id, plan, status)
  values (new_tenant, 'trial', 'trialing');

  return new;
end;
$$;

create or replace function public.accept_invitation(invite_id uuid)
returns uuid
language plpgsql
security definer
set search_path = ''
as $$
declare
  inv public.invitations;
  uid uuid := auth.uid();
  user_email text := lower(coalesce(auth.jwt()->>'email',''));
begin
  if uid is null then
    raise exception 'AUTH_REQUIRED';
  end if;

  select *
    into inv
    from public.invitations
   where id = invite_id
     and status = 'pending'
     and expires_at > now()
     and lower(email) = user_email
   for update;

  if not found then
    raise exception 'INVITATION_NOT_FOUND_OR_EXPIRED';
  end if;

  if exists (
    select 1
      from public.memberships
     where user_id = uid
       and tenant_id = inv.tenant_id
  ) then
    update public.invitations
       set status = 'accepted'
     where id = invite_id;
    return inv.tenant_id;
  end if;

  insert into public.memberships(user_id, tenant_id, role)
  values (uid, inv.tenant_id, inv.role);

  update public.invitations
     set status = 'accepted'
   where id = invite_id;

  return inv.tenant_id;
end;
$$;

revoke all on function public.accept_invitation(uuid) from public;
grant execute on function public.accept_invitation(uuid) to authenticated;
