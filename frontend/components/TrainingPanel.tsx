"use client";

import { Loader2, Play, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api, post } from "@/lib/api";
import { Note, Pill, useToast } from "@/components/ui";

type Task = { id: string; name: string; status: { status: string } };
type DS = { id: string; name: string; approval_status: string; license_gate: { allowed: boolean } };
type Ver = { id: string; version: number; count: number };
type Job = { status: "running" | "done" | "failed" | "stopped" | "unknown"; phase?: string; percent?: number; log?: string[]; error?: string; model_id?: string };

/** Starts a real training job and shows what the running process reports: phase, percent of optimisation steps, log lines. */
export default function TrainingPanel({ tasks, datasets, onFinished }: { tasks: Task[]; datasets: DS[]; onFinished: () => void }) {
  const toast = useToast();
  const usable = datasets.filter((d) => d.approval_status === "approved");
  const [task, setTask] = useState("lesion_classification_research");
  const [ds, setDs] = useState("");
  const [vers, setVers] = useState<Ver[]>([]);
  const [ver, setVer] = useState("");
  const [epochs, setEpochs] = useState(6);
  const [finetune, setFinetune] = useState("layer4");
  const [weights, setWeights] = useState("");
  const [note, setNote] = useState("");
  const [job, setJob] = useState<{ id: string; s: Job } | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const logRef = useRef<HTMLPreElement>(null);

  useEffect(() => { if (ds) api<Ver[]>(`/api/admin/datasets/${ds}/versions`).then((v) => { setVers(v); setVer(v[0]?.id ?? ""); }).catch(() => setVers([])); }, [ds]);

  const stopPolling = useCallback(() => { if (timer.current) clearInterval(timer.current); timer.current = null; }, []);
  useEffect(() => stopPolling, [stopPolling]);
  useEffect(() => { logRef.current?.scrollTo({ top: logRef.current.scrollHeight }); }, [job?.s.log?.length]);

  function watch(id: string) {
    stopPolling();
    timer.current = setInterval(async () => {
      try {
        const s = await api<Job>(`/api/admin/jobs/${id}`);
        setJob({ id, s });
        if (s.status !== "running") { stopPolling(); onFinished(); }
      } catch { stopPolling(); }
    }, 2000);
  }

  async function start() {
    try {
      const r = await post<{ job_id: string }>("/api/admin/train", { task_id: task, dataset_id: ds, version_id: ver, epochs, finetune, weights_path: weights || null, pretrained_note: note, lr: 5e-4, batch: 32 });
      setJob({ id: r.job_id, s: { status: "running", phase: "starting", percent: 0, log: [] } });
      watch(r.job_id);
    } catch (e) { toast(e instanceof Error ? e.message : "Could not start training.", "bad"); }
  }

  const running = job?.s.status === "running";
  return (
    <section className="card space-y-4" aria-label="Training">
      <h2>Train a model</h2>
      <p className="text-sm text-muted">Runs on this server (CPU unless a GPU is detected). Only approved, licence-verified datasets that are compatible with the task can be used. Metrics below come from the running process, not from estimates.</p>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm font-semibold">Task<select className="input mt-1 font-normal" value={task} onChange={(e) => setTask(e.target.value)}>{tasks.map((t) => <option key={t.id} value={t.id}>{t.name} ({t.status.status})</option>)}</select></label>
        <label className="text-sm font-semibold">Dataset<select className="input mt-1 font-normal" value={ds} onChange={(e) => setDs(e.target.value)}><option value="">Choose an approved dataset…</option>{usable.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
        <label className="text-sm font-semibold">Dataset version<select className="input mt-1 font-normal" value={ver} onChange={(e) => setVer(e.target.value)}>{vers.map((v) => <option key={v.id} value={v.id}>v{v.version} · {v.count} images</option>)}</select></label>
        <label className="text-sm font-semibold">Epochs<input className="input mt-1 font-normal" type="number" min={1} max={100} value={epochs} onChange={(e) => setEpochs(Number(e.target.value))} /></label>
        <label className="text-sm font-semibold">Layers to fine-tune<select className="input mt-1 font-normal" value={finetune} onChange={(e) => setFinetune(e.target.value)}><option value="head">Head only</option><option value="layer4">Last block + head</option><option value="all">All layers (slow)</option></select></label>
        <label className="text-sm font-semibold">Pretrained weights file (local path)<input className="input mt-1 font-normal" placeholder="Blank = random initialisation" value={weights} onChange={(e) => setWeights(e.target.value)} /></label>
      </div>
      <label className="block text-sm font-semibold">Weights provenance &amp; licence note (stored on the model card)<input className="input mt-1 font-normal" value={note} onChange={(e) => setNote(e.target.value)} /></label>
      <div className="flex flex-wrap items-center gap-3">
        <button className="btn btn-primary" disabled={!ds || !ver || running} onClick={start}>{running ? <Loader2 className="spin h-4 w-4" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />} Start training</button>
        {running && <button className="btn" onClick={async () => { await post(`/api/admin/jobs/${job!.id}/stop`); toast("Stopping after the current step…", "info"); }}><Square className="h-4 w-4" aria-hidden /> Stop</button>}
        {job && <Pill tone={job.s.status === "done" ? "good" : job.s.status === "failed" ? "bad" : job.s.status === "stopped" ? "warn" : "info"}>{job.s.status}{job.s.phase && running ? ` · ${job.s.phase}` : ""}</Pill>}
      </div>
      {job && (
        <div className="space-y-2" aria-live="polite">
          {running && <div className="h-3 overflow-hidden rounded-full bg-line" role="progressbar" aria-valuenow={Math.round(job.s.percent ?? 0)} aria-valuemin={0} aria-valuemax={100} aria-label="Optimisation steps completed"><div className="h-full rounded-full bg-teal transition-[width]" style={{ width: `${job.s.percent ?? 0}%` }} /></div>}
          {running && <p className="text-xs text-muted">{job.s.percent ?? 0}% of optimisation steps in the {job.s.phase} phase (data preparation and baselines are logged but not counted).</p>}
          {job.s.error && <Note tone="bad">{job.s.error}</Note>}
          <pre ref={logRef} className="max-h-56 overflow-auto rounded-2xl bg-ivory p-3 text-xs">{(job.s.log ?? []).join("\n") || "Waiting for the first log line…"}</pre>
        </div>
      )}
    </section>
  );
}
