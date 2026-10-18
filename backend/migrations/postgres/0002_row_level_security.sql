-- 0002: database-enforced patient isolation (row-level security) for the application schema.
--
-- Model. The backend connects as cosmo_app (no BYPASSRLS) and starts every transaction by publishing the caller as transaction-local settings:
--     app.user_id, app.role (patient|doctor|admin|system|none), app.admin_clinical (read|none), app.ctx_sig = HMAC-SHA256(secret, user_id|role|admin_clinical)
-- The policies below only honour that identity if the signature verifies against a secret stored in app_private.ctx_secret, which cosmo_app cannot read and which the
-- OPERATOR installs (select app_private.set_ctx_secret('...') as the owner). Without a valid signature the effective role is 'none' and every policy denies: an SQL-injection
-- bug in the application could not simply `set_config('app.role','system')`. With no secret installed the database refuses everything (fail closed).
-- The owner/migration role must have BYPASSRLS (Supabase's postgres role does; a local superuser does): it performs migrations, data imports and restores.
-- Clinicians: the current product has no per-clinician patient assignment, so every doctor may read every patient's records; adding an assignment table is a product decision.
-- Administrators: clinical reading is OFF unless COSMO_ADMIN_CLINICAL_ACCESS=read (published as app.admin_clinical).

create schema if not exists app_private;
revoke all on schema app_private from public;
grant usage on schema app_private to cosmo_app;

create table app_private.ctx_secret (
  id integer primary key check (id = 1),
  secret bytea not null check (length(secret) >= 32)
);
revoke all on app_private.ctx_secret from public, cosmo_app;

-- HMAC-SHA256 from the built-in sha256() (no extension needed). Block size 64 bytes.
create function app_private.hmac_sha256(k bytea, m bytea) returns bytea language plpgsql immutable set search_path = pg_catalog as $$
declare
  key bytea := k; ipad bytea; opad bytea; i int;
begin
  if length(key) > 64 then key := sha256(key); end if;
  key := key || decode(repeat('00', 64 - length(key)), 'hex');
  ipad := key; opad := key;
  for i in 0..63 loop
    ipad := set_byte(ipad, i, get_byte(key, i) # 54);   -- 0x36
    opad := set_byte(opad, i, get_byte(key, i) # 92);   -- 0x5c
  end loop;
  return sha256(opad || sha256(ipad || m));
end $$;

create function app_private.set_ctx_secret(s text) returns void language plpgsql security definer set search_path = pg_catalog as $$
begin
  if length(s) < 32 then raise exception 'context secret must be at least 32 characters'; end if;
  insert into app_private.ctx_secret(id, secret) values (1, convert_to(s, 'UTF8'))
    on conflict (id) do update set secret = excluded.secret;
end $$;
revoke all on function app_private.set_ctx_secret(text) from public, cosmo_app;

create function app_private.ctx_ready() returns boolean language sql stable security definer set search_path = pg_catalog as $$
  select exists (select 1 from app_private.ctx_secret) $$;

-- true only when the published identity carries a valid signature
create function app_private.verified() returns boolean language plpgsql stable security definer set search_path = pg_catalog as $$
declare s bytea; msg text; given text;
begin
  select secret into s from app_private.ctx_secret where id = 1;
  if s is null then return false; end if;
  given := current_setting('app.ctx_sig', true);
  if given is null or given = '' then return false; end if;
  msg := coalesce(current_setting('app.user_id', true), '') || E'\x1f' || coalesce(current_setting('app.role', true), '') || E'\x1f' || coalesce(current_setting('app.admin_clinical', true), '');
  return encode(app_private.hmac_sha256(s, convert_to(msg, 'UTF8')), 'hex') = given;
end $$;

-- only the four real roles are ever honoured: anything else (including a validly signed but unknown role) is 'none', and 'none' has no user either
create function app_private.cur_role() returns text language sql stable security definer set search_path = pg_catalog as $$
  select case when app_private.verified() and current_setting('app.role', true) in ('patient', 'doctor', 'admin', 'system') then current_setting('app.role', true) else 'none' end $$;
create function app_private.cur_user() returns text language sql stable security definer set search_path = pg_catalog as $$
  select case when app_private.cur_role() <> 'none' then nullif(current_setting('app.user_id', true), '') else null end $$;
create function app_private.is_system() returns boolean language sql stable security definer set search_path = pg_catalog as $$
  select app_private.cur_role() = 'system' $$;
-- who may read patients' clinical records: doctors, the system, and administrators only when explicitly allowed
create function app_private.clinical_reader() returns boolean language sql stable security definer set search_path = pg_catalog as $$
  select app_private.cur_role() in ('doctor', 'system')
      or (app_private.cur_role() = 'admin' and coalesce(current_setting('app.admin_clinical', true), 'none') = 'read') $$;

revoke all on function app_private.hmac_sha256(bytea, bytea), app_private.ctx_ready(), app_private.verified(), app_private.cur_role(), app_private.cur_user(),
                       app_private.is_system(), app_private.clinical_reader() from public;
grant execute on function app_private.hmac_sha256(bytea, bytea), app_private.ctx_ready(), app_private.verified(), app_private.cur_role(), app_private.cur_user(),
                          app_private.is_system(), app_private.clinical_reader() to cosmo_app;

-- ---------------------------------------------------------------- enable and force RLS on every application table
do $$ declare t text; begin
  foreach t in array array['users','settings','documents','doc_pages','doc_fields','images','conversations','messages','price_items','quotes','kb_articles','datasets',
                           'dataset_versions','model_versions','audit','field_corrections','care_plans','consents','notifications','jobs'] loop
    execute format('alter table app.%I enable row level security', t);
    execute format('alter table app.%I force row level security', t);
  end loop;
end $$;

-- ---------------------------------------------------------------- policies (all TO cosmo_app; an absent policy means DENY)
-- users: everyone sees themselves; clinicians and administrators see the directory; only administrators and the system change accounts and roles
create policy users_select on app.users for select to cosmo_app using (id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'admin', 'system'));
create policy users_write on app.users for all to cosmo_app using ((select app_private.cur_role()) in ('admin', 'system')) with check ((select app_private.cur_role()) in ('admin', 'system'));

-- documents and their pages/fields: owner; clinical readers; doctors and the system review/update; owner and system create/delete
create policy documents_select on app.documents for select to cosmo_app using (owner_id = (select app_private.cur_user()) or (select app_private.clinical_reader()));
create policy documents_insert on app.documents for insert to cosmo_app with check (owner_id = (select app_private.cur_user()) or (select app_private.is_system()));
create policy documents_update on app.documents for update to cosmo_app using (owner_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'system'))
  with check (owner_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'system'));
