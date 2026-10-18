"use client";

import { Loader2, FlaskConical } from "lucide-react";
import { useState } from "react";
import { Note, Pill, useToast } from "@/components/ui";
import { runUploadJob, STATUS_TEXT, JobStatus } from "@/lib/jobs";
import type { Model } from "@/components/ModelCard";

type Result = {
  status: "predicted" | "abstained" | "rejected_input"; top?: { label: string; probability: number }[]; confidence?: number; abstain_below?: number; interpretation?: string;
  warnings?: string[]; problems?: string[]; corrective_action?: string; processing_ms: number; request_id: string; model: { id: string; architecture: string; dataset: string; code_revision: string; artifact_sha256: string; test_macro_f1: number };
};

/** Administrators / ML engineers only. Runs real inference on a registered research model through the job gateway. Output is never shown to patients. */
export default function ResearchTry({ models }: { models: Model[] }) {
  const toast = useToast();
  const usable = models.filter((m) => m.status === "research_only" || m.status === "approved_for_defined_use");
  const [id, setId] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<JobStatus | "">("");
  const [res, setRes] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);

  async function run() {
    if (!file || !id) return;
    setBusy(true); setRes(null); setPhase("queued");
    try {
      const fd = new FormData(); fd.append("model_id", id); fd.append("file", file);
      const job = await runUploadJob<Result>("/api/v1/research/predict", fd, { onStatus: (j) => setPhase(j.status) });
      if (!job.result) throw new Error(job.error ?? "The model could not run.");
      setRes(job.result);
    } catch (e) { toast(e instanceof Error ? e.message : "Inference failed.", "bad"); }
    finally { setBusy(false); setPhase(""); }
  }

  return (
    <section className="card space-y-3" aria-label="Research inference">
      <h2 className="flex items-center gap-2"><FlaskConical className="h-6 w-6 text-teal" aria-hidden /> Try a model (research only)</h2>
      <Note tone="warn">Research output for ML engineers. Not a diagnosis, never shown to patients or used for care decisions.</Note>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="text-sm font-semibold">Registered model<select className="input mt-1 font-normal" value={id} onChange={(e) => setId(e.target.value)} aria-label="Registered model">
          <option value="">Choose…</option>{usable.map((m) => <option key={m.model_id} value={m.model_id}>{m.model_id.slice(0, 8)} · {m.architecture.split(" ")[0]} · macro-F1 {m.test_metrics.macro_f1}</option>)}</select></label>
        <label className="text-sm font-semibold">Image (JPG/PNG)<input className="input mt-1 font-normal" type="file" accept="image/jpeg,image/png" aria-label="Image to analyse" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></label>
      </div>
      <button className="btn btn-primary" onClick={run} disabled={!id || !file || busy}>{busy ? <><Loader2 className="spin h-4 w-4" aria-hidden /> {phase ? STATUS_TEXT[phase as JobStatus] : "Running"}…</> : "Run on this image"}</button>
      {res && (
        <div className="space-y-3 rounded-2xl bg-ivory p-4" aria-live="polite">
          <div className="flex flex-wrap items-center gap-2"><Pill tone={res.status === "predicted" ? "info" : res.status === "abstained" ? "warn" : "bad"}>{res.status.replace("_", " ")}</Pill><Pill tone="warn">Research only</Pill>
            <span className="text-sm text-muted">{res.processing_ms} ms · request {res.request_id.slice(0, 8)} · model {res.model.id.slice(0, 8)} ({res.model.dataset}, code {res.model.code_revision})</span></div>
          {res.status === "rejected_input" ? <><ul className="list-disc pl-5">{res.problems?.map((p) => <li key={p}>{p}</li>)}</ul><p className="text-sm text-muted">{res.corrective_action}</p></> : (
            <>
              {res.status === "abstained" && <p className="font-semibold">Abstained: top confidence {res.confidence} is below the model&apos;s threshold {res.abstain_below}.</p>}
              <table><thead><tr><th>Class</th><th>Calibrated probability</th></tr></thead><tbody>{res.top?.map((t) => <tr key={t.label}><th>{t.label}</th><td>{(t.probability * 100).toFixed(1)}%</td></tr>)}</tbody></table>
              <p className="text-sm">{res.interpretation}</p>
              <ul className="list-disc pl-5 text-sm text-muted">{res.warnings?.map((w) => <li key={w}>{w}</li>)}</ul>
            </>)}
        </div>)}
    </section>
  );
}
