"use client";

import { ShieldCheck } from "lucide-react";
import { useState } from "react";
import { Note, useToast } from "@/components/ui";
import { post } from "@/lib/api";

export type ConsentStatus = { enforced: boolean; required: boolean; accepted: boolean; available: boolean; notice: { text: string; sha256: string; status: string; approved_by: string } | null };

/** Shown instead of the app until a patient accepts the CURRENT privacy notice. The text is legal content supplied by the clinic; this component never writes or alters it. */
export default function ConsentGate({ status, onDone }: { status: ConsentStatus; onDone: (s: ConsentStatus) => void }) {
  const toast = useToast();
  const [ok, setOk] = useState(false);
  const [busy, setBusy] = useState(false);
  const n = status.notice;
  async function accept() {
    if (!n) return;
    setBusy(true);
    try { onDone(await post<ConsentStatus>("/api/consent/accept", { notice_sha256: n.sha256 })); }
    catch (e) { toast(e instanceof Error ? e.message : "Could not record your acceptance.", "bad"); setBusy(false); }
  }
  return (
    <div className="mx-auto max-w-2xl space-y-4 pt-4" role="region" aria-label="Privacy notice">
      <h1 className="flex items-center gap-2"><ShieldCheck className="h-7 w-7" aria-hidden /> Before you continue</h1>
      <p className="text-muted">Please read how your information is handled. You need to accept this notice before you can upload photos or reports, use the assistant or request a quotation.</p>
      {!status.available || !n || !n.text ? (
        <Note tone="warn">The clinic has not published its privacy notice yet, so these features are not available. Please contact the clinic.</Note>
      ) : (
        <>
          {n.status === "draft" && <Note tone="warn">This is a draft notice shown for testing. It has not been approved and is never offered in a live deployment.</Note>}
          <div className="card max-h-[50dvh] overflow-auto whitespace-pre-wrap" tabIndex={0} role="document" aria-label="Privacy notice text">{n.text}</div>
          {n.approved_by && <p className="text-sm text-muted">Approved by {n.approved_by}.</p>}
          <label className="flex items-start gap-3"><input type="checkbox" className="mt-1 h-5 w-5" checked={ok} onChange={(e) => setOk(e.target.checked)} /><span>I have read this notice and I accept it.</span></label>
          <button className="btn btn-primary w-full !py-3" disabled={!ok || busy} onClick={accept}>Accept and continue</button>
        </>)}
    </div>
  );
}
