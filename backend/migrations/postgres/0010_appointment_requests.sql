-- Appointment requests. A request is NOT a booking: only a member of the clinic's staff can record that a time was agreed (confirmed_for, in their own words).
-- A patient sees and changes only their own requests; the clinic's doctors and the server see and answer all of them; administrators have no access unless clinical reading is allowed.
create table app.appointment_requests (
  id text primary key,
  patient_id text not null references app.users(id) on delete cascade,
  service_id text, service_name text,
  reason text not null,
  preferred text,
  contact_method text not null check (contact_method in ('app', 'phone', 'email')),
  contact_detail text,
  analysis_id text,
  share_summary integer default 0,
  urgency text not null default 'routine' check (urgency in ('routine', 'urgent')),
  status text not null default 'requested' check (status in ('requested', 'contacted', 'confirmed', 'declined', 'cancelled', 'closed')),
  confirmed_for text, staff_note text, decided_by text,
  created_at timestamptz, updated_at timestamptz
);
create index ix_appt_patient on app.appointment_requests (patient_id, created_at);
create index ix_appt_status on app.appointment_requests (status, created_at);

grant select, insert, update, delete on app.appointment_requests to cosmo_app;
alter table app.appointment_requests enable row level security;
alter table app.appointment_requests force row level security;

create policy appointment_requests_select on app.appointment_requests for select to cosmo_app
  using (patient_id = (select app_private.cur_user()) or (select app_private.clinical_reader()));
create policy appointment_requests_insert on app.appointment_requests for insert to cosmo_app
  with check ((patient_id = (select app_private.cur_user()) and (select app_private.cur_role()) = 'patient' and status = 'requested') or (select app_private.is_system()));
-- A patient can only withdraw their own request: the row they leave behind is 'requested' or 'cancelled' and carries no agreed time. Agreeing a time is the clinic's act.
create policy appointment_requests_update on app.appointment_requests for update to cosmo_app
  using ((patient_id = (select app_private.cur_user()) and (select app_private.cur_role()) = 'patient') or (select app_private.cur_role()) in ('doctor', 'system'))
  with check ((patient_id = (select app_private.cur_user()) and (select app_private.cur_role()) = 'patient' and status in ('requested', 'cancelled') and coalesce(confirmed_for, '') = '')
              or (select app_private.cur_role()) in ('doctor', 'system'));
create policy appointment_requests_delete on app.appointment_requests for delete to cosmo_app using ((select app_private.is_system()));
