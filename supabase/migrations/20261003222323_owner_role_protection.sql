-- Protect the tenant owner from ordinary role changes.
-- Payment code is intentionally untouched.

create or replace function public.set_member_role(target_tenant uuid, target_user uuid, new_role text)
returns boolean
language plpgsql
security definer
set search_path = ''
as $set_member_role$
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
  update public.memberships set role = new_role
   where user_id = target_user and tenant_id = target_tenant;
  return true;
end;
$set_member_role$;

revoke all on function public.set_member_role(uuid,uuid,text) from public;
grant execute on function public.set_member_role(uuid,uuid,text) to authenticated;
