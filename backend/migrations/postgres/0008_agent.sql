-- Agent: auditable traces (what was called and how it ended; never the message, a record or an argument) and proposed, confirmable actions.
create table app.agent_traces (
  id text primary key,
  user_id text not null references app.users(id) on delete cascade,
  request_id text,
  created_at timestamptz not null default now(),
  intent text, provider text, outcome text,
  steps text default '[]', safety text default '{}',
  duration_ms integer, message_sha256 text, message_chars integer
);
create index agent_traces_user_ix on app.agent_traces (user_id, created_at);

create table app.agent_actions (
  id text primary key,
  user_id text not null references app.users(id) on delete cascade,
  trace_id text,
  tool text not null,
  args text not null,
  args_sha256 text not null,
  summary text,
  state text not null check (state in ('proposed', 'executing', 'executed', 'failed', 'cancelled', 'expired')),
  created_at timestamptz not null default now(),
  expires_at timestamptz, decided_at timestamptz,
  result text
);
create index agent_actions_user_ix on app.agent_actions (user_id, state);

grant select, insert, update, delete on app.agent_traces, app.agent_actions to cosmo_app;
alter table app.agent_traces enable row level security;
alter table app.agent_traces force row level security;
alter table app.agent_actions enable row level security;
alter table app.agent_actions force row level security;

-- traces: the person sees their own; administrators and the server see all (they hold no message or record text); nobody edits one; only the server deletes (retention, erasure)
create policy agent_traces_select on app.agent_traces for select to cosmo_app using (user_id = (select app_private.cur_user()) or (select app_private.cur_role()) in ('admin', 'system'));
create policy agent_traces_insert on app.agent_traces for insert to cosmo_app with check ((user_id = (select app_private.cur_user()) and (select app_private.cur_role()) = 'patient') or (select app_private.is_system()));
create policy agent_traces_delete on app.agent_traces for delete to cosmo_app using ((select app_private.is_system()));

-- actions: only the person they belong to (and the server) can see or move them; administrators and clinicians cannot, because the arguments are the person's own words
create policy agent_actions_select on app.agent_actions for select to cosmo_app using (user_id = (select app_private.cur_user()) or (select app_private.is_system()));
create policy agent_actions_insert on app.agent_actions for insert to cosmo_app with check ((user_id = (select app_private.cur_user()) and (select app_private.cur_role()) = 'patient') or (select app_private.is_system()));
create policy agent_actions_update on app.agent_actions for update to cosmo_app using (user_id = (select app_private.cur_user()) or (select app_private.is_system()))
  with check (user_id = (select app_private.cur_user()) or (select app_private.is_system()));
create policy agent_actions_delete on app.agent_actions for delete to cosmo_app using ((select app_private.is_system()));
