-- Model lifecycle: an append-only record of every step a model goes through (imported, evaluated, approved, activated, deactivated, rolled back, deprecated, clinical issues).
-- Administrators and the server write and read it; nobody edits or deletes a row, so the history of which model answered when cannot be rewritten.
create table app.model_events (
  id text primary key,
  model_id text not null,                      -- deliberately no foreign key: the history must outlive (and never block the removal of) a registry row
  task text,
  at timestamptz not null default now(),
  actor text,
  event text not null,
  from_state text, to_state text,
  detail text default '{}'
);
create index model_events_model_ix on app.model_events (model_id, at);

grant select, insert on app.model_events to cosmo_app;
alter table app.model_events enable row level security;
alter table app.model_events force row level security;
create policy model_events_select on app.model_events for select to cosmo_app using ((select app_private.cur_role()) in ('admin', 'system'));
create policy model_events_insert on app.model_events for insert to cosmo_app with check ((select app_private.cur_role()) in ('admin', 'system'));
