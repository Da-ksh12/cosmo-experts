"use client";

import { AlertTriangle, ArrowRight, Check, CircleDashed, Loader2, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ScreenBar } from "@/components/Shell";
import { Details, Note, PageHeading, ProgressSteps } from "@/components/ui";
import { runUploadJob, STATUS_TEXT, JobStatus } from "@/lib/jobs";
import { Check as CheckT, CheckResult, usePhotoFlow } from "@/lib/photoFlow";

const ICON = { passed: Check, failed: X, uncertain: AlertTriangle, skipped: CircleDashed } as const;
const TONE = { passed: "bg-[var(--good-bg)] text-[var(--good-ink)]", failed: "bg-[var(--bad-bg)] text-[var(--bad-ink)]", uncertain: "bg-[var(--warn-bg)] text-[var(--warn-ink)]", skipped: "bg-line text-muted" } as const;
const WORD = { passed: "Passed", failed: "Needs attention", uncertain: "Uncertain", skipped: "Not checked" } as const;
const PENDING_ROWS = ["File format", "Image quality", "Lighting", "Subject visibility", "Framing", "Overall suitability"];

export default function CheckPage() {
  const router = useRouter();
  const flow = usePhotoFlow();
  const [sel, setSel] = useState(0);
  const inflight = useRef(new Set<string>());
  const leaving = useRef(false);
  const [phase, setPhase] = useState<Record<string, JobStatus>>({});
  const { shots, region, setResult } = flow;

  useEffect(() => { if (shots.length === 0 && !leaving.current) router.replace("/photo"); }, [shots.length, router]);

  const run = useCallback(async (key: string, blob: Blob) => {
    if (inflight.current.has(key)) return;
    inflight.current.add(key);
    try {
      const fd = new FormData();
      fd.append("region", region);
      fd.append("file", new File([blob], "photo.jpg", { type: "image/jpeg" }));
      const job = await runUploadJob<{ id: string; report: Extract<CheckResult, { report: unknown }>["report"] }>("/api/v1/images/check", fd,
        { onStatus: (j) => setPhase((p) => ({ ...p, [key]: j.status })) });
      if (job.status === "failed" || job.status === "cancelled" || !job.result) setResult(key, { error: job.error ?? "The photo check could not finish. Please try again." });
      else setResult(key, job.result as CheckResult);
    } catch (e) {
      setResult(key, { error: e instanceof Error ? e.message : "The photo check failed. Please try again." });
    } finally { inflight.current.delete(key); }
  }, [region, setResult]);

  // The check starts when this screen opens, i.e. right after the patient confirmed with Continue.
  useEffect(() => { shots.forEach((s) => { if (s.edited && !s.result) void run(s.key, s.edited); }); }, [shots, run]);

  const previews = useMemo(() => shots.map((s) => (s.edited ? URL.createObjectURL(s.edited) : "")), [shots]);
  useEffect(() => () => previews.forEach((u) => u && URL.revokeObjectURL(u)), [previews]);

  const idx = Math.min(sel, Math.max(0, shots.length - 1));
  const shot = shots[idx];
  if (!shot) return null;
  const res = shot.result;
  const ok = res && "report" in res ? res : null;
  const err = res && "error" in res ? res.error : "";
  const pending = !res;
  const accepted = shots.filter((s) => s.result && "report" in s.result && s.result.report.verdict === "accepted").length;
  const verdict = ok?.report.verdict;
  const checks: CheckT[] | null = ok?.report.checks ?? null;

  return (
    <div className="space-y-6">
      <ScreenBar back="/photo/adjust" right={<ProgressSteps step={3} total={3} />} />
      <div className="hidden justify-end md:flex"><ProgressSteps step={3} total={3} /></div>
      <PageHeading eyebrow={pending ? "Analysing your photo" : "Photo check"} title="Checking Photo Quality" subtitle="We check that your photo is clear and usable. This is not a diagnosis." />

      {shots.length > 1 && (
        <div className="scroll-x" role="tablist" aria-label="Photos">
          {shots.map((s, i) => <button key={s.key} role="tab" aria-selected={i === idx} className="chip" onClick={() => setSel(i)}>Photo {i + 1}
            {s.result && "report" in s.result ? (s.result.report.verdict === "accepted" ? <Check className="h-4 w-4" /> : <X className="h-4 w-4" />) : <Loader2 className="spin h-4 w-4" />}</button>)}
        </div>
      )}

      <div className="grid grid-cols-1 gap-5 md:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
        <div className="relative overflow-hidden rounded-[26px]">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {previews[idx] && <img src={previews[idx]} alt="The photo being checked" className="aspect-[4/5] w-full object-cover" />}
          <span className="absolute bottom-3 left-3 rounded-full bg-black/55 px-3 py-1 text-sm text-white">Preview</span>
        </div>
        <section className="card space-y-4" aria-live="polite" aria-label="Photo checks">
          <div className="flex items-center gap-4">
            <span className="grid h-14 w-14 place-items-center rounded-full bg-sage text-teal">{pending ? <Loader2 className="spin h-7 w-7" aria-hidden /> : verdict === "accepted" ? <Check className="h-7 w-7" /> : verdict === "uncertain" ? <AlertTriangle className="h-7 w-7" /> : <X className="h-7 w-7" />}</span>
            <div><h2>{pending ? "Analysing image…" : err ? "Check could not finish" : verdict === "accepted" ? "Photo accepted" : verdict === "uncertain" ? "We're not sure" : "Please retake this photo"}</h2>
              <p className="text-sm text-muted">{pending ? (phase[shot.key] === "queued" ? "Your photo is waiting in line." : "The checker is running now.") : "Results from the photo checker."}</p></div>
          </div>
          <ul className="divide-y divide-line">
            {(checks ?? PENDING_ROWS.map((label) => ({ id: label, label, status: "skipped" as const, detail: "" }))).map((c) => {
              const Icon = pending ? Loader2 : ICON[c.status];
              return (
                <li key={c.id} className="flex items-center gap-3 py-2.5">
                  <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-full ${pending ? "bg-line text-muted" : TONE[c.status]}`}><Icon className={`h-5 w-5 ${pending ? "spin" : ""}`} aria-hidden /></span>
                  <span className="min-w-0 flex-1"><span className="block font-semibold leading-tight">{c.label}</span><span className="block text-sm text-muted">{pending ? (phase[shot.key] ? STATUS_TEXT[phase[shot.key]] : "Sending your photo…") : c.detail}</span></span>
                  <span className={`text-sm font-semibold ${pending ? "text-muted" : c.status === "passed" ? "text-[var(--good-ink)]" : c.status === "failed" ? "text-[var(--bad-ink)]" : "text-muted"}`}>{pending ? (phase[shot.key] === "queued" ? "Queued" : "Checking") : WORD[c.status]}</span>
                </li>);
            })}
          </ul>
        </section>
      </div>

      {err && <Note tone="bad"><p className="font-semibold">{err}</p><button className="btn btn-sm mt-2" onClick={() => { setResult(shot.key, undefined); }}><RefreshCw className="h-4 w-4" /> Try again</button></Note>}
      {ok && (
        <section className={`card-sage flex items-start gap-4 ${verdict === "rejected" ? "!bg-[var(--bad-bg)]" : verdict === "uncertain" ? "!bg-[var(--warn-bg)]" : ""}`}>
          <ShieldCheck className="h-10 w-10 shrink-0 text-teal" strokeWidth={1.4} aria-hidden />
          <div><h3>{verdict === "accepted" ? "This photo can be used" : verdict === "uncertain" ? "Please try another photo" : "This photo needs to be retaken"}</h3>
            <p className="mt-1">{ok.report.message}</p>
            {verdict === "accepted" && <p className="mt-2 text-sm text-muted">Accepted photos are saved privately for your clinician. A technically good photo does not mean a condition can be identified from it.</p>}
            {ok.report.warnings.map((w) => <p key={w} className="mt-1 text-sm text-muted">{w}</p>)}</div>
        </section>
      )}

      <div className="grid grid-cols-2 gap-3">
        <button className="btn !py-3" onClick={() => { flow.remove(shot.key); router.push(shots.length > 1 ? "/photo/adjust" : "/photo"); }}><RefreshCw className="h-5 w-5" aria-hidden /> Retake Photo</button>
        <button className="btn btn-primary !py-3" disabled={accepted === 0 || shots.some((s) => !s.result)} onClick={() => { leaving.current = true; router.push("/history?kind=photos"); flow.clear(); }}>Continue <ArrowRight className="h-5 w-5" aria-hidden /></button>
      </div>
      {ok && <Details title="Technical details"><p className="mb-2 text-sm text-muted">Checker version {ok.report.version}. These are image measurements, not medical findings.</p>
        <table><tbody>{Object.entries(ok.report.metrics).map(([k, v]) => <tr key={k}><th>{k.replaceAll("_", " ")}</th><td>{String(v)}</td></tr>)}</tbody></table></Details>}
    </div>
  );
}
