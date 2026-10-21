"use client";

import { useEffect, useState } from "react";
import { Details, ErrorState, Note, Pill } from "@/components/ui";
import { api } from "@/lib/api";

type Source = { id: string; publisher: string; title: string; url: string; page_reviewed?: string | null; next_review_due?: string | null; source_review_overdue?: boolean; terms: string };
type Article = { id: string; title: string; topic: string; version: number; tier: string; review_status: string; edited_by_clinician: boolean; claims_to_verify: string[]; sources: Source[]; statements: number };
type Rule = { id: string; level: string; trigger: string; reason_shown: string; article: string | null; sources: string[]; status: string };
type Lib =
  | { usable: false; problem: string }
  | { usable: true; status: { reference_mode: string; shown_to_patients: number; approved: number; awaiting_review: number }; pack: { version: number | string; sha256: string; checked_on?: string; checked_by?: string };
      articles: Article[]; triage_rules: Rule[]; policy: { version: number | string; rules: number; status: string; emergency_message_source: string; emergency_message_approved: boolean } };

const TIER: Record<string, { label: string; tone: "good" | "warn" | "info" | "bad" }> = {
  approved: { label: "Approved by the clinic", tone: "good" }, reference: { label: "Shown with a 'not reviewed yet' label", tone: "warn" }, not_shown: { label: "Not shown to patients", tone: "info" },
};
const LEVEL_TONE: Record<string, "bad" | "warn" | "info"> = { emergency: "bad", urgent: "warn", prompt: "info" };

/** The state of the dermatology library as the clinic must review it: what patients see now, what each article rests on, and the warning-sign rules that await a clinician's review.
 *  Approving an article is done in the knowledge-base section, where the exact text is attested. */
export default function DermLibraryPanel() {
  const [lib, setLib] = useState<Lib | null>(null);
  const [err, setErr] = useState("");
  useEffect(() => { api<Lib>("/api/clinic/derm-library").then(setLib).catch((e) => setErr(e instanceof Error ? e.message : "We couldn't load the library status.")); }, []);

  return (
    <Details title="Dermatology library and warning-sign rules">
      {err ? <ErrorState message={err} /> : !lib ? <p className="text-muted" role="status">Loading…</p> : !lib.usable ? <Note tone="bad"><p>The library cannot be used: {lib.problem}</p></Note> : (
        <div className="space-y-4" data-derm-library>
          <p className="text-sm">{lib.articles.length} articles · <b>{lib.status.approved}</b> approved by the clinic · <b>{lib.status.awaiting_review}</b> shown with a &quot;not reviewed yet&quot; label (reference mode: {lib.status.reference_mode}). {lib.status.shown_to_patients === 0 ? "Patients currently see no library answers." : ""}</p>
          <Note tone="gold"><p>Every article is a draft until a clinician approves it in <b>Clinically reviewed knowledge base</b> (below). Check the statements and sources listed here first. Approval applies to the exact text and lapses on the re-review date.</p></Note>

          <div className="space-y-1">
            <h4 className="font-semibold">Warning-sign rules</h4>
            <p className="text-sm text-muted">{lib.policy.rules} rules (version {lib.policy.version}) · status: {lib.policy.status.replaceAll("_", " ")} · emergency wording comes from the clinic&apos;s urgent-symptom policy ({lib.policy.emergency_message_source}{lib.policy.emergency_message_approved ? ", approved" : ", not yet approved"}).</p>
            <Details title={`Show the ${lib.triage_rules.length} rules`}>
              <ul className="space-y-2 text-sm">{lib.triage_rules.map((r) => (
                <li key={r.id} className="space-y-0.5"><span className="mr-2"><Pill tone={LEVEL_TONE[r.level] ?? "info"}>{r.level}</Pill></span><b>{r.id}</b><br /><span className="text-muted">When: {r.trigger}</span><br /><span>Shown: {r.reason_shown}</span></li>))}</ul>
            </Details>
          </div>

          <div className="space-y-2">
            <h4 className="font-semibold">Articles</h4>
            <ul className="space-y-2">{lib.articles.map((a) => {
              const t = TIER[a.tier] ?? TIER.not_shown;
              return (
                <li key={a.id} className="rounded-2xl border border-line bg-white p-3 text-sm">
                  <div className="flex flex-wrap items-center gap-2"><b>{a.title}</b><Pill tone={t.tone}>{t.label}</Pill>{a.edited_by_clinician && <Pill tone="info">Edited by a clinician</Pill>}
                    {a.sources.some((s) => s.source_review_overdue) && <Pill tone="warn">Source check overdue</Pill>}</div>
                  <p className="text-muted">{a.statements} statements · {a.claims_to_verify.length} to verify before approval · {a.sources.length} sources</p>
                  <Details title="Sources and statements to verify">
                    <ul className="list-disc space-y-1 pl-5">{a.sources.map((s) => <li key={s.id}><a className="underline" href={s.url} target="_blank" rel="noopener noreferrer">{s.publisher}: {s.title}</a><span className="text-muted"> · page checked {s.page_reviewed ?? "?"} · reuse: {s.terms.replaceAll("_", " ")}</span></li>)}</ul>
                    {a.claims_to_verify.length > 0 && <><p className="mt-2 font-semibold">To verify</p><ul className="list-disc space-y-1 pl-5">{a.claims_to_verify.map((c) => <li key={c}>{c}</li>)}</ul></>}
                  </Details>
                </li>);
            })}</ul>
          </div>
          <p className="text-xs text-muted">Pack version {lib.pack.version} · fingerprint {lib.pack.sha256.slice(0, 12)}{lib.pack.checked_on ? ` · sources checked ${lib.pack.checked_on}` : ""}</p>
        </div>)}
    </Details>
  );
}