create policy documents_delete on app.documents for delete to cosmo_app using (owner_id = (select app_private.cur_user()) or (select app_private.is_system()));

create policy doc_pages_select on app.doc_pages for select to cosmo_app using (exists (select 1 from app.documents d where d.id = document_id));
create policy doc_pages_write on app.doc_pages for all to cosmo_app
  using (exists (select 1 from app.documents d where d.id = document_id and (d.owner_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'system'))))
  with check (exists (select 1 from app.documents d where d.id = document_id and (d.owner_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'system'))));
create policy doc_fields_select on app.doc_fields for select to cosmo_app using (exists (select 1 from app.documents d where d.id = document_id));
create policy doc_fields_write on app.doc_fields for all to cosmo_app
  using (exists (select 1 from app.documents d where d.id = document_id and (d.owner_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'system'))))
  with check (exists (select 1 from app.documents d where d.id = document_id and (d.owner_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'system'))));

-- photos: same shape as documents
create policy images_select on app.images for select to cosmo_app using (owner_id = (select app_private.cur_user()) or (select app_private.clinical_reader()));
create policy images_insert on app.images for insert to cosmo_app with check (owner_id = (select app_private.cur_user()) or (select app_private.is_system()));
create policy images_update on app.images for update to cosmo_app using (owner_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'system'))
  with check (owner_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'system'));
create policy images_delete on app.images for delete to cosmo_app using (owner_id = (select app_private.cur_user()) or (select app_private.is_system()));

-- conversations and messages: private to the patient (no staff access in the current product); the system for retention and deletion
create policy conversations_all on app.conversations for all to cosmo_app using (owner_id = (select app_private.cur_user()) or (select app_private.is_system()))
  with check (owner_id = (select app_private.cur_user()) or (select app_private.is_system()));
create policy messages_all on app.messages for all to cosmo_app
  using (exists (select 1 from app.conversations c where c.id = conversation_id))
  with check (exists (select 1 from app.conversations c where c.id = conversation_id));

