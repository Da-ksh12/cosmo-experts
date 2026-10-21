"use client";

import { useCallback, useEffect, useState } from "react";
import { Note, useToast } from "@/components/ui";
import { api, ApiError, put } from "@/lib/api";

const FIELDS = [
  ["clinic_name", "Clinic name", "text", 120], ["phone", "Phone", "tel", 24], ["whatsapp", "WhatsApp number", "tel", 24], ["email", "E-mail", "email", 200],
  ["address", "Address", "text", 300], ["hours", "Opening hours", "text", 300], ["booking_url", "Online booking page (https://…)", "url", 300], ["notes", "Note shown to patients", "text", 500],
] as const;
type Key = (typeof FIELDS)[number][0];
type Profile = Record<Key, string>;
const EMPTY: Profile = { clinic_name: "", phone: "", whatsapp: "", email: "", address: "", hours: "", booking_url: "", notes: "" };

/** What the clinic says about itself. Only what is filled in is shown to patients; the assistant quotes these details and never invents any. Live availability is not part of this: no calendar is connected. */
export default function ClinicProfileForm() {
  const toast = useToast();
  const [p, setP] = useState<Profile>(EMPTY);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const load = useCallback(() => {
    api<{ profile: Partial<Profile>; configured: boolean }>("/api/v1/clinic/profile").then((r) => { setP({ ...EMPTY, ...r.profile }); setLoaded(true); }).catch((e) => { setErr(e instanceof Error ? e.message : "We couldn't load the clinic profile."); setLoaded(true); });
  }, []);
  useEffect(load, [load]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr("");
    try { await put("/api/v1/clinic/profile", p); toast("Clinic details saved.", "ok"); }
    catch (e2) { setErr(e2 instanceof ApiError ? e2.message : "That could not be saved. Please try again."); }
    finally { setBusy(false); }
  }

  return (
    <form onSubmit={save} className="card space-y-3" aria-label="Clinic details" data-clinic-profile-form>
      <h3>Clinic details for patients</h3>
      <p className="text-sm text-muted">Shown on the booking screen and quoted by the assistant. Leave a field empty to hide it. Appointment times are agreed by your team after a request; no live calendar is connected.</p>
      {!loaded ? <p className="text-muted" role="status">Loading…</p> : (
        <div className="grid gap-3 sm:grid-cols-2">{FIELDS.map(([k, label, type, max]) => (
          <label key={k} className={`block text-sm font-semibold ${k === "notes" || k === "address" || k === "hours" ? "sm:col-span-2" : ""}`}>{label}
            <input className="input mt-1 font-normal" type={type} maxLength={max} value={p[k]} onChange={(e) => setP({ ...p, [k]: e.target.value })} autoComplete="off" /></label>))}
        </div>)}
      {err && <Note tone="bad"><p>{err}</p></Note>}
      <button className="btn btn-primary" type="submit" disabled={busy || !loaded}>{busy ? "Saving…" : "Save clinic details"}</button>
    </form>
  );
}
