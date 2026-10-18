-- Versioned, append-only consent history. The current state is derived from the latest event; nothing is updated or deleted by the application.
-- notice_status records whether the text the person saw was an APPROVED notice or only a draft, as determined by the server (never supplied by the client).
create table app.consent_events (
  id text primary key,
  user_id text not null references app.users(id) on delete cascade,
  notice_sha256 text not null,
  notice_status text not null check (notice_status in ('approved', 'draft')),
  action text not null check (action in ('accepted', 'withdrawn')),
  at timestamptz not null,
  source text not null default 'web'
);
create index consent_events_user_ix on app.consent_events (user_id, at desc);

grant select, insert on app.consent_events to cosmo_app;
alter table app.consent_events enable row level security;
alter table app.consent_events force row level security;
create policy consent_events_select on app.consent_events for select to cosmo_app using (user_id = (select app_private.cur_user()) or (select app_private.is_system()));
create policy consent_events_insert on app.consent_events for insert to cosmo_app with check (user_id = (select app_private.cur_user()) or (select app_private.is_system()));
