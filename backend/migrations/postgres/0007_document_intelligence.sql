-- Document intelligence: provenance / normalised reading / ambiguity for extracted lab fields, page layout and script, typed header facts, and parsed medicine entries.
alter table app.doc_fields add column provenance text default '{}';
alter table app.doc_fields add column normalized text default '{}';
alter table app.doc_fields add column ambiguity text default '[]';
alter table app.doc_pages add column layout text;
alter table app.doc_pages add column script text;

create table app.doc_facts (
  id text primary key,
  document_id text not null references app.documents(id) on delete cascade,
  kind text not null, label text, value text, source_page integer, source_text text, confidence double precision,
  provenance text default '{}', normalized text default '{}', ambiguity text default '[]',
  review_status text default 'unreviewed' check (review_status in ('unreviewed', 'confirmed', 'corrected', 'rejected')), field_no integer default 0
);
create index doc_facts_doc_ix on app.doc_facts (document_id);
create table app.doc_medications (
  id text primary key,
  document_id text not null references app.documents(id) on delete cascade,
  item_no integer, source_page integer, raw text, name text, entry text, ambiguity text default '[]', inferences text default '[]', confidence double precision,
  review_status text default 'unreviewed' check (review_status in ('unreviewed', 'confirmed', 'corrected', 'rejected')), reviewed_by text default '', original text default '', parser text
);
create index doc_medications_doc_ix on app.doc_medications (document_id);

grant select, insert, update, delete on app.doc_facts, app.doc_medications to cosmo_app;
alter table app.doc_facts enable row level security;
alter table app.doc_facts force row level security;
alter table app.doc_medications enable row level security;
alter table app.doc_medications force row level security;
-- same rule as doc_fields: whoever may see the document sees its facts and medicines; the owner, doctors and the system write them
create policy doc_facts_select on app.doc_facts for select to cosmo_app using (exists (select 1 from app.documents d where d.id = document_id));
create policy doc_facts_write on app.doc_facts for all to cosmo_app
  using (exists (select 1 from app.documents d where d.id = document_id and (d.owner_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'system'))))
  with check (exists (select 1 from app.documents d where d.id = document_id and (d.owner_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'system'))));
create policy doc_medications_select on app.doc_medications for select to cosmo_app using (exists (select 1 from app.documents d where d.id = document_id));
create policy doc_medications_write on app.doc_medications for all to cosmo_app
  using (exists (select 1 from app.documents d where d.id = document_id and (d.owner_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'system'))))
  with check (exists (select 1 from app.documents d where d.id = document_id and (d.owner_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('doctor', 'system'))));
