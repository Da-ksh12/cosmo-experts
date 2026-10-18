"use client";

import { Camera, Check, ChevronRight, ClipboardList, Eye, EyeOff, FileText, History as HistoryIcon, Pill as PillIcon, Receipt, Search, Stethoscope, Syringe } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { ScreenBar } from "@/components/Shell";
import { AuthImage, Dialog, EmptyState, ErrorState, LoadingState, Note, PageHeading, Pill, fmtDate, fmtDateTime } from "@/components/ui";
import { api } from "@/lib/api";

type Item = { kind: string; id: string; title: string; subtitle: string; ts: number; status: string; link: string; has_file?: boolean; region?: string; doc_type?: string };
type Care = { plan: { progress: { done: number; total: number; percent: number | null }; stages: { title: string; status: string }[] } | null };

const FILTERS = [["all", "All History", HistoryIcon], ["consultations", "Consultations", Stethoscope], ["reports", "Reports", FileText], ["treatments", "Treatments", Syringe], ["photos", "Photos", Camera], ["quotations", "Quotations", Receipt]] as const;
const RANGES = [["0", "All time"], ["30", "Last 30 days"], ["90", "Last 90 days"], ["365", "Last year"]] as const;
const ICONS: Record<string, typeof FileText> = { consultation: Stethoscope, report: FileText, prescription: PillIcon, treatment: Syringe, photo: Camera, quotation: Receipt };
const PRIVACY_KEY = "cosmo.hidePhotos";

function Ring({ percent }: { percent: number }) {
  const r = 38, c = 2 * Math.PI * r;
  return (
    <svg viewBox="0 0 96 96" className="h-24 w-24 shrink-0" role="img" aria-label={`${percent}% of care-plan stages completed`}>
      <circle cx="48" cy="48" r={r} fill="none" stroke="#dbe7e3" strokeWidth="9" />
      <circle cx="48" cy="48" r={r} fill="none" stroke="#064f50" strokeWidth="9" strokeLinecap="round" strokeDasharray={`${(c * percent) / 100} ${c}`} transform="rotate(-90 48 48)" />
      <text x="48" y="55" textAnchor="middle" className="fill-[#083f43] font-serif text-[22px] font-bold">{percent}%</text>
    </svg>
  );
}

