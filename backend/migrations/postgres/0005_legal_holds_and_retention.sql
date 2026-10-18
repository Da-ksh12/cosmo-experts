-- Legal holds suspend retention purges and patient erasure for one patient. Reason codes are fixed so that no clinical or legal narrative is stored here.
create table app.legal_holds (
  id text primary key,
  patient_id text not null,
  reason_code text not null check (reason_code in ('legal_proceeding', 'regulatory_inquiry', 'clinical_safety_review', 'other')),
  placed_by text not null,
  placed_at timestamptz not null,
  released_by text,
  released_at timestamptz
);
create index legal_holds_active_ix on app.legal_holds (patient_id) where released_at is null;
grant select, insert, update on app.legal_holds to cosmo_app;                       -- never deleted: the history of holds is itself a record
alter table app.legal_holds enable row level security;
alter table app.legal_holds force row level security;
create policy legal_holds_select on app.legal_holds for select to cosmo_app using ((select app_private.cur_role()) in ('admin', 'system'));
create policy legal_holds_insert on app.legal_holds for insert to cosmo_app with check ((select app_private.cur_role()) in ('admin', 'system'));
create policy legal_holds_update on app.legal_holds for update to cosmo_app using ((select app_private.cur_role()) in ('admin', 'system'))
  with check ((select app_private.cur_role()) in ('admin', 'system'));

-- The audit trail is append-only for the application role. Retention may remove OLD entries only through this function, which works for the system identity alone,
-- in bounded batches. (The application role still has no UPDATE/DELETE/TRUNCATE privilege on app.audit.)
create function app.purge_audit(cutoff timestamptz, max_rows integer) returns integer
language plpgsql security definer set search_path = pg_catalog, app as $$
declare n integer;
begin
  if not app_private.is_system() then raise exception 'not permitted' using errcode = '42501'; end if;
  if max_rows < 1 or max_rows > 10000 then raise exception 'batch size out of range' using errcode = '22023'; end if;
  with doomed as (select id from app.audit where ts < cutoff order by ts limit max_rows)
  delete from app.audit a using doomed where a.id = doomed.id;
  get diagnostics n = row_count;
  return n;
end $$;
revoke all on function app.purge_audit(timestamptz, integer) from public;
grant execute on function app.purge_audit(timestamptz, integer) to cosmo_app;
