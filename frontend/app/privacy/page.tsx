"use client";

import { ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { ConsentStatus } from "@/components/ConsentGate";
import { ScreenBar } from "@/components/Shell";
import { ErrorState, LoadingState, Note, PageHeading, fmtDateTime, useToast } from "@/components/ui";
import { api, post } from "@/lib/api";

type Event = { notice_sha256: string; notice_status: string; action: "accepted" | "withdrawn"; at: number };

/** Shows the patient their own consent state and history, and lets them withdraw. The notice text itself is shown by the consent gate; nothing here writes or edits legal text. */
export default function PrivacyPage() {
  const toast = useToast();
  const [status, setStatus] = useState<(ConsentStatus & { withdrawn?: boolean; not_covered?: string[] }) | null>(null);
  const [events, setEvents] = useState<Event[] | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => {
    Promise.all([api<ConsentStatus & { withdrawn?: boolean; not_covered?: string[] }>("/api/consent"), api<{ events: Event[] }>("/api/consent/history")])
      .then(([s, h]) => { setErr(""); setStatus(s); setEvents(h.events); })
      .catch((e) => setErr(e instanceof Error ? e.message : "Could not load your privacy settings."));
  }, []);
  useEffect(load, [load]);
  async function withdraw() {
    if (!window.confirm("Withdraw your consent? You will not be able to upload photos or reports, use the assistant or request a quotation until you accept the notice again. Information you already shared stays on your record until you ask the clinic to delete it.")) return;
    setBusy(true);
    try { await post("/api/consent/withdraw"); toast("Your consent has been withdrawn.", "ok"); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Could not withdraw your consent.", "bad"); }
    setBusy(false);
  }
  return (
    <div className="space-y-5">
      <ScreenBar />
      <PageHeading eyebrow="Your information" title="Privacy and consent" subtitle="What you agreed to, and when." art={false} />
      {err ? <ErrorState message={err} onRetry={load} /> : !status || !events ? <LoadingState /> : (
        <>
          <div className="card flex items-start gap-3">
            <ShieldCheck className="mt-1 h-6 w-6 shrink-0 text-teal-dark" aria-hidden />
            <div>
              <p className="font-semibold" data-testid="consent-state">{status.accepted ? "You have accepted the current privacy notice." : status.withdrawn ? "You have withdrawn your consent." : status.enforced ? "You have not accepted the current privacy notice yet." : "Consent is not required in this environment."}</p>
              <p className="text-sm text-muted">Your information is never used to train AI models.</p>
            </div>
          </div>
          {status.accepted && <button className="btn w-full" disabled={busy} onClick={withdraw}>Withdraw my consent</button>}
          {!status.accepted && status.enforced && <Note tone="warn">Open any feature, such as Ask AI, to read and accept the notice.</Note>}
          <section aria-label="Consent history">
            <h2 className="mb-2 text-lg">History</h2>
            {events.length === 0 ? <p className="text-muted">No decisions recorded yet.</p> : (
              <ul className="space-y-2">
                {events.map((e, i) => (
                  <li key={i} className="card !py-3"><span className="font-semibold">{e.action === "accepted" ? "Accepted" : "Withdrawn"}</span> · {fmtDateTime(e.at)}
                    <span className="block text-xs text-muted">Notice {e.notice_sha256.slice(0, 8)}{e.notice_status === "draft" ? " (draft, for testing)" : ""}</span></li>
                ))}
              </ul>)}
          </section>
        </>)}
    </div>
  );
}
