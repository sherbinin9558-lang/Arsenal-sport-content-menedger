-- Atomic multi-record app_data persistence with optimistic concurrency.
-- The function is SECURITY INVOKER so PostgREST executes it as the authenticated
-- caller. It validates the writer role before touching data and runs atomically.

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
