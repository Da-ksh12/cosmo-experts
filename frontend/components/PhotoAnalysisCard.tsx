"use client";

import { AlertTriangle, Camera, Check, ChevronRight, Crosshair, FileText, Info, Loader2, RefreshCw, ShieldCheck, Sparkles, Stethoscope, X } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import ImageViewer, { FocusRequest, Marker } from "@/components/ImageViewer";
import { AuthImage, Details, fmtDate, LocalImage } from "@/components/ui";
import { analysisStore, Pending } from "@/lib/analysisStore";
import { Analysis, cancelAnalysis, Intent, nextNonce, Observation, TERMINAL, useAnalysis } from "@/lib/photoAnalysis";
import { useBlobUrl } from "@/lib/useBlobUrl";

export type AskRequest = { text: string; intent: Intent; analysisId: string; observationId?: string };
export type FocusInfo = { analysisId: string; observationId: string | null; label?: string | null } | null;
export type Highlight = { analysisId: string; observationIds: string[]; nonce: number } | null;

const TONE_ICON = { ok: Check, attention: AlertTriangle, info: Info } as const;
const TONE_CLASS = { ok: "bg-[var(--good-bg)] text-[var(--good-ink)]", attention: "bg-[var(--warn-bg)] text-[var(--warn-ink)]", info: "bg-sage text-teal" } as const;

