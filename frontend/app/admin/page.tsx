"use client";

import { useEffect, useState } from "react";
import { api, fmt, post } from "./util";
import { Details, Note, Pill, StatusBadge } from "@/components/ui";
import ModelCard, { Model } from "@/components/ModelCard";
import TrainingPanel from "@/components/TrainingPanel";
import ResearchTry from "@/components/ResearchTry";

type Gate = { allowed: boolean; reasons: string[] };
type DS = { id: string; name: string; url: string; maintainer: string; description: string; intended_task: string; modality: string; annotations: string[];
  populations: string; claimed_license: string; attribution: string; download_method: string; version: string; limitations: string;
  terms_verified: boolean; terms_snapshot_hash: string; terms_checked_at: number | null; approval_status: string; license_gate: Gate; verified_spdx?: string };
type Task = { id: string; name: string; intended_use: string; required_image_type: string; required_labels: string[]; required_dataset: string; architecture: string;
  evaluation_requirements: string[]; supported_populations: string; limitations: string[]; status: { status: string; label: string } };
type Compat = { task: string; compatible: boolean; reasons_incompatible: string[]; reasons_compatible: string[] };
type Sys = { free_disk_gb: number; training_device: string; torch: string | null; ocr_available: boolean; tts: { available: boolean; admin_message?: string }; stt: { available: boolean; admin_message?: string } };
type Ready = { status: string; optional: { name: string; ready: boolean; detail: string }[] };
type Metrics = { queued_now: number; processing_now: number; workers: number; queue_limit: number; tasks: Record<string, { count: number; processing_ms_p50: number | null; processing_ms_p95: number | null; failure_rate: number }> };
type Audit = { id: string; ts: number; actor: string; action: string; target: string; detail: string };

const REQS = ["held_out_evaluation_passed", "subgroup_evaluation_documented", "clinical_signoff_recorded", "intended_use_documented", "regulatory_review_recorded"];

