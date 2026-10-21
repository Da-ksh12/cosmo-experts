"use client";

import { CalendarCheck, Check, FileText, Phone, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { Dialog, EmptyState, ErrorState, LoadingState, Note, Pill, fmtDateTime, useToast } from "@/components/ui";
import { api, ApiError, post } from "@/lib/api";

type QReq = {
  id: string; status: string; service_name: string | null; reason: string; preferred: string; contact_method: string; contact_detail: string; urgency: string;
  analysis_id: string | null; share_summary: boolean; confirmed_for: string | null; staff_note: string; created_at: number; patient: string | null; patient_id: string;
};
type Verb = "contacted" | "confirmed" | "declined" | "closed";

const TONE: Record<string, "info" | "good" | "warn" | "bad"> = { requested: "warn", contacted: "info", confirmed: "good", declined: "bad", cancelled: "info", closed: "info" };
const LABEL: Record<string, string> = { requested: "New", contacted: "Contacted", confirmed: "Confirmed", declined: "Declined", cancelled: "Cancelled by patient", closed: "Closed" };
const VERB_TITLE: Record<Verb, string> = { contacted: "Mark as contacted", confirmed: "Confirm a time", declined: "Decline this request", closed: "Close this request" };
const FILTERS = [["open", "Open"], ["all", "All"]] as const;

/** The clinic's queue of appointment requests. A request is answered by a person: the clinic agrees a time with the patient and writes it down here; nothing in the app confirms a time by itself. */
export default function AppointmentQueue() {
  const toast = useToast();
  const [list, setList] = useState<QReq[] | null>(null);
  const [err, setErr] = useState("");
  const [filter, setFilter] = useState<(typeof FILTERS)[number][0]>("open");
  const [act, setAct] = useState<{ r: QReq; verb: Verb } | null>(null);
  const [when, setWhen] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [formErr, setFormErr] = useState("");
  const [summary, setSummary] = useState<{ r: QReq; text: string } | string | null>(null);

  const load = useCallback(() => {
    api<{ requests: QReq[] }>("/api/v1/clinic/appointments").then((r) => { setErr(""); setList(r.requests); }).catch((e) => setErr(e instanceof Error ? e.message : "We couldn't load the requests."));
  }, []);
  useEffect(load, [load]);

  async function submit() {
    if (!act) return;
    setBusy(true); setFormErr("");
    try {
      await post(`/api/v1/clinic/appointments/${encodeURIComponent(act.r.id)}`, { status: act.verb, confirmed_for: act.verb === "confirmed" ? when : "", note });
      toast("Saved. The patient has been notified.", "ok"); setAct(null); load();
    } catch (e) { setFormErr(e instanceof ApiError ? e.message : "That could not be saved. Please try again."); }
    finally { setBusy(false); }
  }
  async function openSummary(r: QReq) {
    setSummary("Loading…");
    try { const s = await api<{ summary: string }>(`/api/v1/clinic/appointments/${encodeURIComponent(r.id)}/summary`); setSummary({ r, text: s.summary }); }
    catch (e) { setSummary(e instanceof Error ? e.message : "The summary could not be opened."); }
  }
  const start = (r: QReq, verb: Verb) => { setAct({ r, verb }); setWhen(r.confirmed_for ?? ""); setNote(r.staff_note ?? ""); setFormErr(""); };

  const shown = (list ?? []).filter((r) => filter === "all" || r.status === "requested" || r.status === "contacted");
  const waiting = (list ?? []).filter((r) => r.status === "requested").length;

  return (
    <section className="space-y-3" aria-label="Appointment requests" data-appointment-queue>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2>Appointment requests{waiting > 0 && <span className="ml-2 align-middle"><Pill tone="warn">{waiting} new</Pill></span>}</h2>
        <div className="flex gap-2" role="group" aria-label="Show">{FILTERS.map(([k, label]) => <button key={k} type="button" className={`chip ${filter === k ? "is-on" : ""}`} aria-pressed={filter === k} onClick={() => setFilter(k)}>{label}</button>)}</div>
      </div>
      <p className="text-sm text-muted">Patients send a request; you agree a time with them and record it here. Confirming needs the day and time you agreed. The patient is notified of every change.</p>
      {err ? <ErrorState message={err} onRetry={load} /> : list === null ? <LoadingState label="Loading requests…" />
        : shown.length === 0 ? <EmptyState icon={<CalendarCheck className="h-7 w-7" />} title="No open requests">New requests from patients appear here.</EmptyState>
        : <ul className="space-y-3">{shown.map((r) => (
          <li key={r.id} className="card space-y-2 !p-4" data-appointment={r.id} data-status={r.status}>
            <div className="flex flex-wrap items-center gap-2"><Pill tone={TONE[r.status] ?? "info"}>{LABEL[r.status] ?? r.status}</Pill>{r.urgency === "urgent" && <Pill tone="bad">Same day</Pill>}
              <strong>{r.patient ?? "Patient"}</strong><span className="text-sm text-muted">{fmtDateTime(r.created_at)}</span></div>
            <p>{r.reason}</p>
            <p className="text-sm text-muted">{r.service_name ? `Service: ${r.service_name}. ` : ""}{r.preferred ? `Prefers: ${r.preferred}. ` : ""}
              <span className="inline-flex items-center gap-1"><Phone className="h-3.5 w-3.5" aria-hidden />Contact: {r.contact_method === "app" ? "in the app" : `${r.contact_method} ${r.contact_detail}`}</span></p>
            {r.status === "confirmed" && r.confirmed_for && <p className="text-sm"><b>Agreed time:</b> {r.confirmed_for}</p>}
            {r.staff_note && <p className="text-sm text-muted">Note to patient: {r.staff_note}</p>}
            <div className="flex flex-wrap gap-2">
              {r.share_summary && r.analysis_id && <button type="button" className="btn btn-sm" onClick={() => void openSummary(r)}><FileText className="h-4 w-4" aria-hidden /> Photo summary shared by the patient</button>}
              {(r.status === "requested") && <button type="button" className="btn btn-sm" onClick={() => start(r, "contacted")}><Phone className="h-4 w-4" aria-hidden /> Mark contacted</button>}
              {(r.status === "requested" || r.status === "contacted") && <>
                <button type="button" className="btn btn-primary btn-sm" onClick={() => start(r, "confirmed")}><Check className="h-4 w-4" aria-hidden /> Confirm a time</button>
                <button type="button" className="btn btn-sm" onClick={() => start(r, "declined")}><X className="h-4 w-4" aria-hidden /> Decline</button></>}
              {r.status === "confirmed" && <button type="button" className="btn btn-sm" onClick={() => start(r, "closed")}>Close</button>}
            </div>
          </li>))}</ul>}

      {act && (
        <Dialog title={VERB_TITLE[act.verb]} onClose={() => setAct(null)}>
          <div className="space-y-3">
            <p className="text-sm text-muted">{act.r.patient}: {act.r.reason}</p>
            {act.verb === "confirmed" && <label className="block text-sm font-semibold">Day and time you agreed with the patient<input className="input mt-1 font-normal" maxLength={120} value={when} onChange={(e) => setWhen(e.target.value)} placeholder="For example: Tuesday 28 October, 4:30 pm" /></label>}
            <label className="block text-sm font-semibold">Message to the patient (optional)<textarea className="input mt-1 font-normal" rows={3} maxLength={300} value={note} onChange={(e) => setNote(e.target.value)} /></label>
            {formErr && <Note tone="bad"><p>{formErr}</p></Note>}
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" className="btn" onClick={() => setAct(null)}>Back</button>
              <button type="button" className="btn btn-primary" disabled={busy || (act.verb === "confirmed" && when.trim().length < 3)} onClick={() => void submit()}>{busy ? "Saving…" : "Save"}</button>
            </div>
          </div>
        </Dialog>)}

      {summary && (
        <Dialog title="Photo summary shared by the patient" onClose={() => setSummary(null)} wide>
          {typeof summary === "string" ? <p>{summary}</p> : (
            <div className="space-y-3"><p className="text-sm text-muted">{summary.r.patient} chose to share this with their request. It describes the photo check; it is not a diagnosis.</p>
              <p className="whitespace-pre-wrap">{summary.text}</p></div>)}
        </Dialog>)}
    </section>
  );
}
