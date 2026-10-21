"use client";

import { CalendarPlus, Clock, ExternalLink, Mail, MapPin, MessageCircle, Phone, Send, X } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { ScreenBar } from "@/components/Shell";
import { ConfirmDialog, EmptyState, ErrorState, LoadingState, Note, PageHeading, Pill, fmtDateTime, money, useToast } from "@/components/ui";
import { api, ApiError, post } from "@/lib/api";

type Service = { id: string; name: string; description?: string; unit_price: number; currency: string; category?: string; sessions?: number; duration_min?: number | null };
type Profile = {
  profile: Record<string, string>; configured: boolean; contact_available: boolean;
  booking: { mode: string; external_page: string | null; note: string }; availability: { source: string; slots: unknown[]; note: string }; services: Service[];
};
type Req = {
  id: string; status: string; service_name: string | null; reason: string; preferred: string; contact_method: string; urgency: string; analysis_id: string | null; share_summary: boolean;
  confirmed_for: string | null; staff_note: string; created_at: number; updated_at: number | null;
};

const STATUS: Record<string, { label: string; tone: "info" | "good" | "warn" | "bad" }> = {
  requested: { label: "Waiting for the clinic", tone: "warn" }, contacted: { label: "The clinic has seen your request", tone: "info" }, confirmed: { label: "Confirmed", tone: "good" },
  declined: { label: "The clinic could not offer a time", tone: "bad" }, cancelled: { label: "Cancelled", tone: "info" }, closed: { label: "Closed", tone: "info" },
};
const METHODS = [["app", "In the app"], ["phone", "Phone call"], ["email", "E-mail"]] as const;
const digits = (s: string) => s.replace(/[^\d+]/g, "");

function Clinic({ p }: { p: Profile }) {
  const c = p.profile;
  const rows = [
    c.phone && { Icon: Phone, node: <a className="underline" href={`tel:${digits(c.phone)}`}>{c.phone}</a> },
    c.whatsapp && { Icon: MessageCircle, node: <a className="underline" href={`https://wa.me/${digits(c.whatsapp).replace(/^\+/, "")}`} target="_blank" rel="noopener noreferrer">WhatsApp {c.whatsapp}</a> },
    c.email && { Icon: Mail, node: <a className="underline" href={`mailto:${c.email}`}>{c.email}</a> },
    c.address && { Icon: MapPin, node: <span>{c.address}</span> },
    c.hours && { Icon: Clock, node: <span>{c.hours}</span> },
  ].filter(Boolean) as { Icon: typeof Phone; node: React.ReactNode }[];
  return (
    <section className="card space-y-3" aria-label="The clinic" data-clinic-profile>
      <h3 className="font-serif text-xl text-teal-dark">{c.clinic_name || "The clinic"}</h3>
      {rows.length > 0 ? <ul className="space-y-2">{rows.map((r, i) => <li key={i} className="flex items-start gap-2"><r.Icon className="mt-0.5 h-4 w-4 shrink-0 text-teal" aria-hidden />{r.node}</li>)}</ul>
        : <p className="text-muted">The clinic has not added its contact details here yet. Your request still reaches its team in the app.</p>}
      {c.notes && <p className="text-sm text-muted">{c.notes}</p>}
      {p.booking.external_page && <a href={p.booking.external_page} target="_blank" rel="noopener noreferrer" className="btn btn-sm no-underline"><ExternalLink className="h-4 w-4" aria-hidden /> Open the clinic&apos;s online booking page</a>}
    </section>
  );
}

