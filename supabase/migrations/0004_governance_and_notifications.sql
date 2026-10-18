-- 0004: bring the Postgres schema level with application changes made after 0003.
--  A. knowledge-base governance: approval is an attested, content-bound act (reviewer, time, source note, SHA-256 of the exact text, attestation) and ANY change to an
--     approved article's content returns it to draft in the database itself (a client cannot edit approved text and keep the approval).
--  B. in-app notifications: private to the recipient; only the server (service role) creates them.
-- Not covered here (the application keeps these in SQLite): users.disabled, the job queue and the model-training checkpoints. Account disabling in a Supabase
-- deployment is done in Supabase Auth (ban user); the backend refuses tokens of users whose profile is missing.

-- ---------------------------------------------------------------- A. knowledge base
alter table kb_articles add column if not exists reviewed_at timestamptz;
alter table kb_articles add column if not exists content_sha256 text not null default '';
alter table kb_articles add column if not exists attestation jsonb not null default '{}'::jsonb;
alter table kb_articles add column if not exists source_note text not null default '';
alter table kb_articles add column if not exists content_origin text not null default 'clinic';
alter table kb_articles add column if not exists claims_to_verify jsonb not null default '[]'::jsonb;
alter table kb_articles drop constraint if exists kb_origin_ck;
alter table kb_articles add constraint kb_origin_ck check (content_origin in ('clinic', 'ai_draft'));

-- An approved row must carry the whole approval record. (Rows inserted by the database owner before this migration are checked only on update: NOT VALID.)
alter table kb_articles drop constraint if exists kb_approved_record_ck;
alter table kb_articles add constraint kb_approved_record_ck check
  (review_status <> 'approved' or (reviewed_by is not null and reviewed_at is not null and content_sha256 <> '' and length(source_note) >= 10 and attestation <> '{}'::jsonb)) not valid;

drop policy if exists kb_update on kb_articles;
create policy kb_update on kb_articles for update to authenticated using ((select private.is_doctor()))
  with check ((select private.is_doctor()) and (review_status <> 'approved' or (reviewed_by = (select auth.uid()) and content_sha256 <> '' and length(source_note) >= 10 and attestation <> '{}'::jsonb)));
drop policy if exists kb_insert on kb_articles;
create policy kb_insert on kb_articles for insert to authenticated with check ((select private.is_doctor()) and review_status = 'draft' and content_sha256 = '');

-- Content change => back to draft, version bumped, approval record cleared. Runs for every role except the database owner/service role doing maintenance.
create or replace function private.kb_content_change_withdraws_approval() returns trigger language plpgsql security invoker set search_path = '' as $$
begin
  if (new.title is distinct from old.title or new.body is distinct from old.body or new.language is distinct from old.language) then
    if old.review_status = 'approved' or new.review_status = 'approved' then
      new.review_status := 'draft';
      new.reviewed_by := null;
      new.reviewed_at := null;
      new.content_sha256 := '';
      new.attestation := '{}'::jsonb;
      new.source_note := '';
    end if;
    new.version := coalesce(old.version, 1) + 1;
  end if;
  return new;
end $$;
revoke all on function private.kb_content_change_withdraws_approval() from public, anon;
drop trigger if exists kb_content_change on kb_articles;
create trigger kb_content_change before update on kb_articles for each row execute function private.kb_content_change_withdraws_approval();

-- ---------------------------------------------------------------- B. notifications
create table if not exists notifications (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references profiles(id) on delete cascade,
  kind text not null,
  title text not null check (length(title) <= 140),
  body text not null default '' check (length(body) <= 300),
  link text not null default '' check (length(link) <= 200),
  dedupe_key text not null default '',
  created_at timestamptz not null default now(),
  read_at timestamptz
);
create unique index if not exists notifications_dedupe_ux on notifications(user_id, dedupe_key) where dedupe_key <> '';
create index if not exists notifications_user_created_ix on notifications(user_id, created_at desc);

alter table notifications enable row level security;
revoke all on notifications from anon, authenticated;
grant select on notifications to authenticated;
grant update (read_at) on notifications to authenticated;                    -- a recipient may mark their own notification read, nothing else
grant all on notifications to service_role;
drop policy if exists notifications_select_own on notifications;
create policy notifications_select_own on notifications for select to authenticated using (user_id = (select auth.uid()));
drop policy if exists notifications_update_own on notifications;
create policy notifications_update_own on notifications for update to authenticated using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
-- no insert/delete policy for authenticated: notifications are created by the backend only
