-- 0005: scheduled expiry. Quotations carry a validity date set when a doctor approves them; knowledge-base approvals carry a re-review date after which the
-- assistant stops using the article (the application enforces both; the columns keep the Postgres schema level with the application).
alter table quotes add column if not exists valid_until timestamptz;
alter table kb_articles add column if not exists review_due timestamptz;
-- doctors may set the validity date together with the approval (column-level grant, as for the other approval fields)
grant update (status, approved_by, lines, total, subtotal, tax_percent, tax_amount, currency, notes, updated_at, valid_until) on quotes to authenticated;
-- editing an approved article already withdraws approval (0004 trigger); the due date must be cleared with it
create or replace function private.kb_content_change_withdraws_approval() returns trigger language plpgsql security invoker set search_path = '' as $$
begin
  if (new.title is distinct from old.title or new.body is distinct from old.body or new.language is distinct from old.language) then
    if old.review_status = 'approved' or new.review_status = 'approved' then
      new.review_status := 'draft';
      new.reviewed_by := null;
      new.reviewed_at := null;
      new.review_due := null;
      new.content_sha256 := '';
      new.attestation := '{}'::jsonb;
      new.source_note := '';
    end if;
    new.version := coalesce(old.version, 1) + 1;
  end if;
  return new;
end $$;
