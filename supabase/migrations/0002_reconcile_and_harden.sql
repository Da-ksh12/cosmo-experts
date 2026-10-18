-- 0002: bring the Postgres schema to parity with the application's current data model and fix the row-level-security design.
--
-- A. Parity: tables/columns added to the application after 0001 (care plans, jobs, corrections, settings, review/quote/catalogue columns).
-- B. Hardening (each item below was demonstrated against a real Supabase project with supabase/tests/rls_e2e.py BEFORE this migration):
--      * patients could UPDATE their own documents/photos (e.g. mark them "reviewed by a clinician"),
--      * patients could INSERT "assistant" messages and quotations with arbitrary totals,
--      * patients could upload any file/mime type directly to private storage,
--      * profiles had no protection against role changes by the user and no sign-up trigger (role must NEVER come from user metadata),
--      * the audit table was mutable.
--    Model: defence in depth. The FastAPI service uses the service role (bypasses RLS) and still authorises every request. Direct client access is
--    READ-ONLY for patients; clinical writes are limited to the 'doctor' role by both column privileges and policies; administrators cannot author or
--    approve clinical content.

-- ---------------------------------------------------------------- A. parity
alter table profiles  add column if not exists credentials text default '', add column if not exists verified boolean default false;
alter table documents add column if not exists size_bytes bigint default 0, add column if not exists user_category text default '',
                      add column if not exists deleted boolean default false, add column if not exists language text default 'en', add column if not exists error text default '';
alter table doc_fields add column if not exists patient_flag text default '';
alter table images    add column if not exists deleted boolean default false, add column if not exists clinician_review text default '',
                      add column if not exists reviewed_by uuid references profiles(id), add column if not exists reviewed_at timestamptz;
alter table price_items add column if not exists category text default 'General', add column if not exists sessions int default 1,
                        add column if not exists duration_min int default 0, add column if not exists compare_at_price numeric(12,2);
alter table quotes    add column if not exists subtotal numeric(12,2) default 0, add column if not exists tax_percent numeric(5,2) default 0, add column if not exists tax_amount numeric(12,2) default 0;

alter table profiles add constraint profiles_role_ck check (role in ('patient','doctor','admin')) not valid;
alter table profiles validate constraint profiles_role_ck;
alter table documents add constraint documents_review_ck check (review_status in ('pending_clinician_review','reviewed_by_clinician')) not valid;
alter table quotes add constraint quotes_status_ck check (status in ('draft','approved')) not valid;
alter table kb_articles add constraint kb_status_ck check (review_status in ('draft','approved')) not valid;

create table if not exists settings (key text primary key, value jsonb not null);

create table if not exists care_plans (
  id uuid primary key default gen_random_uuid(),
  patient_id uuid not null references profiles(id) on delete cascade,
  doctor_id uuid references profiles(id),
  status text not null default 'draft' check (status in ('draft','approved','superseded')),
  assessment text default '', findings jsonb default '[]', stages jsonb default '[]', notes text default '', recommended_item_ids jsonb default '[]',
  reviewed_at timestamptz, updated_at timestamptz default now()
);
create table if not exists field_corrections (
  id uuid primary key default gen_random_uuid(),
  field_id uuid, document_id uuid references documents(id) on delete cascade,
  before jsonb, after jsonb, status text, reviewer uuid references profiles(id), ts timestamptz default now(), processing_version text,
  training_eligible boolean not null default false, governance_ref text default '', deidentified boolean not null default false,
  constraint corrections_governance_ck check (not training_eligible or (governance_ref <> '' and deidentified))
);
create table if not exists jobs (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references profiles(id) on delete cascade,
  task text not null, status text not null, request_id text, idempotency_key text, params jsonb, result jsonb, error text,
  created_at timestamptz default now(), started_at timestamptz, finished_at timestamptz, cancel_requested boolean default false,
  unique (owner_id, idempotency_key)
);

-- indexes on every column used by RLS or by the application's lookups
create index if not exists documents_owner_idx on documents(owner_id);
create index if not exists doc_pages_doc_idx on doc_pages(document_id);
create index if not exists doc_fields_doc_idx on doc_fields(document_id);
create index if not exists images_owner_idx on images(owner_id);
create index if not exists conversations_owner_idx on conversations(owner_id);
create index if not exists messages_conv_idx on messages(conversation_id);
create index if not exists quotes_patient_idx on quotes(patient_id);
create index if not exists care_plans_patient_idx on care_plans(patient_id);
create index if not exists corrections_doc_idx on field_corrections(document_id);
create index if not exists jobs_owner_idx on jobs(owner_id, created_at);
create unique index if not exists care_plans_one_draft_idx on care_plans(patient_id) where status = 'draft';
create index if not exists price_items_approver_idx on price_items(approved_by);
create index if not exists kb_reviewer_idx on kb_articles(reviewed_by);
create index if not exists images_reviewer_idx on images(reviewed_by);
create index if not exists care_plans_doctor_idx on care_plans(doctor_id);
create index if not exists corrections_reviewer_idx on field_corrections(reviewer);
create index if not exists dataset_versions_ds_idx on dataset_versions(dataset_id);