-- price list and quotations
create policy price_items_select on app.price_items for select to cosmo_app using ((select app_private.cur_role()) in ('patient', 'doctor', 'admin', 'system'));
create policy price_items_write on app.price_items for all to cosmo_app using ((select app_private.cur_role()) in ('doctor', 'system')) with check ((select app_private.cur_role()) in ('doctor', 'system'));
create policy quotes_select on app.quotes for select to cosmo_app using (patient_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'admin', 'system'));
create policy quotes_insert on app.quotes for insert to cosmo_app with check ((patient_id = (select app_private.cur_user()) and status = 'draft') or (select app_private.cur_role()) in ('doctor', 'system'));
create policy quotes_update on app.quotes for update to cosmo_app using ((select app_private.cur_role()) in ('doctor', 'system')) with check ((select app_private.cur_role()) in ('doctor', 'system'));
create policy quotes_delete on app.quotes for delete to cosmo_app using ((select app_private.is_system()));

-- care plans: a patient sees only their own APPROVED plans; clinical readers see all; doctors and the system write
create policy care_plans_select on app.care_plans for select to cosmo_app using ((patient_id = (select app_private.cur_user()) and status = 'approved') or (select app_private.clinical_reader()));
create policy care_plans_write on app.care_plans for all to cosmo_app using ((select app_private.cur_role()) in ('doctor', 'system')) with check ((select app_private.cur_role()) in ('doctor', 'system'));

-- clinician corrections
create policy field_corrections_select on app.field_corrections for select to cosmo_app using ((select app_private.cur_role()) in ('doctor', 'admin', 'system'));
create policy field_corrections_write on app.field_corrections for all to cosmo_app using ((select app_private.cur_role()) in ('doctor', 'admin', 'system'))
  with check ((select app_private.cur_role()) in ('doctor', 'admin', 'system'));

-- knowledge base: everyone reads APPROVED articles; staff read all; only doctors and the system write
create policy kb_select on app.kb_articles for select to cosmo_app using ((review_status = 'approved' and (select app_private.cur_role()) in ('patient', 'doctor', 'admin', 'system')) or (select app_private.cur_role()) in ('doctor', 'admin', 'system'));
create policy kb_write on app.kb_articles for all to cosmo_app using ((select app_private.cur_role()) in ('doctor', 'system')) with check ((select app_private.cur_role()) in ('doctor', 'system'));

-- datasets and models: administrators and the system
create policy datasets_all on app.datasets for all to cosmo_app using ((select app_private.cur_role()) in ('admin', 'system')) with check ((select app_private.cur_role()) in ('admin', 'system'));
create policy dataset_versions_all on app.dataset_versions for all to cosmo_app using ((select app_private.cur_role()) in ('admin', 'system')) with check ((select app_private.cur_role()) in ('admin', 'system'));
create policy model_versions_all on app.model_versions for all to cosmo_app using ((select app_private.cur_role()) in ('admin', 'system')) with check ((select app_private.cur_role()) in ('admin', 'system'));

-- settings: any signed-in role reads; staff write
create policy settings_select on app.settings for select to cosmo_app using ((select app_private.cur_role()) in ('patient', 'doctor', 'admin', 'system'));
create policy settings_write on app.settings for all to cosmo_app using ((select app_private.cur_role()) in ('doctor', 'admin', 'system')) with check ((select app_private.cur_role()) in ('doctor', 'admin', 'system'));

-- audit: append-only. Any signed-in actor may record events about themselves; administrators and the system read.
create policy audit_insert on app.audit for insert to cosmo_app with check (((select app_private.cur_role()) in ('patient', 'doctor', 'admin') and actor = (select app_private.cur_user())) or (select app_private.is_system()));
create policy audit_select on app.audit for select to cosmo_app using ((select app_private.cur_role()) in ('admin', 'system'));

-- consent, notifications, jobs
create policy consents_all on app.consents for all to cosmo_app using (user_id = (select app_private.cur_user()) or (select app_private.is_system()))
  with check (user_id = (select app_private.cur_user()) or (select app_private.is_system()));
create policy notifications_select on app.notifications for select to cosmo_app using (user_id = (select app_private.cur_user()) or (select app_private.is_system()));
create policy notifications_update on app.notifications for update to cosmo_app using (user_id = (select app_private.cur_user()) or (select app_private.is_system()))
  with check (user_id = (select app_private.cur_user()) or (select app_private.is_system()));
create policy notifications_system_write on app.notifications for insert to cosmo_app with check ((select app_private.is_system()));      -- notifications for other users are server actions
create policy notifications_delete on app.notifications for delete to cosmo_app using ((select app_private.is_system()));
create policy jobs_select on app.jobs for select to cosmo_app using (owner_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('admin', 'system'));
create policy jobs_write on app.jobs for all to cosmo_app using (owner_id = (select app_private.cur_user()) or (select app_private.is_system()))
  with check (owner_id = (select app_private.cur_user()) or (select app_private.is_system()));
