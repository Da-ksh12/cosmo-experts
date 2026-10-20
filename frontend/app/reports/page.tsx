"use client";

import { ArrowRight, Eye, FileText, FileUp, Loader2, MoreHorizontal, Pill, Plus, RefreshCw, StickyNote, Trash2, UploadCloud, FlaskConical, X } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import DocumentPreview from "@/components/DocumentPreview";
import ResultsOverTime from "@/components/ResultsOverTime";
import { ScreenBar } from "@/components/Shell";
import { AuthImage, ConfirmDialog, EmptyState, ErrorState, LoadingState, Note, PageHeading, Pill as Badge, fmtBytes, fmtDate, useToast } from "@/components/ui";
import { api, CATEGORY_LABEL, del, DOC_TYPE_LABEL } from "@/lib/api";
import { runUploadJob, STATUS_TEXT, JobStatus } from "@/lib/jobs";
import { reportHref } from "@/lib/links";

type Doc = { id: string; filename: string; doc_type: string; user_category: string; doc_date: string; page_count: number; size_bytes: number; mime: string; review_status: string; error: string; created_at: number };
type Job = { id: number; file: File; category: string; progress: number; status: "uploading" | "error"; error?: string; phase?: JobStatus };

const CATS = [
  { id: "laboratory_report", Icon: FlaskConical }, { id: "prescription", Icon: Pill }, { id: "consultation_note", Icon: StickyNote }, { id: "other", Icon: MoreHorizontal },
] as const;
const OK_EXT = /\.(pdf|jpe?g|png|docx|txt)$/i;
const MAX = 15 * 1024 * 1024;