function Requests({ list, onChanged }: { list: Req[]; onChanged: () => void }) {
  const toast = useToast();
  const [cancel, setCancel] = useState<Req | null>(null);
  async function doCancel(r: Req) {
    setCancel(null);
    try { await post(`/api/v1/appointments/requests/${encodeURIComponent(r.id)}/cancel`); toast("Request cancelled.", "ok"); onChanged(); }
    catch (e) { toast(e instanceof Error ? e.message : "Could not cancel the request.", "bad"); }
  }
  return (
    <section className="space-y-3" aria-label="Your requests" data-requests>
      <h2>Your requests</h2>
      <ul className="space-y-3">{list.map((r) => {
        const st = STATUS[r.status] ?? { label: r.status, tone: "info" as const };
        return (
          <li key={r.id} className="card space-y-2 !p-4" data-request-status={r.status}>
            <div className="flex flex-wrap items-center gap-2"><Pill tone={st.tone}>{st.label}</Pill>{r.urgency === "urgent" && <Pill tone="warn">Same-day request</Pill>}<span className="text-sm text-muted">{fmtDateTime(r.created_at)}</span></div>
            <p className="font-semibold">{r.reason}</p>
            {r.service_name && <p className="text-sm text-muted">Service: {r.service_name}</p>}
            {r.preferred && <p className="text-sm text-muted">Preferred: {r.preferred}</p>}
            {r.status === "confirmed" && r.confirmed_for && <Note tone="good"><p><b>Confirmed for {r.confirmed_for}.</b> Please contact the clinic if you cannot come.</p></Note>}
            {r.staff_note && <p className="text-sm">Message from the clinic: {r.staff_note}</p>}
            {(r.status === "requested" || r.status === "contacted") && <button type="button" className="btn btn-sm" onClick={() => setCancel(r)}><X className="h-4 w-4" aria-hidden /> Cancel this request</button>}
          </li>);
      })}</ul>
      {cancel && <ConfirmDialog title="Cancel this request?" message="The clinic will no longer contact you about it. You can send a new request at any time." confirmLabel="Cancel request" danger onConfirm={() => void doCancel(cancel)} onCancel={() => setCancel(null)} />}
    </section>
  );
}

