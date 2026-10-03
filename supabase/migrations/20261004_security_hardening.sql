-- Final security hardening for invitation and checkout RLS.
-- Payment business logic is intentionally untouched.

drop policy if exists "admins can cancel invitations" on public.invitations;
create policy "admins can cancel invitations" on public.invitations
for update to authenticated
using ((select public.is_tenant_admin(tenant_id)))
with check ((select public.is_tenant_admin(tenant_id)));

drop policy if exists "admins can delete invitations" on public.invitations;
create policy "admins can delete invitations" on public.invitations
for delete to authenticated
using ((select public.is_tenant_admin(tenant_id)));

drop policy if exists "tenant members can read own checkout sessions" on public.billing_checkout_sessions;
create policy "tenant members can read own checkout sessions"
on public.billing_checkout_sessions for select to authenticated
using ((select public.is_tenant_member(tenant_id)));

create or replace function public.accept_invitation(invite_id uuid)
returns uuid
language plpgsql
security definer
set search_path = ''
as $accept_invitation$
declare
  inv public.invitations;
  uid uuid := auth.uid();
  user_email text := lower(coalesce(auth.jwt()->>'email',''));
begin
  if uid is null then
    raise exception 'AUTH_REQUIRED';
  end if;

  select * into inv
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
    raise exception 'ALREADY_A_MEMBER';
  end if;

  insert into public.memberships(user_id, tenant_id, role)
  values(uid, inv.tenant_id, inv.role);

  update public.invitations
     set status = 'accepted'
   where id = invite_id;

  return inv.tenant_id;
end;
$accept_invitation$;

revoke all on function public.accept_invitation(uuid) from public;
grant execute on function public.accept_invitation(uuid) to authenticated;
