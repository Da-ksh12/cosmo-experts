-- 0001: application schema (PostgreSQL). Mirrors the SQLite model column-for-column and in the same order (the code uses positional INSERTs in places);
-- tests/test_pg_schema_parity.py checks that. Deliberate type mapping, documented in docs/DATABASE_MIGRATION.md:
--   TEXT identifiers stay text (the application's stable ids are 32-hex strings and, for Supabase users, auth user ids); REAL measurements -> double precision;
--   every time column -> timestamptz (timezone-aware; the Python side keeps seeing Unix-second floats through the Epoch type); INTEGER flags stay integer;
--   JSON payloads stay text (the application serialises them), so semantics are unchanged.
-- Roles: this file is applied by the OWNER role (scripts/migrate.py with COSMO_MIGRATION_DATABASE_URL). The running application connects as cosmo_app, which has
-- only DML on these tables (and, from 0002 on, row-level security applies to it). cosmo_app is created here WITHOUT a login: the operator sets its credential.

create schema if not exists app;

do $$ begin
  if not exists (select 1 from pg_roles where rolname = 'cosmo_app') then
    create role cosmo_app nologin nosuperuser nocreatedb nocreaterole noinherit nobypassrls;
  end if;
end $$;
alter role cosmo_app set search_path = app;
grant usage on schema app to cosmo_app;

-- ---------------------------------------------------------------- identity mirror and settings
create table app.users (
  id text primary key,
  name text,
  role text not null check (role in ('patient', 'doctor', 'admin')),
  password_hash text,
  credentials text default '',
  verified integer default 0,
  disabled integer default 0
);

create table app.settings (key text primary key, value text);

-- ---------------------------------------------------------------- documents
create table app.documents (
  id text primary key,
  owner_id text not null references app.users(id) on delete cascade,
  filename text, stored_path text, mime text, sha256 text, doc_type text, doc_date text,
  page_count integer, status text, processing_version text, review_status text, language text, error text,
  created_at timestamptz,
  deleted integer default 0,
  size_bytes bigint default 0,
  user_category text default ''
);
create table app.doc_pages (
  id text primary key,
  document_id text not null references app.documents(id) on delete cascade,
  page_no integer, text text, method text, ocr_confidence double precision
);
create table app.doc_fields (
  id text primary key,
  document_id text not null references app.documents(id) on delete cascade,
  name text, value text, unit text, ref_range text, source_page integer, source_text text, confidence double precision, flag text,
  review_status text, corrected_by text, processing_version text,
  patient_flag text default '',
  field_no integer default 0                       -- extraction order within the document (replaces SQLite's implicit rowid tie-break)
);

create table app.images (
  id text primary key,
  owner_id text not null references app.users(id) on delete cascade,
  region text, stored_path text, verdict text, report text,
  created_at timestamptz,
  deleted integer default 0,
  clinician_review text default '', reviewed_by text default '',
  reviewed_at timestamptz
);

-- ---------------------------------------------------------------- conversations
create table app.conversations (
  id text primary key,
  owner_id text not null references app.users(id) on delete cascade,
  created_at timestamptz
);
create table app.messages (
  id text primary key,
  conversation_id text not null references app.conversations(id) on delete cascade,
  role text, content text, sources text,
  created_at timestamptz
);

-- ---------------------------------------------------------------- quotations
create table app.price_items (
  id text primary key,
  name text, description text, unit_price double precision, currency text, active integer, approved_by text,
  category text default 'General', sessions integer default 1, duration_min integer default 0, compare_at_price double precision
);
create table app.quotes (
  id text primary key,
  patient_id text not null references app.users(id) on delete cascade,
  status text not null check (status in ('draft', 'approved')),
  lines text, total double precision, currency text, notes text, created_by text, approved_by text,
  created_at timestamptz, updated_at timestamptz, valid_until timestamptz,
  subtotal double precision default 0, tax_percent double precision default 0, tax_amount double precision default 0
);

-- ---------------------------------------------------------------- knowledge base
create table app.kb_articles (
  id text primary key,
  title text, body text, language text,
  review_status text not null check (review_status in ('draft', 'approved')),
  reviewed_by text, version integer,
  reviewed_at timestamptz, review_due timestamptz,
  content_sha256 text default '', attestation text default '', source_note text default '', content_origin text default 'clinic', claims_to_verify text default '[]'
);

-- ---------------------------------------------------------------- datasets and models (administrator)
create table app.datasets (id text primary key, data text, approval_status text, updated_at timestamptz);
create table app.dataset_versions (
  id text primary key,
  dataset_id text not null,                          -- no foreign key: the importer also records versions for datasets that are not catalogue entries (SQLite never enforced one)
  manifest text, created_at timestamptz
);
create table app.model_versions (id text primary key, task text, data text, status text, created_at timestamptz);

-- ---------------------------------------------------------------- audit (append-only by privilege: no UPDATE/DELETE grant, see below)
create table app.audit (id text primary key, ts timestamptz, actor text, action text, target text, detail text);

-- ---------------------------------------------------------------- clinical workflow
create table app.field_corrections (
  id text primary key, field_id text, document_id text, before text, after text, status text, reviewer text,
  ts timestamptz, processing_version text,
  training_eligible integer default 0, governance_ref text default '', deidentified integer default 0
);
create table app.care_plans (
  id text primary key,
  patient_id text not null references app.users(id) on delete cascade,
  doctor_id text,
  status text not null check (status in ('draft', 'approved', 'superseded')),
  assessment text, findings text, stages text, notes text, recommended_item_ids text,
  reviewed_at timestamptz, updated_at timestamptz
);

-- ---------------------------------------------------------------- privacy, notifications, jobs
create table app.consents (
  user_id text not null references app.users(id) on delete cascade,
  notice_sha256 text not null,
  accepted_at timestamptz,
  primary key (user_id, notice_sha256)
);
create table app.notifications (
  id text primary key,
  user_id text not null references app.users(id) on delete cascade,
  kind text, title text, body text, link text, dedupe_key text default '',
  created_at timestamptz, read_at timestamptz
);
create table app.jobs (
  id text primary key,
  owner_id text, task text, status text, request_id text, idempotency_key text, params text, input_path text, result text, error text,
  created_at timestamptz, started_at timestamptz, finished_at timestamptz,
  cancel_requested integer default 0
);

-- ---------------------------------------------------------------- indexes for the real access patterns
create index documents_owner_created_ix on app.documents (owner_id, created_at desc) where deleted = 0;
create index doc_pages_doc_ix on app.doc_pages (document_id, page_no);
create index doc_fields_doc_ix on app.doc_fields (document_id);
create index images_owner_created_ix on app.images (owner_id, created_at desc) where deleted = 0;
create index conversations_owner_ix on app.conversations (owner_id, created_at desc);
create index messages_conv_created_ix on app.messages (conversation_id, created_at);
create index quotes_patient_created_ix on app.quotes (patient_id, created_at desc);
create index care_plans_patient_status_ix on app.care_plans (patient_id, status);
create index field_corrections_doc_ix on app.field_corrections (document_id);
create index dataset_versions_ds_ix on app.dataset_versions (dataset_id);
create index model_versions_task_ix on app.model_versions (task, created_at desc);
create index audit_ts_ix on app.audit (ts desc);
create index audit_actor_ts_ix on app.audit (actor, ts desc);
create index audit_action_ts_ix on app.audit (action, ts desc);
create index audit_target_ix on app.audit (target);
create unique index ux_notifications_dedupe on app.notifications (user_id, dedupe_key) where dedupe_key <> '';
create index ix_notifications_user on app.notifications (user_id, created_at desc);
create index jobs_owner on app.jobs (owner_id, created_at);
create unique index jobs_idem on app.jobs (owner_id, idempotency_key) where idempotency_key is not null;

-- ---------------------------------------------------------------- privileges for the application role
grant select, insert, update, delete on all tables in schema app to cosmo_app;
revoke update, delete, truncate on app.audit from cosmo_app;               -- the audit trail is append-only for the application