export default function Reports() {
  const toast = useToast();
  const [docs, setDocs] = useState<Doc[] | null>(null);
  const [loadErr, setLoadErr] = useState("");
  const [cat, setCat] = useState<string>("laboratory_report");
  const [jobs, setJobs] = useState<Job[]>([]);
  const [preview, setPreview] = useState<Doc | null>(null);
  const [confirmDel, setConfirmDel] = useState<Doc | null>(null);
  const [drag, setDrag] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const n = useRef(0);

  const load = useCallback(() => { api<Doc[]>("/api/documents").then((x) => { setLoadErr(""); setDocs(x); }).catch((e) => setLoadErr(e.message)); }, []);
  useEffect(load, [load]);

  const send = useCallback(async (job: Job) => {
    setJobs((j) => j.map((x) => (x.id === job.id ? { ...x, status: "uploading", progress: 0, error: undefined } : x)));
    try {
      const fd = new FormData(); fd.append("file", job.file); fd.append("category", job.category);
      const done = await runUploadJob<{ error?: string }>("/api/v1/documents", fd, {
        onUpload: (p) => setJobs((j) => j.map((x) => (x.id === job.id ? { ...x, progress: p } : x))),
        onStatus: (s) => setJobs((j) => j.map((x) => (x.id === job.id ? { ...x, phase: s.status } : x))),
      });
      if (done.status !== "completed") throw new Error((done.result as { error?: string } | null)?.error ?? done.error ?? "The document could not be read.");
      setJobs((j) => j.filter((x) => x.id !== job.id));
      toast(`${job.file.name} was read successfully.`);
      load();
    } catch (e) {
      setJobs((j) => j.map((x) => (x.id === job.id ? { ...x, status: "error", error: e instanceof Error ? e.message : "Upload failed." } : x)));
    }
  }, [load, toast]);

  const add = (files: FileList | File[]) => {
    for (const f of Array.from(files)) {
      if (!OK_EXT.test(f.name)) { toast(`${f.name}: this file type isn't supported.`, "bad"); continue; }
      if (f.size > MAX) { toast(`${f.name}: larger than 15 MB.`, "bad"); continue; }
      const job: Job = { id: ++n.current, file: f, category: cat, progress: 0, status: "uploading" };
      setJobs((j) => [...j, job]);
      void send(job);
    }
  };

  async function remove(d: Doc) {
    setConfirmDel(null);
    try { await del(`/api/documents/${d.id}`); toast("The document and everything read from it was deleted."); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Could not delete.", "bad"); }
  }

  const latest = docs?.[0];
  return (
    <div className="space-y-6">
      <ScreenBar back="/" />
      <PageHeading eyebrow="Upload reports" title="Share Your Reports" subtitle="Upload lab reports, prescriptions or previous consultation notes so we can explain them in simple words." />

      <div className="scroll-x" role="group" aria-label="Report type">
        {CATS.map(({ id, Icon }) => <button key={id} className="chip" aria-pressed={cat === id} onClick={() => setCat(id)}><Icon className="h-5 w-5" aria-hidden />{CATEGORY_LABEL[id]}</button>)}
      </div>

      <div className={`card-flat flex flex-col items-center gap-3 border-2 border-dashed py-10 text-center transition ${drag ? "!border-teal bg-teal-soft" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)} onDrop={(e) => { e.preventDefault(); setDrag(false); add(e.dataTransfer.files); }}>
        <span className="grid h-16 w-16 place-items-center rounded-full bg-sage text-teal"><UploadCloud className="h-8 w-8" strokeWidth={1.4} aria-hidden /></span>
        <h2>Tap to Upload Reports</h2>
        <p className="text-muted">Choose files from your device, or drop them here</p>
        <button className="btn btn-primary" onClick={() => input.current?.click()}><FileUp className="h-5 w-5" aria-hidden /> Choose Files</button>
        <input ref={input} type="file" multiple className="sr-only" aria-label="Choose report files" accept=".pdf,.jpg,.jpeg,.png,.docx,.txt" onChange={(e) => { if (e.target.files) add(e.target.files); e.target.value = ""; }} />
        <p className="text-sm text-muted">Supported: PDF, JPG, PNG, DOCX, TXT · up to 15 MB each</p>
      </div>

      {jobs.length > 0 && (
        <ul className="space-y-3" aria-label="Uploads in progress">
          {jobs.map((j) => (
            <li key={j.id} className="card !p-4">
              <div className="flex items-center gap-3">
                <span className="grid h-11 w-11 place-items-center rounded-2xl bg-sage text-teal">{j.status === "uploading" ? <Loader2 className="spin h-5 w-5" aria-hidden /> : <X className="h-5 w-5 text-[var(--bad-ink)]" aria-hidden />}</span>
                <span className="min-w-0 flex-1"><span className="block truncate font-semibold">{j.file.name}</span>
                  <span className="block text-sm text-muted">{j.status === "uploading" ? (j.progress >= 1 ? (j.phase ? `${STATUS_TEXT[j.phase]}…` : "Reading the document…") : `Uploading ${Math.round(j.progress * 100)}%`) : <span className="text-[var(--bad-ink)]">{j.error}</span>}</span></span>
                {j.status === "error" && <><button className="btn btn-sm" onClick={() => send(j)}><RefreshCw className="h-4 w-4" aria-hidden /> Retry</button><button className="btn btn-icon btn-sm" aria-label="Dismiss" onClick={() => setJobs((x) => x.filter((y) => y.id !== j.id))}><X className="h-4 w-4" /></button></>}
              </div>
              {j.status === "uploading" && <div className="mt-3 h-2 overflow-hidden rounded-full bg-line" role="progressbar" aria-valuenow={Math.round(j.progress * 100)} aria-valuemin={0} aria-valuemax={100}><div className="h-full rounded-full bg-teal transition-[width]" style={{ width: `${j.progress * 100}%` }} /></div>}
            </li>))}
        </ul>
      )}

      <section aria-label="Uploaded reports" className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2"><h2>Uploaded Reports{docs ? ` (${docs.length})` : ""}</h2>
          <button className="btn btn-sm" onClick={() => input.current?.click()}><Plus className="h-4 w-4" aria-hidden /> Add More</button></div>
        {loadErr ? <ErrorState message={loadErr} onRetry={load} /> : docs === null ? <LoadingState />
          : docs.length === 0 ? <EmptyState icon={<FileText className="h-7 w-7" />} title="No reports yet">Upload your first report above and we&apos;ll explain it in plain language.</EmptyState>
          : docs.map((d) => (
            <article key={d.id} className="card flex items-center gap-3 !p-3">
              <Link href={reportHref(d.id)} className="flex min-w-0 flex-1 items-center gap-3 no-underline" aria-label={`Open explanation of ${d.filename}`}>
                <AuthImage src={`/api/documents/${d.id}/thumbnail`} alt="" className="h-20 w-16 shrink-0 rounded-xl border border-line" fallback={<FileText className="h-6 w-6" />} />
                <span className="min-w-0"><span className="block truncate font-semibold">{d.filename}</span>
                  <span className="block text-sm text-muted">{fmtDate(d.doc_date ? Date.parse(d.doc_date) / 1000 : d.created_at)} · {fmtBytes(d.size_bytes)} · {d.page_count} page{d.page_count === 1 ? "" : "s"}</span>
                  <span className="mt-1 flex flex-wrap gap-1.5"><Badge tone="good">{d.user_category ? CATEGORY_LABEL[d.user_category] : DOC_TYPE_LABEL[d.doc_type]}</Badge>
                    {d.error && <Badge tone="warn">Needs a clearer copy</Badge>}</span></span>
              </Link>
              <button className="btn btn-sm hidden sm:inline-flex" onClick={() => setPreview(d)}><Eye className="h-4 w-4" aria-hidden /> Preview</button>
              <button className="btn btn-icon sm:hidden" onClick={() => setPreview(d)} aria-label={`Preview ${d.filename}`}><Eye className="h-5 w-5" /></button>
              <button className="btn btn-icon" onClick={() => setConfirmDel(d)} aria-label={`Delete ${d.filename}`}><Trash2 className="h-5 w-5" /></button>
            </article>))}
      </section>

      <ResultsOverTime />

      <Note tone="info">Files are stored privately on the clinic&apos;s system and are visible only to you and your care team. You can delete a document at any time.</Note>
      {latest && <Link href={reportHref(latest.id)} className="btn btn-primary w-full !py-3">Continue to my report summary <ArrowRight className="h-5 w-5" aria-hidden /></Link>}

      {preview && <DocumentPreview id={preview.id} name={preview.filename} mime={preview.mime} onClose={() => setPreview(null)} />}
      {confirmDel && <ConfirmDialog title="Delete this document?" message={`"${confirmDel.filename}" and everything read from it will be permanently deleted.`} confirmLabel="Delete" danger onConfirm={() => remove(confirmDel)} onCancel={() => setConfirmDel(null)} />}
    </div>
  );
}
