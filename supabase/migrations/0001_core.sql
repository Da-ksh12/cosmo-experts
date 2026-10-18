-- Cosmo Experts AI: core schema with strict Row Level Security.
-- Roles live in profiles.role (patient | doctor | admin). The FastAPI service uses the service-role key
-- server-side only and still enforces authorization itself; RLS protects any direct client access.

create table profiles (
  id uuid primary key references auth.users on delete cascade,
  full_name text,
  role text not null default 'patient' check (role in ('patient','doctor','admin')),
  created_at timestamptz default now()
);

create function public.my_role() returns text language sql stable security definer set search_path = public as
$$ select role from profiles where id = auth.uid() $$;

create table documents (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references profiles(id) on delete cascade,
  filename text, storage_path text, mime text, sha256 text,
  doc_type text, doc_date date, page_count int, status text,
  processing_version text, review_status text default 'pending_clinician_review',
  created_at timestamptz default now()
);
create table doc_pages (id uuid primary key default gen_random_uuid(), document_id uuid references documents on delete cascade,
  page_no int, text text, method text, ocr_confidence real);
create table doc_fields (id uuid primary key default gen_random_uuid(), document_id uuid references documents on delete cascade,
  name text, value text, unit text, ref_range text, source_page int, source_text text, confidence real, flag text,
  review_status text default 'unreviewed', corrected_by uuid, processing_version text);
create table images (id uuid primary key default gen_random_uuid(), owner_id uuid not null references profiles(id) on delete cascade,
  region text, storage_path text, verdict text, report jsonb, created_at timestamptz default now());
create table conversations (id uuid primary key default gen_random_uuid(), owner_id uuid not null references profiles(id) on delete cascade,
  created_at timestamptz default now());
create table messages (id uuid primary key default gen_random_uuid(), conversation_id uuid references conversations on delete cascade,
  role text, content text, sources jsonb, created_at timestamptz default now());
create table price_items (id uuid primary key default gen_random_uuid(), name text, description text, unit_price numeric(12,2),
  currency text, active boolean default true, approved_by uuid references profiles(id));
create table quotes (id uuid primary key default gen_random_uuid(), patient_id uuid references profiles(id), status text default 'draft',
  lines jsonb, total numeric(12,2), currency text, notes text, created_by uuid, approved_by uuid,
  created_at timestamptz default now(), updated_at timestamptz default now());
create table kb_articles (id uuid primary key default gen_random_uuid(), title text, body text, language text,
  review_status text default 'draft', reviewed_by uuid references profiles(id), version int default 1);
create table datasets (id text primary key, data jsonb, approval_status text default 'unreviewed', updated_at timestamptz default now());
create table dataset_versions (id uuid primary key default gen_random_uuid(), dataset_id text references datasets, manifest jsonb, created_at timestamptz default now());
create table model_versions (id uuid primary key default gen_random_uuid(), task text, data jsonb, status text default 'research_only', created_at timestamptz default now());
create table audit (id uuid primary key default gen_random_uuid(), ts timestamptz default now(), actor text, action text, target text, detail jsonb);

-- Row Level Security on everything
do $$ declare t text; begin
  for t in select unnest(array['profiles','documents','doc_pages','doc_fields','images','conversations','messages','price_items','quotes',
                               'kb_articles','datasets','dataset_versions','model_versions','audit']) loop
    execute format('alter table %I enable row level security', t);
  end loop; end $$;

create policy profiles_self on profiles for select using (id = auth.uid() or my_role() in ('doctor','admin'));
create policy docs_owner on documents for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy docs_staff_read on documents for select using (my_role() in ('doctor','admin'));
create policy pages_owner on doc_pages for select using (exists (select 1 from documents d where d.id = document_id and (d.owner_id = auth.uid() or my_role() in ('doctor','admin'))));
create policy fields_owner on doc_fields for select using (exists (select 1 from documents d where d.id = document_id and (d.owner_id = auth.uid() or my_role() in ('doctor','admin'))));
create policy fields_doctor_update on doc_fields for update using (my_role() = 'doctor');
create policy images_owner on images for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy images_staff_read on images for select using (my_role() in ('doctor','admin'));
create policy conv_owner on conversations for all using (owner_id = auth.uid()) with check (owner_id = auth.uid());
create policy msg_owner on messages for all using (exists (select 1 from conversations c where c.id = conversation_id and c.owner_id = auth.uid()));
create policy price_read on price_items for select using (active and approved_by is not null);
create policy price_staff on price_items for all using (my_role() = 'doctor');
create policy quotes_owner_read on quotes for select using (patient_id = auth.uid() or my_role() in ('doctor','admin'));
create policy quotes_patient_insert on quotes for insert with check (patient_id = auth.uid() and status = 'draft');
create policy quotes_doctor on quotes for update using (my_role() = 'doctor');
create policy kb_read_approved on kb_articles for select using (review_status = 'approved' or my_role() = 'doctor');
create policy kb_doctor on kb_articles for all using (my_role() = 'doctor');
create policy admin_only_datasets on datasets for all using (my_role() = 'admin');
create policy admin_only_dsv on dataset_versions for all using (my_role() = 'admin');
create policy admin_only_models on model_versions for all using (my_role() = 'admin');
create policy admin_read_audit on audit for select using (my_role() = 'admin');   -- inserts only via service role

-- Private storage buckets; objects are keyed <user_id>/<file>
insert into storage.buckets (id, name, public) values ('patient-documents','patient-documents', false), ('patient-images','patient-images', false)
  on conflict do nothing;
create policy storage_owner on storage.objects for all
  using (bucket_id in ('patient-documents','patient-images') and (storage.foldername(name))[1] = auth.uid()::text)
  with check (bucket_id in ('patient-documents','patient-images') and (storage.foldername(name))[1] = auth.uid()::text);
create policy storage_staff_read on storage.objects for select
  using (bucket_id in ('patient-documents','patient-images') and public.my_role() in ('doctor','admin'));
