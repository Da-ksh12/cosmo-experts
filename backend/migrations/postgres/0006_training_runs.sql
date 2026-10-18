-- Persisted record of every training run: configuration (hashed), resource plan, status, resulting model. Administrators and the system only; never deleted by the application.
create table app.training_runs (
  id text primary key,
  task text, dataset_id text, version_id text,
  config text not null, config_hash text not null, plan text,
  status text not null check (status in ('running', 'done', 'failed', 'stopped', 'interrupted')),
  started_at timestamptz not null, finished_at timestamptz,
  model_id text, error text, actor text, device text
);
create index training_runs_started_ix on app.training_runs (started_at desc);
grant select, insert, update on app.training_runs to cosmo_app;
alter table app.training_runs enable row level security;
alter table app.training_runs force row level security;
create policy training_runs_select on app.training_runs for select to cosmo_app using ((select app_private.cur_role()) in ('admin', 'system'));
create policy training_runs_insert on app.training_runs for insert to cosmo_app with check ((select app_private.cur_role()) in ('admin', 'system'));
create policy training_runs_update on app.training_runs for update to cosmo_app using ((select app_private.cur_role()) in ('admin', 'system')) with check ((select app_private.cur_role()) in ('admin', 'system'));
