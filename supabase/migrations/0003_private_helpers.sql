-- 0003: keep the RLS helper functions out of the API surface. Supabase's advisor flagged public.my_role/is_doctor/is_staff/is_admin as callable
-- through /rest/v1/rpc/*. They only reveal the caller's own role, but there is no reason to expose them. Functions in a non-exposed schema are still
-- usable inside policies (policy expressions reference the function, not its URL).
create schema if not exists private;
revoke all on schema private from public, anon;
grant usage on schema private to authenticated, service_role;
alter function public.my_role()   set schema private;
alter function public.is_doctor() set schema private;
alter function public.is_staff()  set schema private;
alter function public.is_admin()  set schema private;