// ---------------------------------------------------------------- progress: only stages that really happened or are happening
export function AnalysisProgress({ analysis, pending, preview, onCancel }: { analysis?: Analysis | null; pending?: Pending | null; preview?: string | null; onCancel?: () => void }) {
  const uploading = pending?.stage === "uploading";
  const queued = analysis?.status === "queued";
  const stage = analysis?.stage;
  const steps: { text: string; done: boolean; detail?: string }[] = [];
  if (pending) steps.push({ text: uploading ? "Uploading your photo" : "Photo received", done: !uploading, detail: uploading ? `${Math.round(pending.progress * 100)}%` : undefined });
  else if (analysis) steps.push({ text: "Photo received", done: true });
  if (queued) steps.push({ text: "Waiting in line", done: false });
  if (analysis?.status === "processing") {
    const first = stage === "assessment";
    steps.push({ text: "Checking the photo and finding the face", done: first });
    if (first) steps.push({ text: "Preparing your assessment", done: false });
  }
  const pct = pending && uploading ? Math.round(pending.progress * 100) : null;
  return (
    <section className="card space-y-3" aria-label="Photo assessment in progress" aria-live="polite" data-analysis-state="progress">
      <div className="flex items-center gap-3">
        {preview ? <LocalImage src={preview} alt="Your photo" className="h-20 w-16 shrink-0 rounded-2xl border border-line object-cover" />
          : <span className="grid h-20 w-16 shrink-0 place-items-center rounded-2xl bg-sage text-teal"><Camera className="h-6 w-6" aria-hidden /></span>}
        <div className="min-w-0 flex-1"><p className="font-serif text-lg font-semibold text-teal-dark">Checking your photo</p><p className="text-sm text-muted">This takes a few seconds. You can leave this screen: it will be here when you come back.</p></div>
      </div>
      <ol className="space-y-2">
        {steps.map((s) => (
          <li key={s.text} className="flex items-center gap-2 text-sm" data-step-done={s.done}>
            <span className={`grid h-5 w-5 shrink-0 place-items-center rounded-full ${s.done ? "bg-teal text-white" : "bg-sage text-teal"}`}>{s.done ? <Check className="h-3 w-3" aria-hidden /> : <Loader2 className="spin h-3 w-3" aria-hidden />}</span>
            <span className={s.done ? "text-muted" : "font-semibold"}>{s.text}</span>{s.detail && <span className="ml-auto tabular-nums text-muted">{s.detail}</span>}
          </li>))}
      </ol>
      {pct !== null && <div className="h-2 overflow-hidden rounded-full bg-line" role="progressbar" aria-label="Upload progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}><div className="h-full rounded-full bg-teal transition-[width]" style={{ width: `${Math.max(3, pct)}%` }} /></div>}
      {onCancel && <button type="button" className="btn btn-sm" onClick={onCancel}><X className="h-4 w-4" aria-hidden /> Cancel</button>}
    </section>
  );
}

/** A photo that is still on its way to the server (or that could not be sent): its real state, with retry, cancel and retake. */
export function PendingUpload({ pending, onDismiss }: { pending: Pending; onDismiss?: () => void }) {
  const preview = pending.photos[0]?.url ?? null;
  if (pending.stage === "uploading" || pending.stage === "submitted") return <AnalysisProgress pending={pending} preview={preview} onCancel={pending.stage === "uploading" ? () => analysisStore.cancel(pending.id) : undefined} />;
  const cancelled = pending.stage === "cancelled";
  return (
    <section className="card space-y-3" role="alert" data-analysis-state={pending.stage}>
      <div className="flex items-center gap-3">
        {preview && <LocalImage src={preview} alt="Your photo" className="h-20 w-16 shrink-0 rounded-2xl border border-line object-cover" />}
        <div className="min-w-0 flex-1"><p className="font-serif text-lg font-semibold text-teal-dark">{cancelled ? "Upload cancelled" : "We couldn't send your photo"}</p>
          <p className="text-sm text-muted">{cancelled ? "Nothing was sent." : pending.error}</p></div>
      </div>
      <div className="flex flex-wrap gap-2">
        <button type="button" className="btn btn-primary btn-sm" onClick={() => analysisStore.retry(pending.id)}><RefreshCw className="h-4 w-4" aria-hidden /> Try again</button>
        <Link href={`/photo?region=${pending.region}`} className="btn btn-sm" onClick={onDismiss}><Camera className="h-4 w-4" aria-hidden /> Retake</Link>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------- the assessment of one photo
function Chip({ tone, children }: { tone: "good" | "warn" | "bad" | "info"; children: React.ReactNode }) {
  return <span className={`badge note-${tone}`}>{children}</span>;
}

function ObservationDetail({ o, a, onView, onAsk, onClose }: { o: Observation; a: Analysis; onView?: () => void; onAsk: (q: AskRequest) => void; onClose: () => void }) {
  const p = a.provenance[o.provenance];
  return (
    <section className="rounded-2xl border border-gold bg-[var(--gold-soft,#f6efe1)] p-4" aria-label={`Selected: ${o.label}`} data-selected-observation={o.id}>
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1"><p className="font-serif text-lg font-semibold text-teal-dark">{o.label}</p>
          <div className="mt-1 flex flex-wrap items-center gap-1.5"><Chip tone={o.tone === "ok" ? "good" : o.tone === "attention" ? "warn" : "info"}>{o.tone === "ok" ? "Fine" : o.tone === "attention" ? "Needs attention" : "For information"}</Chip>
            {o.geometry && <Chip tone="info">Located on the photo</Chip>}{o.validated ? <Chip tone="good">Validated threshold</Chip> : <Chip tone="info">Advisory</Chip>}</div></div>
        <button type="button" className="btn btn-icon btn-sm" onClick={onClose} aria-label="Close details"><X className="h-4 w-4" aria-hidden /></button>
      </div>
      <p className="mt-2">{o.summary}</p>
      {o.evidence.length > 0 && <ul className="mt-2 list-disc space-y-1 pl-5 text-sm">{o.evidence.map((e) => <li key={e.text}>{e.text}</li>)}</ul>}
      <p className="mt-2 text-sm text-muted">{o.confidence ? `Confidence ${Math.round(o.confidence.value * 100)}% (${o.confidence.calibrated ? "calibrated" : "not calibrated"}: ${o.confidence.basis}).` : "Measured, not predicted: there is no confidence score."}</p>
      {o.limitations.length > 0 && <ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm text-muted">{o.limitations.map((l) => <li key={l}>{l}</li>)}</ul>}
      {p && <p className="mt-2 text-xs text-muted">{p.kind === "rule_based" ? "Rule-based check" : "Approved model"} · {p.capability} {p.version}</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        {onView && <button type="button" className="btn btn-sm" onClick={onView}><Crosshair className="h-4 w-4" aria-hidden /> View on photo</button>}
        <button type="button" className="btn btn-sm" onClick={() => onAsk({ text: `Explain: ${o.label}.`, intent: "observation", analysisId: a.id, observationId: o.id })}><Sparkles className="h-4 w-4" aria-hidden /> Explain this</button>
      </div>
    </section>
  );
}

function Assessment({ a, preview, selected, onSelect, focus, onAsk }: { a: Analysis; preview: string | null; selected: string | null; onSelect: (id: string | null) => void; focus: FocusRequest | null; onAsk: (q: AskRequest) => void }) {
  const [annotations, setAnnotations] = useState(true);
  const [full, setFull] = useState(false);
  const stored = useBlobUrl(a.image?.kept ? a.image.url : null);
  const src = stored.url || preview || "";
  const w = a.transform?.width ?? a.image?.width ?? 0, h = a.transform?.height ?? a.image?.height ?? 0;
  const byId = useMemo(() => new Map(a.observations.map((o) => [o.id, o])), [a.observations]);
  const markers: Marker[] = useMemo(() => a.sections.areas.map((id) => byId.get(id)).filter((o): o is Observation => !!o && !!o.geometry).map((o, i) => ({ id: o.id, label: o.label, number: i + 1, geometry: o.geometry!, tone: o.tone })), [a.sections.areas, byId]);
  const numberOf = (id: string) => markers.find((m) => m.id === id)?.number;
  const sel = selected ? byId.get(selected) ?? null : null;
  const observe = a.sections.observe.map((id) => byId.get(id)).filter((o): o is Observation => !!o);
  const attention = observe.filter((o) => o.tone === "attention"), fine = observe.filter((o) => o.tone !== "attention");
  const v = a.outcome?.verdict;
  const [localFocus, setLocalFocus] = useState<FocusRequest | null>(null);
  const view = (id: string) => setLocalFocus({ id, nonce: nextNonce() });
  const frame = useRef<HTMLDivElement>(null);
  const viewOnPhoto = (id: string) => { onSelect(id); view(id); frame.current?.scrollIntoView({ block: "nearest", behavior: "smooth" }); };
  const date = a.image?.taken_at ?? a.created_at;
  const note = a.image && !a.image.kept ? (a.outcome?.verdict === "rejected" ? "Not saved: this photo did not pass the check, so only this copy on your phone is shown." : "This photo is no longer kept.") : "";

  return (
    <section className="card space-y-4 !p-4" aria-label="Photo assessment" data-analysis-state={a.status} data-analysis-id={a.id}>
      <div className="flex flex-wrap items-center gap-2">
        <Chip tone={v === "accepted" ? "good" : v === "rejected" ? "bad" : "warn"}>{v === "accepted" ? "Photo accepted" : v === "rejected" ? "Retake needed" : "Not sure"}</Chip>
        <span className="text-sm text-muted">{a.image?.region_label} · {fmtDate(date)}</span>
      </div>
      <div ref={frame}>
        {src && w > 0 && h > 0 ? (
          <>
            <ImageViewer src={src} alt={`Your ${a.image?.region_label?.toLowerCase() ?? ""} photo`} width={w} height={h} markers={markers} selectedId={selected} onSelect={onSelect} showAnnotations={annotations}
              onToggleAnnotations={() => setAnnotations((x) => !x)} focus={focus ?? localFocus} onFullscreen={() => setFull(true)} />
            {full && <ImageViewer src={src} alt="Your photo" width={w} height={h} markers={markers} selectedId={selected} onSelect={onSelect} showAnnotations={annotations} onToggleAnnotations={() => setAnnotations((x) => !x)} focus={focus ?? localFocus} fullscreen onClose={() => setFull(false)} />}
          </>
        ) : <div className="grid h-40 place-items-center rounded-2xl bg-sage text-center text-sm text-muted">{stored.failed ? "The photo could not be loaded. Check your connection and try again." : a.image?.kept ? "Loading your photo…" : "This photo was not kept."}</div>}
        {note && <p className="mt-2 text-xs text-muted">{note}</p>}
      </div>

      {markers.length > 0 && annotations && (
        <div aria-label="Legend" className="flex flex-wrap gap-2 text-sm" role="group">
          {markers.map((m) => (
            <button key={m.id} type="button" onClick={() => viewOnPhoto(m.id)} aria-pressed={selected === m.id} data-legend={m.id}
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${selected === m.id ? "border-gold bg-[var(--gold-soft,#f6efe1)] font-semibold" : "border-line bg-white"}`}>
              <span className="grid h-5 w-5 place-items-center rounded-full bg-teal-dark text-[11px] font-bold text-white">{m.number}</span>{m.label}</button>))}
        </div>)}

      <div>
        <h3 className="font-serif text-xl text-teal-dark">{a.outcome?.headline}</h3>
        <p className="mt-1 text-muted">{a.outcome?.message}</p>
      </div>

      {sel && <ObservationDetail o={sel} a={a} onClose={() => onSelect(null)} onAsk={onAsk} onView={sel.geometry ? () => view(sel.id) : undefined} />}

      {observe.length > 0 && (
        <section aria-label="What I can observe" className="space-y-2">
          <h4 className="font-semibold text-teal-dark">What I can observe</h4>
          <ul className="space-y-2">
            {attention.map((o) => { const Icon = TONE_ICON[o.tone]; return (
              <li key={o.id}><button type="button" onClick={() => onSelect(selected === o.id ? null : o.id)} aria-pressed={selected === o.id} className="flex w-full items-start gap-3 rounded-2xl border border-line bg-white p-3 text-left">
                <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full ${TONE_CLASS[o.tone]}`}><Icon className="h-4 w-4" aria-hidden /></span>
                <span className="min-w-0 flex-1"><span className="block font-semibold">{o.label}</span><span className="block text-sm text-muted">{o.summary}</span></span><ChevronRight className="mt-1 h-4 w-4 shrink-0 text-muted" aria-hidden /></button></li>); })}
          </ul>
          {fine.length > 0 && (
            <Details title={`${fine.length} check${fine.length === 1 ? "" : "s"} passed or for information`}>
              <ul className="space-y-1.5">{fine.map((o) => <li key={o.id}><button type="button" onClick={() => onSelect(selected === o.id ? null : o.id)} className="flex w-full items-center gap-2 text-left text-sm"><Check className="h-4 w-4 shrink-0 text-[var(--good-ink)]" aria-hidden /><span className="font-semibold">{o.label}</span><span className="text-muted">{o.summary}</span></button></li>)}</ul>
            </Details>)}
        </section>)}

      {markers.length > 0 && (
        <section aria-label="Areas on the photo" className="space-y-2">
          <h4 className="font-semibold text-teal-dark">Areas on the photo</h4>
          <ul className="space-y-2">{markers.map((m) => { const o = byId.get(m.id)!; return (
            <li key={m.id}><button type="button" onClick={() => viewOnPhoto(m.id)} className="flex w-full items-center gap-3 rounded-2xl border border-line bg-white p-3 text-left">
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-teal-dark text-xs font-bold text-white">{numberOf(m.id)}</span>
              <span className="min-w-0 flex-1"><span className="block font-semibold">{o.label}</span><span className="block text-sm text-muted">{o.summary}</span></span><Crosshair className="h-4 w-4 shrink-0 text-muted" aria-hidden /></button></li>); })}</ul>
        </section>)}

      {a.sections.meaning.length > 0 && (
        <section aria-label="What this may mean" className="space-y-1">
          <h4 className="font-semibold text-teal-dark">What this may mean</h4>
          <ul className="list-disc space-y-1 pl-5">{a.sections.meaning.map((t) => <li key={t}>{t}</li>)}</ul>
        </section>)}

      <Details title="What this photo and this check cannot tell you">
        <ul className="list-disc space-y-1 pl-5 text-sm">{a.sections.cannot.map((t) => <li key={t}>{t}</li>)}</ul>
        {a.capabilities.length > 0 && <p className="mt-2 text-sm text-muted">Skin analyses: {a.capabilities.map((c) => `${c.name} (${c.available ? "available" : "not available"})`).join(", ")}.</p>}
      </Details>

      {a.sections.next_steps.length > 0 && (
        <section aria-label="Suggested next steps" className="space-y-2">
          <h4 className="font-semibold text-teal-dark">Suggested next steps</h4>
          <ul className="space-y-2">
            {a.sections.next_steps.map((s) => {
              const body = (<><span className={`grid h-8 w-8 shrink-0 place-items-center rounded-full ${s.kind === "retake" ? TONE_CLASS.attention : TONE_CLASS.info}`}>{s.kind === "retake" || s.kind === "add_photo" ? <Camera className="h-4 w-4" aria-hidden /> : s.kind === "reports" ? <FileText className="h-4 w-4" aria-hidden /> : s.kind === "summary" ? <Sparkles className="h-4 w-4" aria-hidden /> : <Stethoscope className="h-4 w-4" aria-hidden />}</span>
                <span className="min-w-0 flex-1"><span className="block font-semibold">{s.label}</span>{s.detail && <span className="block text-sm text-muted">{s.detail}</span>}</span></>);
              const cls = "flex w-full items-start gap-3 rounded-2xl border border-line bg-white p-3 text-left no-underline";
              return <li key={s.id}>{s.route ? <Link href={s.route} className={cls} data-next-step={s.kind}>{body}</Link>
                : s.kind === "summary" ? <button type="button" className={cls} data-next-step={s.kind} onClick={() => onAsk({ text: "Prepare a summary for my dermatologist.", intent: "summary", analysisId: a.id })}>{body}</button>
                : <div className={cls} data-next-step={s.kind}>{body}</div>}</li>;
            })}
          </ul>
        </section>)}

      <section aria-label="Ask about this photo" className="space-y-2" data-questions>
        <h4 className="font-semibold text-teal-dark">Ask about this photo</h4>
        <div className="flex flex-wrap gap-2">
          {sel && <button type="button" className="chip" data-question="highlighted" onClick={() => onAsk({ text: sel.geometry ? "Explain the highlighted area." : `Explain: ${sel.label}.`, intent: "observation", analysisId: a.id, observationId: sel.id })}><Crosshair className="h-4 w-4" aria-hidden /> {sel.geometry ? "Explain the highlighted area" : `Explain: ${sel.label}`}</button>}
          {a.sections.questions.filter((q) => !(sel && q.observation_id === sel.id)).map((q) => (
            <button key={q.id} type="button" className="chip" data-question={q.intent} onClick={() => q.intent === "compare" ? onAsk({ text: q.text, intent: q.intent, analysisId: a.id }) : onAsk({ text: q.text, intent: q.intent, analysisId: a.id, observationId: q.observation_id ?? undefined })}>{q.text}</button>))}
        </div>
      </section>

      <p className="flex items-start gap-2 text-xs text-muted"><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-teal" aria-hidden />
        {a.provenance.filter((p) => p.capability !== "face.detect" || a.observations.some((o) => o.provenance === 2)).map((p) => `${p.capability} ${p.version}`).slice(0, 3).join(" · ")}. {a.disclaimers.join(" ")}</p>
    </section>
  );
}

// ---------------------------------------------------------------- one card for one message: 1 to 4 photos, a switcher, real state throughout
export default function PhotoAnalysisCard({ ids, onAsk, onFocus, highlight = null }: { ids: string[]; onAsk: (q: AskRequest) => void; onFocus?: (f: FocusInfo) => void; highlight?: Highlight }) {
  const router = useRouter();
  const [idx, setIdx] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [focus, setFocus] = useState<FocusRequest | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const s0 = useAnalysis(ids[0] ?? null), s1 = useAnalysis(ids[1] ?? null), s2 = useAnalysis(ids[2] ?? null), s3 = useAnalysis(ids[3] ?? null);
  const all = [s0, s1, s2, s3].slice(0, Math.min(ids.length, 4));
  const cur = all[Math.min(idx, all.length - 1)];
  const curId = ids[Math.min(idx, ids.length - 1)];
  const a = cur?.analysis ?? null;

  const select = (id: string | null) => { setSelected(id); onFocus?.({ analysisId: curId, observationId: id, label: id ? a?.observations.find((x) => x.id === id)?.label ?? null : null }); };
  const choose = (i: number) => { setIdx(i); setSelected(null); onFocus?.({ analysisId: ids[i], observationId: null }); };
  useEffect(() => { onFocus?.({ analysisId: ids[0], observationId: null }); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // an answer about an area highlights that area on the photo and zooms to it
  useEffect(() => {
    if (!highlight || !a || highlight.analysisId !== a.id || !highlight.observationIds[0]) return;
    const o = a.observations.find((x) => x.id === highlight.observationIds[0]);
    if (!o) return;
    // a one-off response to a request from the conversation (the answer is about this area): select it, tell the composer, zoom to it
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setSelected(o.id);
    onFocus?.({ analysisId: a.id, observationId: o.id, label: o.label });
    if (o.geometry) setFocus({ id: o.id, nonce: highlight.nonce });
  }, [highlight?.nonce, a?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!cur || !curId) return null;
  const preview = analysisStore.previewFor(curId);
  const switcher = all.length > 1 && (
    <div role="tablist" aria-label="Photos in this assessment" className="flex gap-2">
      {all.map((s, i) => (
        <button key={ids[i]} role="tab" type="button" aria-selected={i === idx} aria-label={`Photo ${i + 1}`} onClick={() => choose(i)} className={`relative h-14 w-14 overflow-hidden rounded-2xl border-2 ${i === idx ? "border-teal" : "border-transparent"}`}>
          {s.analysis?.image?.thumb_url ? <AuthImage src={s.analysis.image.thumb_url} alt="" className="h-14 w-14" /> : analysisStore.previewFor(ids[i]) ? <LocalImage src={analysisStore.previewFor(ids[i])!} alt="" className="h-14 w-14 object-cover" /> : <span className="grid h-14 w-14 place-items-center bg-sage text-xs text-muted">{i + 1}</span>}
          {s.analysis && !TERMINAL.includes(s.analysis.status) && <span className="absolute inset-0 grid place-items-center bg-white/60"><Loader2 className="spin h-4 w-4 text-teal" aria-hidden /></span>}
        </button>))}
    </div>);

  let body: React.ReactNode;
  if (cur.gone) body = <section className="card" role="alert" data-analysis-state="gone"><p className="font-semibold">This photo assessment is no longer available.</p><p className="text-sm text-muted">It may have been deleted. You can take a new photo any time.</p><Link href="/photo" className="btn btn-sm mt-2"><Camera className="h-4 w-4" aria-hidden /> Take a photo</Link></section>;
  else if (!a) body = cur.error ? <section className="card space-y-2" role="alert" data-analysis-state="error"><p className="font-semibold">We can&apos;t load this assessment right now.</p><p className="text-sm text-muted">{cur.error}</p><button type="button" className="btn btn-primary btn-sm" onClick={cur.refresh}><RefreshCw className="h-4 w-4" aria-hidden /> Try again</button></section>
    : <section className="card text-muted" aria-busy data-analysis-state="loading"><Loader2 className="spin mr-2 inline h-4 w-4" aria-hidden />Loading your assessment…</section>;
  else if (a.status === "queued" || a.status === "processing") body = <AnalysisProgress analysis={a} preview={preview} onCancel={cancelling ? undefined : () => { setCancelling(true); void cancelAnalysis(a.id).catch(() => {}).finally(() => { setCancelling(false); cur.refresh(); }); }} />;
  else if (a.status === "failed" || a.status === "cancelled") {
    const pend = analysisStore.byAnalysis(a.id);
    body = (<section className="card space-y-3" role="alert" data-analysis-state={a.status}>
      <p className="font-serif text-lg font-semibold text-teal-dark">{a.outcome?.headline ?? "The photo check did not finish"}</p>
      <p className="text-sm text-muted">{a.error || a.outcome?.message || "Nothing was saved."}</p>
      <div className="flex flex-wrap gap-2">{pend && <button type="button" className="btn btn-primary btn-sm" onClick={() => { const nid = analysisStore.rerun(pend.id); if (nid) router.push(`/ask?pending=${nid}`); }}><RefreshCw className="h-4 w-4" aria-hidden /> Try again</button>}
        <Link href={`/photo?region=${a.image?.region ?? "face"}`} className="btn btn-sm"><Camera className="h-4 w-4" aria-hidden /> Retake</Link></div>
    </section>);
  } else body = <Assessment a={a} preview={preview} selected={selected} onSelect={select} focus={focus} onAsk={onAsk} />;

  return (
    <div className="space-y-3" data-photo-assessment={ids.join(",")}>
      {switcher}
      {cur.error && a && <p role="status" className="text-xs text-[var(--warn-ink)]">We can&apos;t update this right now. Trying again…</p>}
      {body}
    </div>
  );
}