alter table settings enable row level security;
alter table care_plans enable row level security;
alter table field_corrections enable row level security;
alter table jobs enable row level security;

-- ---------------------------------------------------------------- B. role helpers (search_path pinned; not executable by anon)
create or replace function public.my_role() returns text language sql stable security definer set search_path = '' as
$$ select p.role from public.profiles p where p.id = (select auth.uid()) $$;
create or replace function public.is_doctor() returns boolean language sql stable security definer set search_path = '' as
$$ select coalesce((select p.role = 'doctor' from public.profiles p where p.id = (select auth.uid())), false) $$;
create or replace function public.is_staff() returns boolean language sql stable security definer set search_path = '' as
$$ select coalesce((select p.role in ('doctor','admin') from public.profiles p where p.id = (select auth.uid())), false) $$;
create or replace function public.is_admin() returns boolean language sql stable security definer set search_path = '' as
$$ select coalesce((select p.role = 'admin' from public.profiles p where p.id = (select auth.uid())), false) $$;
revoke execute on function public.my_role(), public.is_doctor(), public.is_staff(), public.is_admin() from public, anon;
grant execute on function public.my_role(), public.is_doctor(), public.is_staff(), public.is_admin() to authenticated, service_role;

-- every new auth user becomes a PATIENT. The role is never read from user-controlled metadata.
create or replace function public.handle_new_user() returns trigger language plpgsql security definer set search_path = '' as $$
begin
  insert into public.profiles (id, full_name, role) values (new.id, coalesce(new.raw_user_meta_data ->> 'full_name', ''), 'patient') on conflict (id) do nothing;
  return new;
end $$;
revoke execute on function public.handle_new_user() from public, anon, authenticated;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users for each row execute function public.handle_new_user();

-- the audit trail cannot be altered, even by the service role
create or replace function public.audit_immutable() returns trigger language plpgsql set search_path = '' as $$
begin raise exception 'audit records are append-only'; end $$;
drop trigger if exists audit_no_change on audit;
create trigger audit_no_change before update or delete on audit for each row execute function public.audit_immutable();
drop trigger if exists audit_no_truncate on audit;
create trigger audit_no_truncate before truncate on audit for each statement execute function public.audit_immutable();

-- ---------------------------------------------------------------- C. privileges: read-only by default, narrow column grants
revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
alter default privileges in schema public revoke all on tables from anon, authenticated;
alter default privileges in schema public revoke all on sequences from anon, authenticated;

grant select on profiles, documents, doc_pages, doc_fields, images, conversations, messages, price_items, quotes, kb_articles, care_plans, field_corrections, jobs, settings,
                datasets, dataset_versions, model_versions, audit to authenticated;
grant update (full_name) on profiles to authenticated;
grant update (review_status) on documents to authenticated;
grant update (value, unit, ref_range, review_status, corrected_by) on doc_fields to authenticated;
grant update (clinician_review, reviewed_by, reviewed_at) on images to authenticated;
grant insert, update on care_plans to authenticated;
grant insert on field_corrections to authenticated;
grant update (training_eligible, governance_ref, deidentified) on field_corrections to authenticated;
grant insert, update on price_items, kb_articles to authenticated;
grant update (status, approved_by, lines, total, subtotal, tax_percent, tax_amount, currency, notes, updated_at) on quotes to authenticated;
grant insert, update on settings to authenticated;
grant all on all tables in schema public to service_role;

-- ---------------------------------------------------------------- D. policies (old broad ones removed)
drop policy if exists profiles_self on profiles;           drop policy if exists docs_owner on documents;            drop policy if exists docs_staff_read on documents;
drop policy if exists pages_owner on doc_pages;            drop policy if exists fields_owner on doc_fields;         drop policy if exists fields_doctor_update on doc_fields;
drop policy if exists images_owner on images;              drop policy if exists images_staff_read on images;        drop policy if exists conv_owner on conversations;
drop policy if exists msg_owner on messages;               drop policy if exists price_read on price_items;          drop policy if exists price_staff on price_items;
drop policy if exists quotes_owner_read on quotes;         drop policy if exists quotes_patient_insert on quotes;    drop policy if exists quotes_doctor on quotes;
drop policy if exists kb_read_approved on kb_articles;     drop policy if exists kb_doctor on kb_articles;           drop policy if exists admin_only_datasets on datasets;
drop policy if exists admin_only_dsv on dataset_versions;  drop policy if exists admin_only_models on model_versions; drop policy if exists admin_read_audit on audit;
drop policy if exists storage_owner on storage.objects;    drop policy if exists storage_staff_read on storage.objects;

