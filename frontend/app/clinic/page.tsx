"use client";

import { BadgeCheck, Check, ClipboardList, Flag, Plus, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { AuthImage, Details, Dialog, EmptyState, Note, Pill, StatusBadge, fmtDateTime, useToast } from "@/components/ui";
import { useUser } from "@/components/Shell";
import { api, DOC_TYPE_LABEL, patch, post, put } from "@/lib/api";

type Sub = { documents: { id: string; owner_id: string; patient: string; filename: string; doc_type: string; review_status: string; created_at: number }[];
  images: Photo[]; quotes: { id: string; patient_id: string; status: string; total: number; currency: string }[] };
type Check = { id: string; label: string; status: string; detail: string };
type Photo = { id: string; owner_id?: string; patient?: string; region: string; verdict: string; created_at: number; clinician_review: string; has_file: number | boolean; report: { message: string; checks: Check[]; warnings: string[]; version: string } };
type Case = { patient: { id: string; name: string }; photos: Photo[]; documents: { id: string; filename: string; doc_type: string; review_status: string; flagged: number }[]; quotes: { id: string; status: string; total: number; currency: string }[] };
type Field = { id: string; name: string; value: string; unit: string; ref_range: string; source_page: number; source_text: string; confidence: number; review_status: string; patient_flag: string };
type Detail = { document: { id: string; filename: string; review_status: string }; pages: { page_no: number; text: string }[]; fields: Field[] };
type Price = { id: string; name: string; unit_price: number; currency: string; category: string; sessions: number; duration_min: number; compare_at_price: number | null };
type Quote = { id: string; patient_id: string; status: string; total: number; currency: string; notes: string; lines: { item_id: string; name: string; qty: number }[] };
type KB = { id: string; title: string; body: string; review_status: string; content_origin: string; claims_to_verify: string[]; usable_by_assistant: boolean; reviewed_by: string; source_note: string; version: number };
type Stage = { title: string; detail: string; status: "planned" | "in_progress" | "done" };
type Plan = { id: string; patient_id: string; status: string; assessment: string; findings: string[]; stages: Stage[]; notes: string; recommended_item_ids: string[]; reviewed_at: number | null };
type Cfg = { tax_percent: number; payment_options: string[]; inclusions: string[]; terms: string };
type Metrics = Record<string, number>;

const EMPTY_PLAN = { assessment: "", findings: "", stages: [] as Stage[], notes: "", recommended_item_ids: [] as string[] };

export default function Clinic() {
  const user = useUser();
  const toast = useToast();
  const isDoctor = user?.role === "doctor";
  const [sub, setSub] = useState<Sub | null>(null);
  const [det, setDet] = useState<Detail | null>(null);
  const [quotes, setQuotes] = useState<Quote[]>([]);
  const [prices, setPrices] = useState<Price[]>([]);
  const [kb, setKb] = useState<KB[]>([]);
  const [metrics, setMetrics] = useState<Metrics>({});
  const [patients, setPatients] = useState<{ id: string; name: string }[]>([]);
  const [cfg, setCfg] = useState<Cfg>({ tax_percent: 0, payment_options: [], inclusions: [], terms: "" });
  const [np, setNp] = useState({ name: "", unit_price: 0, category: "General", sessions: 1, duration_min: 0, compare_at_price: "" });
  const [nk, setNk] = useState({ title: "", body: "" });
  const [rev, setRev] = useState<(KB & { ok: boolean; src: string }) | null>(null);
  const [pid, setPid] = useState("");
  const [plans, setPlans] = useState<Plan[]>([]);
  const [form, setForm] = useState(EMPTY_PLAN);
  const [ver, setVer] = useState({ credentials: "", verified: true });
  const [photo, setPhoto] = useState<Photo | null>(null);
  const [kase, setKase] = useState<Case | null>(null);

  const load = useCallback(() => {
    api<Sub>("/api/clinic/submissions").then(setSub).catch(() => {});
    api<Quote[]>("/api/quotes").then(setQuotes).catch(() => {});
    api<Price[]>("/api/price-items").then(setPrices).catch(() => {});
    api<KB[]>("/api/clinic/kb").then(setKb).catch(() => {});
    api<Metrics>("/api/clinic/metrics").then(setMetrics).catch(() => {});
    api<{ id: string; name: string }[]>("/api/clinic/patients").then(setPatients).catch(() => {});
    api<Cfg>("/api/quote-config").then(setCfg).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const loadPlans = useCallback(async (p: string) => {
    if (!p) { setPlans([]); setForm(EMPTY_PLAN); setKase(null); return; }
    api<Case>(`/api/clinic/cases/${p}`).then(setKase).catch(() => setKase(null));
    const rows = await api<Plan[]>(`/api/clinic/care-plans/${p}`);
    setPlans(rows);
    const base = rows.find((r) => r.status === "draft") ?? rows.find((r) => r.status === "approved");
    setForm(base ? { assessment: base.assessment, findings: base.findings.join(", "), stages: base.stages, notes: base.notes, recommended_item_ids: base.recommended_item_ids } : EMPTY_PLAN);
  }, []);

  const act = async (fn: () => Promise<unknown>, ok = "Saved.") => {
    try { await fn(); toast(ok); load(); if (det) void open(det.document.id); if (pid) void loadPlans(pid); }
    catch (e) { toast(e instanceof Error ? e.message : "Failed", "bad"); }
  };
  const open = (id: string) => api<Detail>(`/api/documents/${id}`).then(setDet);
  const draft = plans.find((p) => p.status === "draft");
  const approved = plans.find((p) => p.status === "approved");
  const body = () => ({ assessment: form.assessment, findings: form.findings.split(",").map((s) => s.trim()).filter(Boolean), stages: form.stages, notes: form.notes, recommended_item_ids: form.recommended_item_ids });

  return (
    <div className="space-y-8">
      <div><p className="eyebrow mb-1">Clinic workspace</p><h1>Review &amp; care plans</h1><p className="mt-1 text-muted">Software output is shown beside its source. Only a signed-in doctor can approve clinical content.</p></div>

      <section className="space-y-3"><h2>Patient submissions</h2>
        {!sub?.documents.length ? <EmptyState icon={<ClipboardList className="h-7 w-7" />} title="No submissions yet" /> : (
          <div className="card overflow-x-auto !p-2" tabIndex={0} role="region" aria-label="Scrollable table"><table><thead><tr><th>Patient</th><th>Document</th><th>Type</th><th>Status</th><th></th></tr></thead><tbody>
            {sub.documents.map((d) => <tr key={d.id}><td>{d.patient}</td><td>{d.filename}</td><td>{DOC_TYPE_LABEL[d.doc_type] ?? d.doc_type}</td><td>{d.review_status === "reviewed_by_clinician" ? <Pill tone="good">Reviewed</Pill> : <Pill tone="info">Awaiting review</Pill>}</td><td><button className="btn btn-sm" onClick={() => open(d.id)}>Review</button></td></tr>)}</tbody></table></div>)}
        <h2 className="pt-2">Photo submissions</h2>
        {!sub?.images.filter((i) => i.has_file).length ? <p className="text-muted">No accepted photos waiting.</p> : (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Photo submissions">
            {sub.images.filter((i) => i.has_file).map((i) => (
              <button key={i.id} className="card !p-2 text-left" onClick={() => setPhoto(i)} aria-label={`Open ${i.region} photo from ${i.patient}`}>
                <AuthImage src={`/api/images/${i.id}/file?thumb=1`} alt="" className="aspect-square w-full rounded-xl" />
                <span className="mt-1 block text-sm font-semibold">{i.patient}</span>
                <span className="flex flex-wrap gap-1 text-xs text-muted">{i.region} · {i.clinician_review === "reviewed" ? <Pill tone="good">Reviewed</Pill> : <Pill tone="info">Awaiting review</Pill>}</span>
              </button>))}
          </div>)}
        <p className="text-sm text-muted">Photo checks assess usability only. No condition is detected by the software.</p>
      </section>

      {photo && (
        <Dialog title={`${photo.patient ?? "Patient"} · ${photo.region} photo`} onClose={() => setPhoto(null)} wide>
          <div className="grid gap-4 md:grid-cols-2">
            <AuthImage src={`/api/images/${photo.id}/file`} alt={`${photo.region} photo submitted by the patient`} className="max-h-[60dvh] w-full rounded-2xl" />
            <div className="space-y-3">
              <h3>Automated photo check <span className="text-sm font-normal text-muted">(version {photo.report.version})</span></h3>
              <ul className="space-y-1 text-sm">{photo.report.checks.map((c) => <li key={c.id}><strong>{c.label}:</strong> {c.status} — {c.detail}</li>)}</ul>
              <Note tone="info">This only says the image was usable. It is not a finding. Examine the photo with the patient&apos;s history.</Note>
              {isDoctor && photo.clinician_review !== "reviewed" && <button className="btn btn-primary" onClick={() => act(async () => { await post(`/api/clinic/images/${photo.id}/review`); setPhoto(null); }, "Photo marked reviewed.")}><Check className="h-5 w-5" aria-hidden /> Mark photo reviewed</button>}
              {photo.clinician_review === "reviewed" && <Pill tone="good">Reviewed by a clinician</Pill>}
            </div>
          </div>
        </Dialog>)}

      {det && (
        <section className="card space-y-4" aria-label="Document review">
          <div className="flex flex-wrap items-center justify-between gap-2"><h2>{det.document.filename}</h2>
            <button className="btn btn-primary" onClick={() => act(() => post(`/api/clinic/documents/${det.document.id}/review`), "Marked reviewed.")}><Check className="h-5 w-5" aria-hidden /> Mark document reviewed</button></div>
          <p className="text-sm text-muted">Each value is shown with the exact source line. Correct anything the software misread.</p>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <div className="space-y-3">{det.fields.map((f) => <FieldEditor key={`${f.id}:${f.value}:${f.unit}:${f.ref_range}:${f.review_status}`} f={f} onSave={(b) => act(() => patch(`/api/clinic/fields/${f.id}`, b))} />)}
              {!det.fields.length && <p className="text-muted">No structured results were extracted.</p>}</div>
            <div className="max-h-[32rem] overflow-auto rounded-2xl bg-ivory p-3 text-sm">{det.pages.map((p) => <div key={p.page_no}><strong>Page {p.page_no}</strong><pre className="whitespace-pre-wrap">{p.text || "(no readable text)"}</pre></div>)}</div>
          </div>
        </section>)}

      <section className="card space-y-4" aria-label="Care plan editor">
        <h2>Care plan</h2>
        <p className="text-sm text-muted">Patients only see a plan after a doctor approves it. Editing an approved plan creates a new draft; the approved version stays visible until you approve the new one.</p>
        <select className="input" value={pid} onChange={(e) => { setPid(e.target.value); void loadPlans(e.target.value); }} aria-label="Patient"><option value="">Choose a patient…</option>{patients.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.id})</option>)}</select>
        {pid && kase && (
          <div className="rounded-2xl bg-ivory p-3 space-y-2" aria-label="Case overview">
            <h3>Case overview — {kase.patient.name}</h3>
            <div className="flex flex-wrap gap-2">{kase.photos.filter((p) => p.has_file).map((p) => (
              <button key={p.id} className="h-16 w-16 overflow-hidden rounded-xl" onClick={() => setPhoto({ ...p, patient: kase.patient.name })} aria-label={`Open ${p.region} photo`}>
                <AuthImage src={`/api/images/${p.id}/file?thumb=1`} alt="" className="h-16 w-16" /></button>))}
              {!kase.photos.some((p) => p.has_file) && <span className="text-sm text-muted">No accepted photos.</span>}</div>
            <ul className="text-sm">{kase.documents.map((d) => <li key={d.id} className="flex flex-wrap items-center gap-2">{d.filename} <Pill tone={d.review_status === "reviewed_by_clinician" ? "good" : "info"}>{d.review_status === "reviewed_by_clinician" ? "reviewed" : "awaiting review"}</Pill>
              {d.flagged > 0 && <Pill tone="warn">{d.flagged} flagged by patient</Pill>}<button className="btn btn-sm" onClick={() => open(d.id)}>Review</button></li>)}</ul>
          </div>)}
        {pid && (<>
          <div className="flex flex-wrap gap-2">{approved && <Pill tone="good">Approved version live{approved.reviewed_at ? ` · ${fmtDateTime(approved.reviewed_at)}` : ""}</Pill>}{draft && <Pill tone="warn">Unapproved draft saved</Pill>}</div>
          <label className="block"><span className="font-semibold">Clinical assessment</span><textarea className="input" rows={4} value={form.assessment} onChange={(e) => setForm({ ...form, assessment: e.target.value })} disabled={!isDoctor} /></label>
          <label className="block"><span className="font-semibold">Findings (comma separated)</span><input className="input" value={form.findings} onChange={(e) => setForm({ ...form, findings: e.target.value })} disabled={!isDoctor} /></label>
          <div className="space-y-2"><span className="font-semibold">Stages</span>
            {form.stages.map((s, i) => (
              <div key={i} className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_1.4fr_auto_auto]">
                <input className="input" placeholder="Title" aria-label={`Stage ${i + 1} title`} value={s.title} disabled={!isDoctor} onChange={(e) => setForm({ ...form, stages: form.stages.map((x, j) => (j === i ? { ...x, title: e.target.value } : x)) })} />
                <input className="input" placeholder="Detail" aria-label={`Stage ${i + 1} detail`} value={s.detail} disabled={!isDoctor} onChange={(e) => setForm({ ...form, stages: form.stages.map((x, j) => (j === i ? { ...x, detail: e.target.value } : x)) })} />
                <select className="input" aria-label={`Stage ${i + 1} status`} value={s.status} disabled={!isDoctor} onChange={(e) => setForm({ ...form, stages: form.stages.map((x, j) => (j === i ? { ...x, status: e.target.value as Stage["status"] } : x)) })}><option value="planned">Planned</option><option value="in_progress">In progress</option><option value="done">Done</option></select>
                <button className="btn btn-icon" aria-label={`Remove stage ${i + 1}`} disabled={!isDoctor} onClick={() => setForm({ ...form, stages: form.stages.filter((_, j) => j !== i) })}><Trash2 className="h-4 w-4" /></button>
              </div>))}
            {isDoctor && form.stages.length < 8 && <button className="btn btn-sm" onClick={() => setForm({ ...form, stages: [...form.stages, { title: "", detail: "", status: "planned" }] })}><Plus className="h-4 w-4" aria-hidden /> Add stage</button>}</div>
          <label className="block"><span className="font-semibold">Doctor&apos;s notes for the patient</span><textarea className="input" rows={3} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} disabled={!isDoctor} /></label>
          <fieldset className="space-y-1"><legend className="font-semibold">Recommended price-list items</legend>
            {prices.length === 0 ? <p className="text-sm text-muted">No price-list items yet.</p> : prices.map((p) => (
              <label key={p.id} className="flex items-center gap-2"><input type="checkbox" className="h-5 w-5 accent-[var(--teal)]" disabled={!isDoctor} checked={form.recommended_item_ids.includes(p.id)}
                onChange={(e) => setForm({ ...form, recommended_item_ids: e.target.checked ? [...form.recommended_item_ids, p.id] : form.recommended_item_ids.filter((x) => x !== p.id) })} />{p.name} <span className="text-sm text-muted">({p.category})</span></label>))}</fieldset>
          {isDoctor ? <div className="flex flex-wrap gap-2"><button className="btn" onClick={() => act(() => put(`/api/clinic/care-plans/${pid}`, body()), "Draft saved.")}>Save draft</button>
            <button className="btn btn-primary" disabled={!draft} onClick={() => act(() => post(`/api/clinic/care-plans/${draft!.id}/approve`), "Plan approved and now visible to the patient.")}><Check className="h-5 w-5" aria-hidden /> Approve plan</button></div>
            : <Note tone="info">Administrators can view but not author or approve clinical plans.</Note>}
          {approved && isDoctor && <div className="space-y-2"><span className="font-semibold">Record progress on the approved plan</span>
            {approved.stages.map((s, i) => <div key={i} className="flex flex-wrap items-center justify-between gap-2 rounded-2xl bg-ivory p-2 pl-4"><span>{s.title || `Stage ${i + 1}`} <span className="text-sm text-muted">· {s.status.replace("_", " ")}</span></span>
              <span className="flex gap-1">{(["planned", "in_progress", "done"] as const).map((st) => <button key={st} className="chip !px-3 !py-1.5 text-sm" aria-pressed={s.status === st} onClick={() => act(() => post(`/api/clinic/care-plans/${approved.id}/stages/${i}`, { status: st }), "Progress recorded.")}>{st.replace("_", " ")}</button>)}</span></div>)}</div>}
        </>)}
      </section>

      <section className="space-y-3"><h2>Quotations</h2>
        {quotes.map((q) => <div key={q.id} className="card flex flex-wrap items-center justify-between gap-3"><span><strong>{q.id.slice(0, 8)}</strong> · {q.patient_id} · {q.lines.map((l) => `${l.name}×${l.qty}`).join(", ")}{q.notes && <span className="block text-sm text-muted">{q.notes}</span>}</span>
          <span className="flex flex-wrap items-center gap-3"><span>{q.total.toFixed(2)} {q.currency}</span>{q.status === "approved" ? <Pill tone="good">approved</Pill> : <Pill tone="warn">awaiting approval</Pill>}
            {isDoctor && q.status !== "approved" && <button className="btn btn-primary btn-sm" onClick={() => act(() => post(`/api/clinic/quotes/${q.id}/approve`), "Quotation approved.")}>Approve</button>}
            <button className="btn btn-sm" onClick={() => { const n = prompt("New quantity for the first item (0 removes it):", String(q.lines[0]?.qty ?? 1)); if (n !== null) void act(() => put(`/api/clinic/quotes/${q.id}`, { items: q.lines.map((l, i) => ({ item_id: l.item_id, qty: i === 0 ? Number(n) : l.qty })).filter((l) => l.qty > 0), notes: q.notes }), "Quotation updated and returned to draft."); }}>Edit</button></span></div>)}
        {!quotes.length && <p className="text-muted">No quotation requests.</p>}

        <div className="card space-y-3"><h3>Approved price list</h3>
          <ul className="text-sm">{prices.map((p) => <li key={p.id}>{p.name} <span className="text-muted">· {p.category} · {p.sessions} session(s) · {p.duration_min} min</span> — {p.unit_price} {p.currency}{p.compare_at_price ? ` (list ${p.compare_at_price})` : ""}</li>)}</ul>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            <input className="input" placeholder="Item name" value={np.name} onChange={(e) => setNp({ ...np, name: e.target.value })} aria-label="Item name" />
            <input className="input" placeholder="Category" value={np.category} onChange={(e) => setNp({ ...np, category: e.target.value })} aria-label="Category" />
            <input className="input" type="number" min={0} placeholder="Price per session" value={np.unit_price || ""} onChange={(e) => setNp({ ...np, unit_price: Number(e.target.value) })} aria-label="Price per session" />
            <input className="input" type="number" min={1} placeholder="Default sessions" value={np.sessions} onChange={(e) => setNp({ ...np, sessions: Number(e.target.value) })} aria-label="Default sessions" />
            <input className="input" type="number" min={0} placeholder="Minutes per session" value={np.duration_min || ""} onChange={(e) => setNp({ ...np, duration_min: Number(e.target.value) })} aria-label="Minutes per session" />
            <input className="input" type="number" min={0} placeholder="Struck-through list price (optional)" value={np.compare_at_price} onChange={(e) => setNp({ ...np, compare_at_price: e.target.value })} aria-label="List price" /></div>
          <button className="btn" disabled={!np.name || !isDoctor} onClick={() => act(() => post("/api/clinic/price-items", { ...np, currency: "INR", compare_at_price: np.compare_at_price ? Number(np.compare_at_price) : null }), "Item added.")}>Add item</button>
          <p className="text-sm text-muted">Items are recorded as approved by you. Prices, discounts and savings are never generated by the software.</p></div>

        <div className="card space-y-3"><h3>Quotation settings</h3>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <label className="block text-sm">Tax %<input className="input" type="number" min={0} max={100} value={cfg.tax_percent} onChange={(e) => setCfg({ ...cfg, tax_percent: Number(e.target.value) })} /></label>
            <label className="block text-sm">Payment options (one per line)<textarea className="input" rows={3} value={cfg.payment_options.join("\n")} onChange={(e) => setCfg({ ...cfg, payment_options: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })} /></label>
            <label className="block text-sm">What&apos;s included (one per line)<textarea className="input" rows={3} value={cfg.inclusions.join("\n")} onChange={(e) => setCfg({ ...cfg, inclusions: e.target.value.split("\n").map((s) => s.trim()).filter(Boolean) })} /></label>
            <label className="block text-sm">Terms<textarea className="input" rows={3} value={cfg.terms} onChange={(e) => setCfg({ ...cfg, terms: e.target.value })} /></label></div>
          <button className="btn" onClick={() => act(() => put("/api/clinic/quote-config", cfg), "Settings saved.")}>Save settings</button></div>
      </section>

      <section className="space-y-3"><h2>Clinically reviewed knowledge base</h2>
        <p className="text-sm text-muted">The assistant only uses articles a doctor has approved.</p>
        {kb.map((a) => <div key={a.id} className="card flex flex-wrap items-start justify-between gap-3"><div className="min-w-0 flex-1"><strong>{a.title}</strong>{a.content_origin === "ai_draft" && a.review_status !== "approved" && <span className="ml-2 text-xs text-muted">software-drafted, not yet reviewed</span>}<p className="text-sm">{a.body}</p></div>
          <div className="flex items-center gap-2"><StatusBadge status={a.usable_by_assistant ? "approved_for_defined_use" : "research_only"} label={a.usable_by_assistant ? "Approved" : a.review_status === "approved" ? "Approval out of date" : "Draft"} />{isDoctor && <button className="btn btn-sm" onClick={() => setRev({ ...a, ok: false, src: "" })} aria-label={`Review article ${a.title}`}>{a.usable_by_assistant ? "View" : "Review"}</button>}</div></div>)}
        <div className="card space-y-2"><input className="input" placeholder="Title" value={nk.title} onChange={(e) => setNk({ ...nk, title: e.target.value })} aria-label="Article title" />
          <textarea className="input" rows={3} placeholder="Plain-language text" value={nk.body} onChange={(e) => setNk({ ...nk, body: e.target.value })} aria-label="Article text" />
          <button className="btn" disabled={!nk.title || !nk.body} onClick={() => act(async () => { await post("/api/clinic/kb", nk); setNk({ title: "", body: "" }); }, "Saved as draft.")}>Save as draft</button></div>
      </section>

      {rev && (
        <Dialog title="Review article" onClose={() => setRev(null)} wide>
          <div className="space-y-3">
            <p className="text-sm text-muted">Approval applies to this exact text. Any later change returns the article to draft. The assistant uses only approved, unchanged articles.</p>
            <label className="block text-sm">Title<input className="input" value={rev.title} onChange={(e) => setRev({ ...rev, title: e.target.value, ok: false })} /></label>
            <label className="block text-sm">Text<textarea className="input" rows={5} value={rev.body} onChange={(e) => setRev({ ...rev, body: e.target.value, ok: false })} /></label>
            {rev.claims_to_verify.length > 0 && <div className="text-sm"><strong>Statements to verify before approving</strong><ul className="ml-5 list-disc">{rev.claims_to_verify.map((c) => <li key={c}>{c}</li>)}</ul></div>}
            {rev.usable_by_assistant ? <p className="text-sm">Approved by {rev.reviewed_by}. Source note: {rev.source_note}</p> : isDoctor && (<>
              <label className="block text-sm">Source(s) you checked this against<input className="input" value={rev.src} onChange={(e) => setRev({ ...rev, src: e.target.value })} placeholder="e.g. clinic patient-information leaflet, edition and page" /></label>
              <label className="flex items-start gap-2 text-sm"><input type="checkbox" className="mt-1" checked={rev.ok} onChange={(e) => setRev({ ...rev, ok: e.target.checked })} /><span>I have reviewed this exact text and approve it as accurate, suitable for patients and non-diagnostic.</span></label></>)}
            <div className="flex flex-wrap justify-end gap-2">
              {isDoctor && <button className="btn" onClick={() => act(async () => { await put(`/api/clinic/kb/${rev.id}`, { title: rev.title, body: rev.body }); setRev(null); }, "Saved as draft.")}>Save changes (returns to draft)</button>}
              {isDoctor && !rev.usable_by_assistant && <button className="btn btn-primary" disabled={!rev.ok || rev.src.trim().length < 10 || rev.title !== kb.find((k) => k.id === rev.id)?.title || rev.body !== kb.find((k) => k.id === rev.id)?.body}
                onClick={() => act(async () => { await post(`/api/clinic/kb/${rev.id}/approve`, { attest: rev.ok, sources_checked: rev.src }); setRev(null); }, "Article approved.")}>Approve this text</button>}
            </div>
          </div>
        </Dialog>)}

      {user?.role === "admin" && (
        <section className="card space-y-3"><h2>Verify a clinician</h2><p className="text-sm text-muted">The patient app shows &quot;Verified clinician&quot; only after an administrator records credentials here.</p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-[1fr_2fr_auto]"><input className="input" value="doctor1" readOnly aria-label="Clinician id" /><input className="input" placeholder="Credentials as verified, e.g. MD Dermatology, registration no." value={ver.credentials} onChange={(e) => setVer({ ...ver, credentials: e.target.value })} aria-label="Credentials" />
            <button className="btn btn-primary" disabled={!ver.credentials} onClick={() => act(() => post("/api/admin/users/doctor1/verify-clinician", ver), "Verification recorded.")}><BadgeCheck className="h-5 w-5" aria-hidden /> Record</button></div></section>)}

      <Details title="AI performance & error monitoring"><table><tbody>{Object.entries(metrics).map(([k, v]) => <tr key={k}><th>{k.replaceAll("_", " ")}</th><td>{v}</td></tr>)}</tbody></table></Details>
    </div>
  );
}