function HistoryView() {
  const params = useSearchParams();
  const [kind, setKind] = useState(params.get("kind") ?? "all");
  const [range, setRange] = useState("0");
  const [q, setQ] = useState("");
  const [items, setItems] = useState<Item[] | null>(null);
  const [photos, setPhotos] = useState<Item[]>([]);
  const [docs, setDocs] = useState<Item[]>([]);
  const [care, setCare] = useState<Care | null>(null);
  const [err, setErr] = useState("");
  const [detail, setDetail] = useState<Item | null>(null);
  const [hide, setHide] = useState(() => { try { return sessionStorage.getItem(PRIVACY_KEY) === "1"; } catch { return false; } });
  const [pair, setPair] = useState<{ a: string; b: string }>({ a: "", b: "" });

  const load = useCallback(() => {
    const qs = new URLSearchParams({ kind, since_days: range, q });
    api<Item[]>(`/api/history?${qs}`).then((x) => { setErr(""); setItems(x); }).catch((e) => setErr(e.message));
  }, [kind, range, q]);
  useEffect(() => { const t = setTimeout(load, q ? 250 : 0); return () => clearTimeout(t); }, [load, q]);
  useEffect(() => {
    api<Item[]>("/api/history?kind=photos").then((p) => setPhotos(p.filter((x) => x.has_file))).catch(() => {});
    api<Item[]>("/api/history?kind=reports").then(setDocs).catch(() => {});
    api<Care>("/api/care-plan").then(setCare).catch(() => {});
  }, []);

  const toggleHide = () => { const v = !hide; setHide(v); try { sessionStorage.setItem(PRIVACY_KEY, v ? "1" : "0"); } catch { /* storage blocked */ } };

  const regions = useMemo(() => [...new Set(photos.map((p) => p.region!))].filter((r) => photos.filter((p) => p.region === r).length >= 2), [photos]);
  const [region, setRegion] = useState("");
  const rp = useMemo(() => photos.filter((p) => p.region === (region || regions[0])).sort((a, b) => a.ts - b.ts), [photos, region, regions]);
  const A = rp.find((p) => p.id === pair.a) ?? rp[0], B = rp.find((p) => p.id === pair.b) ?? rp[rp.length - 1];

  if (err && !items) return <ErrorState message={err} onRetry={load} />;
  const plan = care?.plan;

  return (
    <div className="space-y-6">
      <ScreenBar back="/" />
      <PageHeading eyebrow="Your journey" title="History & Reports" subtitle="Your past reviews, photos, reports and quotations in one place." />

      <div className="scroll-x" role="tablist" aria-label="Filter history">
        {FILTERS.map(([id, label, Icon]) => <button key={id} role="tab" aria-selected={kind === id} className="chip" onClick={() => setKind(id)}><Icon className="h-5 w-5" aria-hidden />{label}</button>)}
      </div>

      {plan && plan.progress.percent !== null && (
        <section className="card flex flex-wrap items-center gap-5" aria-label="Care plan progress">
          <Ring percent={plan.progress.percent} />
          <div className="min-w-[min(12rem,100%)] flex-1 basis-48 space-y-2"><h2>Your Care Plan Progress</h2><p className="text-muted">{plan.progress.done} of {plan.progress.total} stages completed, as recorded by your clinic.</p>
            <ol className="flex flex-wrap gap-x-4 gap-y-1 text-sm">{plan.stages.map((s) => <li key={s.title} className="flex items-center gap-1.5"><span className={`grid h-5 w-5 place-items-center rounded-full ${s.status === "done" ? "bg-teal text-white" : "bg-line text-muted"}`}>{s.status === "done" && <Check className="h-3 w-3" aria-hidden />}</span>{s.title}</li>)}</ol></div>
          <Link href="/care" className="btn btn-primary w-full sm:w-auto">View Care Plan</Link>
        </section>
      )}

      <section className="space-y-3" aria-label="Recent activity">
        <div className="flex flex-wrap items-center justify-between gap-3"><h2>Recent Activity</h2>
          <div className="flex flex-wrap items-center gap-2">
            <label className="relative"><span className="sr-only">Search history</span><Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted" aria-hidden />
              <input className="input !w-44 !rounded-full !pl-9" type="search" placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} /></label>
            <label><span className="sr-only">Date range</span><select className="input !w-auto !rounded-full font-semibold" value={range} onChange={(e) => setRange(e.target.value)}>{RANGES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label></div></div>
        {items === null ? <LoadingState /> : items.length === 0 ? (
          <EmptyState icon={<ClipboardList className="h-7 w-7" />} title={kind === "all" && !q && range === "0" ? "Nothing here yet" : "No matching records"}>
            {kind === "all" && !q && range === "0" ? "Your photos, reports, quotations and doctor reviews will build up here." : "Try a different filter, date range or search."}</EmptyState>
        ) : (
          <ol className="relative space-y-3 border-l-2 border-line pl-5">
            {items.map((it) => { const Icon = ICONS[it.kind] ?? FileText; return (
              <li key={`${it.kind}${it.id}`} className="relative">
                <span className="absolute -left-[1.78rem] top-6 h-3 w-3 rounded-full bg-teal ring-4 ring-ivory" aria-hidden />
                <div className="card flex flex-wrap items-center gap-3 !p-3">
                  <span className="h-16 w-16 shrink-0 overflow-hidden rounded-2xl">
                    {it.kind === "photo" && it.has_file ? <AuthImage src={`/api/images/${it.id}/file?thumb=1`} alt="" className="h-16 w-16" blur={hide} fallback={<Camera className="h-6 w-6" />} />
                      : it.kind === "report" || it.kind === "prescription" ? <AuthImage src={`/api/documents/${it.id}/thumbnail`} alt="" className="h-16 w-16" fallback={<Icon className="h-6 w-6" />} />
                      : <span className="grid h-16 w-16 place-items-center bg-sage text-teal"><Icon className="h-7 w-7" strokeWidth={1.5} /></span>}</span>
                  <span className="min-w-0 flex-1"><span className="block text-xs font-semibold text-muted">{fmtDate(it.ts)}</span><span className="block truncate font-semibold">{it.title}</span><span className="block truncate text-sm text-muted">{it.subtitle}</span></span>
                  <button className="btn btn-sm hidden sm:inline-flex" onClick={() => setDetail(it)}>View Details</button>
                  <button className="btn btn-icon btn-sm sm:hidden" onClick={() => setDetail(it)} aria-label={`View details of ${it.title}`}><ChevronRight className="h-5 w-5" /></button>
                </div></li>); })}
          </ol>)}
      </section>

      <section className="card space-y-4" aria-label="Progress photos">
        <div className="flex flex-wrap items-center justify-between gap-2"><div><h2>Progress Photos</h2><p className="text-sm text-muted">Your accepted photos over time. Only you and your care team can see them.</p></div>
          <button className="btn btn-sm" onClick={toggleHide} aria-pressed={hide}>{hide ? <Eye className="h-4 w-4" aria-hidden /> : <EyeOff className="h-4 w-4" aria-hidden />}{hide ? "Show photos" : "Hide photos"}</button></div>
        {photos.length === 0 ? <p className="text-muted">No accepted photos yet. <Link href="/photo" className="font-semibold text-teal">Add a photo</Link></p> : (
          <div className="scroll-x">{[...photos].sort((a, b) => a.ts - b.ts).map((p) => <figure key={p.id} className="w-28"><AuthImage src={`/api/images/${p.id}/file?thumb=1`} alt={`${p.region} photo from ${fmtDate(p.ts)}`} className="h-28 w-28 rounded-2xl" blur={hide} /><figcaption className="mt-1 text-center text-xs text-muted">{fmtDate(p.ts)}</figcaption></figure>)}</div>)}
        {rp.length >= 2 && A && B && (
          <div className="space-y-3 rounded-2xl bg-ivory p-3"><h3>Compare two photos</h3>
            <div className="flex flex-wrap gap-2">{regions.length > 1 && <select className="input !w-auto" aria-label="Area" value={region || regions[0]} onChange={(e) => { setRegion(e.target.value); setPair({ a: "", b: "" }); }}>{regions.map((r) => <option key={r} value={r}>{r.replace("_", " / ")}</option>)}</select>}
              <select className="input !w-auto" aria-label="Earlier photo" value={A.id} onChange={(e) => setPair({ ...pair, a: e.target.value })}>{rp.map((p) => <option key={p.id} value={p.id}>{fmtDate(p.ts)}</option>)}</select>
              <select className="input !w-auto" aria-label="Later photo" value={B.id} onChange={(e) => setPair({ ...pair, b: e.target.value })}>{rp.map((p) => <option key={p.id} value={p.id}>{fmtDate(p.ts)}</option>)}</select></div>
            <div className="grid grid-cols-2 gap-3">{[["Before", A], ["After", B]].map(([label, p]) => <figure key={label as string}><AuthImage src={`/api/images/${(p as Item).id}/file`} alt={`${label} photo`} className="aspect-square w-full rounded-2xl" blur={hide} /><figcaption className="mt-1 text-center text-sm font-semibold">{label as string} · {fmtDate((p as Item).ts)}</figcaption></figure>)}</div>
            <p className="text-xs text-muted">Photos taken in different light or at different angles aren&apos;t directly comparable. Your clinician interprets any change.</p></div>)}
      </section>

      <section className="card space-y-3" aria-label="Reports summary"><div className="flex items-center justify-between"><h2>Reports Summary</h2><Link href="/reports" className="font-semibold text-teal">View all</Link></div>
        {docs.length === 0 ? <p className="text-muted">No reports yet.</p> : <ul className="divide-y divide-line">{docs.slice(0, 3).map((d) => <li key={d.id} className="flex items-center gap-3 py-2"><FileText className="h-6 w-6 shrink-0 text-teal" aria-hidden />
          <span className="min-w-0 flex-1"><span className="block truncate font-semibold">{d.title}</span><span className="text-sm text-muted">{fmtDate(d.ts)} · {d.subtitle}</span></span><Link href={d.link} className="btn btn-sm">View</Link></li>)}</ul>}</section>

      {detail && (
        <Dialog title={detail.title} onClose={() => setDetail(null)}>
          <div className="space-y-3">
            {detail.kind === "photo" && detail.has_file && <AuthImage src={`/api/images/${detail.id}/file`} alt={detail.title} className="max-h-[50dvh] w-full rounded-2xl" blur={hide} />}
            <p className="text-sm text-muted">{fmtDateTime(detail.ts)}</p><p>{detail.subtitle}</p>
            <Pill tone={["accepted", "approved", "reviewed", "done", "reviewed_by_clinician"].includes(detail.status) ? "good" : detail.status === "rejected" ? "bad" : "info"}>{detail.status === "reviewed" ? "reviewed by a clinician" : detail.status.replaceAll("_", " ")}</Pill>
            {detail.kind === "photo" && !detail.has_file && <Note tone="info">This photo wasn&apos;t kept because it didn&apos;t pass the quality check.</Note>}
            <Link href={detail.link} className="btn btn-primary w-full" onClick={() => setDetail(null)}>Open</Link></div>
        </Dialog>)}
    </div>
  );
}

export default function History() { return <Suspense fallback={null}><HistoryView /></Suspense>; }