function Book() {
  const params = useSearchParams();
  const urgentParam = params.get("urgency") === "urgent";
  const analysis = params.get("analysis") ?? "";
  const [prof, setProf] = useState<Profile | null>(null);
  const [err, setErr] = useState("");
  const [reqs, setReqs] = useState<Req[] | null>(null);
  const [reason, setReason] = useState("");
  const [serviceId, setServiceId] = useState("");
  const [preferred, setPreferred] = useState("");
  const [method, setMethod] = useState<(typeof METHODS)[number][0]>("app");
  const [detail, setDetail] = useState("");
  const [urgent, setUrgent] = useState(urgentParam);
  const [share, setShare] = useState(false);
  const [busy, setBusy] = useState(false);
  const [formErr, setFormErr] = useState("");
  const [sent, setSent] = useState<Req | null>(null);

  const loadReqs = useCallback(() => { api<{ requests: Req[] }>("/api/v1/appointments/requests").then((r) => setReqs(r.requests)).catch(() => setReqs([])); }, []);
  const load = useCallback(() => {
    api<Profile>("/api/v1/clinic/profile").then((r) => { setErr(""); setProf(r); }).catch((e) => setErr(e instanceof Error ? e.message : "We couldn't load the clinic's details."));
    loadReqs();
  }, [loadReqs]);
  useEffect(load, [load]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setFormErr(""); setSent(null);
    if (reason.trim().length < 3) { setFormErr("Please say briefly what the appointment is for."); return; }
    setBusy(true);
    try {
      const r = await post<Req>("/api/v1/appointments/requests", {
        reason: reason.trim(), preferred: preferred.trim(), service_id: serviceId || null, contact_method: method, contact_detail: method === "app" ? "" : detail.trim(),
        analysis_id: analysis && share ? analysis : null, share_summary: !!(analysis && share), urgency: urgent ? "urgent" : "routine",
      });
      setSent(r); setReason(""); setPreferred(""); setDetail(""); setShare(false); loadReqs();
    } catch (e2) { setFormErr(e2 instanceof ApiError ? e2.message : "We couldn't send your request. Please try again."); }
    finally { setBusy(false); }
  }

  if (err && !prof) return <div className="space-y-6"><ScreenBar back="/" /><ErrorState message={err} onRetry={load} /></div>;
  if (!prof) return <LoadingState />;

  return (
    <div className="space-y-6">
      <ScreenBar back="/" />
      <PageHeading eyebrow="Appointments" title="Book a dermatologist" subtitle="Send the clinic a request. Its team contacts you to agree a time." />

      <Note tone="info"><p>{prof.booking.note} {prof.availability.note}</p></Note>
      <Clinic p={prof} />

      {sent ? (
        <section className="card space-y-2" role="status" data-request-sent>
          <h3 className="font-serif text-xl text-teal-dark">Your request has been sent</h3>
          <p>This is <b>not a confirmed appointment yet</b>. The clinic&apos;s team will contact you {sent.contact_method === "app" ? "here in the app" : sent.contact_method === "phone" ? "by phone" : "by e-mail"} to agree a time. You can follow its status below.</p>
          <button type="button" className="btn btn-sm" onClick={() => setSent(null)}><CalendarPlus className="h-4 w-4" aria-hidden /> Send another request</button>
        </section>
      ) : (
        <form onSubmit={submit} className="card space-y-4" aria-label="Appointment request" data-request-form>
          <h2>Appointment request</h2>
          <div className="space-y-1">
            <label htmlFor="reason" className="block font-semibold">What is the appointment for?</label>
            <textarea id="reason" className="input" rows={3} maxLength={500} required value={reason} onChange={(e) => setReason(e.target.value)} placeholder="For example: pimples that keep coming back, or a review of my skincare routine" />
            <p className="text-xs text-muted">{reason.length}/500. Describe what you are noticing in your own words. Please do not include passwords or payment details.</p>
          </div>
          {prof.services.length > 0 && (
            <div className="space-y-1">
              <label htmlFor="service" className="block font-semibold">Service (optional)</label>
              <select id="service" className="input" value={serviceId} onChange={(e) => setServiceId(e.target.value)}>
                <option value="">Not sure yet / a consultation</option>
                {prof.services.map((s) => <option key={s.id} value={s.id}>{s.name} · {money(s.unit_price, s.currency)}</option>)}
              </select>
              <p className="text-xs text-muted">Prices are the clinic&apos;s approved list. The final cost depends on the plan your doctor agrees with you.</p>
            </div>)}
          <div className="space-y-1">
            <label htmlFor="preferred" className="block font-semibold">Days or times that suit you (optional)</label>
            <input id="preferred" className="input" maxLength={200} value={preferred} onChange={(e) => setPreferred(e.target.value)} placeholder="For example: weekday evenings, or Saturday morning" />
          </div>
          <fieldset className="space-y-2">
            <legend className="font-semibold">How should the clinic reach you?</legend>
            <div className="flex flex-wrap gap-2" role="radiogroup">{METHODS.map(([k, label]) => <button key={k} type="button" role="radio" aria-checked={method === k} className={`chip ${method === k ? "is-on" : ""}`} onClick={() => setMethod(k)}>{label}</button>)}</div>
            {method !== "app" && (
              <div className="space-y-1">
                <label htmlFor="detail" className="block text-sm font-semibold">{method === "phone" ? "Phone number" : "E-mail address"}</label>
                <input id="detail" className="input" type={method === "phone" ? "tel" : "email"} inputMode={method === "phone" ? "tel" : "email"} maxLength={200} required value={detail} onChange={(e) => setDetail(e.target.value)} autoComplete={method === "phone" ? "tel" : "email"} />
              </div>)}
          </fieldset>
          <label className="flex items-start gap-3"><input type="checkbox" className="mt-1 h-5 w-5" checked={urgent} onChange={(e) => setUrgent(e.target.checked)} data-urgent />
            <span><span className="font-semibold">I would like the clinic to look at this today</span><span className="block text-sm text-muted">This marks the request as same-day. It does not guarantee a time. Not for emergencies: if you feel very unwell, contact emergency services.</span></span></label>
          {analysis && (
            <label className="flex items-start gap-3"><input type="checkbox" className="mt-1 h-5 w-5" checked={share} onChange={(e) => setShare(e.target.checked)} data-share-summary />
              <span><span className="font-semibold">Share a short summary of my photo check with this request</span><span className="block text-sm text-muted">It describes the photo and what the check could and could not tell. The clinic can open it for this request only. It is not a diagnosis.</span></span></label>)}
          {formErr && <Note tone="bad"><p>{formErr}</p></Note>}
          <button className="btn btn-primary" type="submit" disabled={busy || reason.trim().length < 3}><Send className="h-5 w-5" aria-hidden /> {busy ? "Sending…" : "Send request"}</button>
        </form>)}

      {reqs && reqs.length > 0 ? <Requests list={reqs} onChanged={loadReqs} />
        : reqs && !sent ? <EmptyState icon={<CalendarPlus className="h-7 w-7" />} title="No requests yet">When you send a request it appears here with its status.</EmptyState> : null}

      <p className="text-center text-sm text-muted">Prefer to ask first? <Link href="/ask" className="font-semibold text-teal">Ask the skin guide</Link></p>
    </div>
  );
}

export default function BookPage() { return <Suspense fallback={null}><Book /></Suspense>; }