function FieldEditor({ f, onSave }: { f: Field; onSave: (b: { value: string; unit: string; ref_range: string; review_status: string }) => void }) {
  const [v, setV] = useState({ value: f.value, unit: f.unit, ref_range: f.ref_range });
  return (
    <div className="space-y-2 rounded-2xl border border-line bg-white p-3">
      <div className="flex flex-wrap items-center justify-between gap-2"><strong>{f.name}</strong><span className="flex gap-2">{f.patient_flag && <Pill tone="warn"><Flag className="h-3 w-3" aria-hidden /> Patient flagged</Pill>}<Pill tone="info">{f.review_status} · {Math.round(f.confidence * 100)}%</Pill></span></div>
      <p className="rounded-xl bg-ivory p-2 font-mono text-xs">p.{f.source_page}: {f.source_text}</p>
      <div className="grid grid-cols-3 gap-2">
        <input className="input" aria-label="Value" value={v.value} onChange={(e) => setV({ ...v, value: e.target.value })} />
        <input className="input" aria-label="Unit" value={v.unit} onChange={(e) => setV({ ...v, unit: e.target.value })} />
        <input className="input" aria-label="Printed range" value={v.ref_range} onChange={(e) => setV({ ...v, ref_range: e.target.value })} /></div>
      <div className="flex flex-wrap gap-2"><button className="btn btn-sm" onClick={() => onSave({ ...v, review_status: "confirmed" })}>Confirm</button>
        <button className="btn btn-sm" onClick={() => onSave({ ...v, review_status: "corrected" })}>Save correction</button>
        <button className="btn btn-sm" onClick={() => onSave({ ...v, review_status: "rejected" })}>Reject</button></div>
    </div>);
}