create policy profiles_select on profiles for select to authenticated using (id = (select auth.uid()) or (select public.is_staff()));
create policy profiles_update_self on profiles for update to authenticated using (id = (select auth.uid())) with check (id = (select auth.uid()));   -- full_name only (column grant)

create policy documents_select on documents for select to authenticated using ((owner_id = (select auth.uid()) and not deleted) or (select public.is_staff()));
create policy documents_review on documents for update to authenticated using ((select public.is_doctor())) with check ((select public.is_doctor()));
create policy doc_pages_select on doc_pages for select to authenticated using (exists (select 1 from documents d where d.id = document_id and ((d.owner_id = (select auth.uid()) and not d.deleted) or (select public.is_staff()))));
create policy doc_fields_select on doc_fields for select to authenticated using (exists (select 1 from documents d where d.id = document_id and ((d.owner_id = (select auth.uid()) and not d.deleted) or (select public.is_staff()))));
create policy doc_fields_review on doc_fields for update to authenticated using ((select public.is_doctor())) with check ((select public.is_doctor()));

create policy images_select on images for select to authenticated using ((owner_id = (select auth.uid()) and not deleted) or (select public.is_staff()));
create policy images_review on images for update to authenticated using ((select public.is_doctor())) with check ((select public.is_doctor()) and reviewed_by = (select auth.uid()));

create policy conversations_select on conversations for select to authenticated using (owner_id = (select auth.uid()));        -- clinicians and admins do not read patient chats
create policy messages_select on messages for select to authenticated using (exists (select 1 from conversations c where c.id = conversation_id and c.owner_id = (select auth.uid())));

create policy price_items_select on price_items for select to authenticated using ((active and approved_by is not null) or (select public.is_doctor()));
create policy price_items_insert on price_items for insert to authenticated with check ((select public.is_doctor()) and approved_by = (select auth.uid()));
create policy price_items_update on price_items for update to authenticated using ((select public.is_doctor())) with check ((select public.is_doctor()));

create policy quotes_select on quotes for select to authenticated using (patient_id = (select auth.uid()) or (select public.is_staff()));
create policy quotes_doctor_update on quotes for update to authenticated using ((select public.is_doctor()))
  with check ((select public.is_doctor()) and (status <> 'approved' or approved_by = (select auth.uid())));

create policy kb_select on kb_articles for select to authenticated using (review_status = 'approved' or (select public.is_staff()));
create policy kb_insert on kb_articles for insert to authenticated with check ((select public.is_doctor()) and review_status = 'draft');
create policy kb_update on kb_articles for update to authenticated using ((select public.is_doctor()))
  with check ((select public.is_doctor()) and (review_status <> 'approved' or reviewed_by = (select auth.uid())));

create policy care_plans_select on care_plans for select to authenticated using ((patient_id = (select auth.uid()) and status = 'approved') or (select public.is_staff()));
create policy care_plans_insert on care_plans for insert to authenticated with check ((select public.is_doctor()) and doctor_id = (select auth.uid()));
create policy care_plans_update on care_plans for update to authenticated using ((select public.is_doctor()))
  with check ((select public.is_doctor()) and doctor_id = (select auth.uid()));

create policy corrections_select on field_corrections for select to authenticated using ((select public.is_staff()));
create policy corrections_insert on field_corrections for insert to authenticated with check ((select public.is_doctor()) and reviewer = (select auth.uid()) and not training_eligible);
create policy corrections_governance on field_corrections for update to authenticated using ((select public.is_admin())) with check ((select public.is_admin()));

create policy jobs_select on jobs for select to authenticated using (owner_id = (select auth.uid()) or (select public.is_admin()));
create policy settings_select on settings for select to authenticated using (key = 'quote_config' or (select public.is_admin()));
create policy settings_write on settings for insert to authenticated with check (key = 'quote_config' and (select public.is_staff()));
create policy settings_update on settings for update to authenticated using (key = 'quote_config' and (select public.is_staff())) with check (key = 'quote_config' and (select public.is_staff()));

create policy datasets_select on datasets for select to authenticated using ((select public.is_admin()));
create policy dataset_versions_select on dataset_versions for select to authenticated using ((select public.is_admin()));
create policy model_versions_select on model_versions for select to authenticated using ((select public.is_admin()));
create policy audit_select on audit for select to authenticated using ((select public.is_admin()));

-- ---------------------------------------------------------------- E. private storage: read-only for clients, size/type limits on the bucket
update storage.buckets set public = false, file_size_limit = 15728640,
  allowed_mime_types = case id when 'patient-documents' then array['application/pdf','image/jpeg','image/png','text/plain','application/vnd.openxmlformats-officedocument.wordprocessingml.document']
                               else array['image/jpeg','image/png'] end
  where id in ('patient-documents','patient-images');
create policy patient_files_select on storage.objects for select to authenticated
  using (bucket_id in ('patient-documents','patient-images') and ((storage.foldername(name))[1] = (select auth.uid())::text or (select public.is_staff())));