export default function Admin() {
  const [ds, setDs] = useState<DS[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [sys, setSys] = useState<Sys | null>(null);
  const [audit, setAudit] = useState<Audit[]>([]);
  const [ready, setReady] = useState<Ready | null>(null);
  const [met, setMet] = useState<Metrics | null>(null);
  const [dl, setDl] = useState<Record<string, { state: string; error?: string; files?: Record<string, { done: number; total: number; verified: boolean }> }>>({});
  const [compat, setCompat] = useState<Record<string, Compat[]>>({});
  const [msg, setMsg] = useState("");
  const [folder, setFolder] = useState({ folder: "", labels_csv: "" });
  const [ver, setVer] = useState({ verified_spdx: "", permission_document: "" });

  const load = () => {
    api<DS[]>("/api/admin/datasets").then(setDs).catch(() => {});
    api<Task[]>("/api/tasks").then(setTasks).catch(() => {});
    api<Model[]>("/api/admin/models").then(setModels).catch(() => {});
    api<Sys>("/api/admin/system").then(setSys).catch(() => {});
    api<Audit[]>("/api/admin/audit?limit=100").then(setAudit).catch(() => {});
    api<Ready>("/api/v1/health/ready").then(setReady).catch(() => {});
    api<Metrics>("/api/v1/metrics").then(setMet).catch(() => {});
  };
  useEffect(load, []);
  const act = async (fn: () => Promise<unknown>) => { try { await fn(); setMsg(""); load(); } catch (e) { setMsg(e instanceof Error ? e.message : "Failed"); } };

  return (
    <div id="main" className="space-y-8">
      <h1>Admin &amp; AI lab</h1>
      <Note tone="warn">No model here is clinically validated. Nothing can be shown as approved until every release requirement is recorded.</Note>
      {msg && <Note tone="bad">{msg}</Note>}

      {sys && <section className="card"><h2>System</h2><table><tbody>
        <tr><th>Free disk</th><td>{sys.free_disk_gb} GB</td></tr><tr><th>Training device</th><td>{sys.training_device} {sys.torch && `(torch ${sys.torch})`}</td></tr>
        <tr><th>OCR (scanned documents)</th><td>{sys.ocr_available ? "Installed" : "Not installed — scanned documents cannot be read. Install Tesseract."}</td></tr>
        <tr><th>Voice playback</th><td>{sys.tts.available ? "Offline voice ready" : sys.tts.admin_message}</td></tr>
        <tr><th>Voice input</th><td>{sys.stt.available ? "Local speech recognition ready" : sys.stt.admin_message}</td></tr>
        {ready && ready.optional.map((c) => <tr key={c.name}><th>{c.name.replaceAll("_", " ")}</th><td>{c.ready ? <Pill tone="good">ready</Pill> : <Pill tone="warn">unavailable</Pill>} <span className="text-sm text-muted">{c.detail}</span></td></tr>)}
        {met && <tr><th>Job queue</th><td>{met.queued_now} queued · {met.processing_now} processing · {met.workers} workers (limit {met.queue_limit})</td></tr>}
        {met && Object.entries(met.tasks).map(([k, v]) => <tr key={k}><th>{k.replaceAll("_", " ")} (24 h)</th><td>{v.count} jobs · p50 {v.processing_ms_p50 ?? "—"} ms · p95 {v.processing_ms_p95 ?? "—"} ms · failure rate {(v.failure_rate * 100).toFixed(1)}%</td></tr>)}</tbody></table></section>}

      <section className="space-y-3"><h2>Dataset Discovery &amp; Import Center</h2>
        <p className="text-sm text-muted">Every dataset starts blocked. Retrieve the official terms, record the licence you verified, then approve. Licence text below is a pre-filled <em>claim</em>, not a verification.</p>
        {ds.map((d) => (
          <Details key={d.id} title={`${d.name} — ${d.license_gate.allowed ? "usable" : "blocked"} (${d.approval_status})`}>
            <div className="space-y-3">
              <p>{d.description}</p>
              <table><tbody>
                <tr><th>Source</th><td><a className="underline" href={d.url} target="_blank" rel="noreferrer">{d.url}</a> ({d.maintainer})</td></tr>
                <tr><th>Task / modality</th><td>{d.intended_task} · {d.modality}</td></tr>
                <tr><th>Annotations</th><td>{d.annotations.join(", ")}</td></tr>
                <tr><th>Populations</th><td>{d.populations}</td></tr>
                <tr><th>Claimed licence</th><td>{d.claimed_license}</td></tr>
                <tr><th>Attribution</th><td>{d.attribution}</td></tr>
                <tr><th>Download</th><td>{d.download_method} · version {d.version}</td></tr>
                <tr><th>Terms evidence</th><td>{d.terms_snapshot_hash ? `Snapshot ${d.terms_snapshot_hash.slice(0, 12)}… (${fmt(d.terms_checked_at)})` : "None recorded"}</td></tr>
                <tr><th>Limitations</th><td>{d.limitations}</td></tr></tbody></table>
              {!d.license_gate.allowed && <Note tone="bad"><strong>Licence gate:</strong><ul className="list-disc pl-5">{d.license_gate.reasons.map((r) => <li key={r}>{r}</li>)}</ul></Note>}
              <div className="flex flex-wrap gap-2">
                <button className="btn" onClick={() => act(() => post(`/api/admin/datasets/${d.id}/fetch-terms`))}>Retrieve official terms</button>
                <button className="btn" onClick={async () => setCompat({ ...compat, [d.id]: await api<Compat[]>(`/api/admin/datasets/${d.id}/compatibility`) })}>Check task compatibility</button>
                <button className="btn" onClick={() => act(() => post(`/api/admin/datasets/${d.id}/cancel`))}>Cancel running import</button>
                <button className="btn" onClick={async () => { await act(() => post(`/api/admin/datasets/${d.id}/download`)); const t = setInterval(async () => { const st = await api<{ state: string }>(`/api/admin/datasets/${d.id}/download`); setDl((x) => ({ ...x, [d.id]: st })); if (st.state !== "running") clearInterval(t); }, 3000); }}>Download official files (checksum-verified)</button></div>
              {dl[d.id]?.files && <ul className="text-sm">{Object.entries(dl[d.id].files!).map(([n, f]) => <li key={n}>{n}: {f.total ? Math.round((f.done / f.total) * 100) : 0}% {f.verified ? "· sha256 verified" : ""}</li>)}</ul>}
              {dl[d.id]?.error && <Note tone="bad">{dl[d.id].error}</Note>}
              <div className="grid gap-2 sm:grid-cols-3">
                <input className="input" placeholder="Verified SPDX id (e.g. CC-BY-4.0)" value={ver.verified_spdx} onChange={(e) => setVer({ ...ver, verified_spdx: e.target.value })} />
                <input className="input" placeholder="Written-permission reference (optional)" value={ver.permission_document} onChange={(e) => setVer({ ...ver, permission_document: e.target.value })} />
                <button className="btn" onClick={() => act(() => post(`/api/admin/datasets/${d.id}/verify`, ver))}>Record verification</button></div>
              <div className="flex gap-2">
                <button className="btn btn-primary" onClick={() => act(() => post(`/api/admin/datasets/${d.id}/approve`, { approved: true, commercial: true }))}>Approve for project</button>
                <button className="btn" onClick={() => act(() => post(`/api/admin/datasets/${d.id}/approve`, { approved: false }))}>Reject</button></div>
              <div className="grid gap-2 sm:grid-cols-3">
                <input className="input" placeholder="Local image folder" value={folder.folder} onChange={(e) => setFolder({ ...folder, folder: e.target.value })} />
                <input className="input" placeholder="labels.csv (filename,label,group_id,fitzpatrick,source)" value={folder.labels_csv} onChange={(e) => setFolder({ ...folder, labels_csv: e.target.value })} />
                <button className="btn" disabled={!folder.folder} onClick={() => act(() => post(`/api/admin/datasets/${d.id}/import-folder`, { folder: folder.folder, labels_csv: folder.labels_csv || null, limit: 500, commercial: false }))}>Import subset</button></div>
              {compat[d.id] && <table><thead><tr><th>Task</th><th>Compatible</th><th>Why</th></tr></thead><tbody>{compat[d.id].map((c) => <tr key={c.task}><td>{c.task}</td><td>{c.compatible ? "Yes" : "No"}</td><td>{[...c.reasons_incompatible, ...c.reasons_compatible].join(" ")}</td></tr>)}</tbody></table>}
            </div>
          </Details>))}
      </section>

      <section className="space-y-3"><h2>Task registry</h2>
        {tasks.map((t) => (<Details key={t.id} title={`${t.name}`}>
          <div className="space-y-2"><StatusBadge status={t.status.status} /><p>{t.intended_use}</p>
            <table><tbody><tr><th>Image type</th><td>{t.required_image_type}</td></tr><tr><th>Labels</th><td>{t.required_labels.join(", ") || "—"}</td></tr><tr><th>Dataset</th><td>{t.required_dataset}</td></tr>
              <tr><th>Architecture</th><td>{t.architecture}</td></tr><tr><th>Evaluation</th><td>{t.evaluation_requirements.join("; ") || "—"}</td></tr><tr><th>Populations</th><td>{t.supported_populations}</td></tr><tr><th>Limitations</th><td>{t.limitations.join("; ")}</td></tr></tbody></table></div>
        </Details>))}
      </section>

      <TrainingPanel tasks={tasks} datasets={ds} onFinished={load} />

      <section className="space-y-3"><h2>Models</h2>
        {!models.length && <p className="text-muted">No models trained yet. Approve and import a compatible dataset, then train above.</p>}
        {models.map((m) => (<div key={m.model_id} className="card space-y-3">
          <strong>{m.task} · {m.model_id.slice(0, 8)}</strong>
          <ModelCard m={m} />
          {m.approval?.missing_requirements.length ? <p className="text-sm">Missing for approval: {m.approval.missing_requirements.join(", ")}{m.approval.failed_evaluation_gates?.length ? ` · failed evaluation gates: ${m.approval.failed_evaluation_gates.join(", ")}` : ""}</p> : null}
          <div className="flex flex-wrap gap-2">
            <button className="btn" onClick={() => { const ok = REQS.filter((r) => confirm(`Has this been completed and documented?\n${r}`)); const use = prompt("Narrow intended use:") ?? ""; void act(() => post(`/api/admin/models/${m.model_id}/approval`, { intended_use: use, requirements: Object.fromEntries(REQS.map((r) => [r, ok.includes(r)])) })); }}>Record approval review</button>
            <button className="btn" onClick={() => act(() => post(`/api/admin/models/${m.model_id}/deploy`))}>Deploy</button>
            <button className="btn" onClick={() => act(() => post(`/api/admin/tasks/${m.task}/rollback`))}>Roll back task</button></div>
        </div>))}
      </section>

      <ResearchTry models={models} />

      <Details title="Audit log (latest 100)"><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th></tr></thead><tbody>{audit.map((a) => <tr key={a.id}><td>{fmt(a.ts)}</td><td>{a.actor}</td><td>{a.action}</td><td>{a.target.slice(0, 12)}</td></tr>)}</tbody></table></Details>
    </div>
  );
}
