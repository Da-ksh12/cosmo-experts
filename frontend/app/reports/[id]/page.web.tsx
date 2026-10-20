"use client";

import { AlertTriangle, BarChart3, Check, ChevronDown, ExternalLink, FileText, FlagTriangleRight, Globe, Lightbulb, MessageCircle, MessagesSquare, ScrollText } from "lucide-react";
import Link from "next/link";
import { useParams, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import AudioPlayer from "@/components/AudioPlayer";
import DocumentPreview from "@/components/DocumentPreview";
import { ScreenBar } from "@/components/Shell";
import { AuthImage, ErrorState, LoadingState, Note, PageHeading, Pill, StatusBadge, fmtDate, useToast } from "@/components/ui";
import { api, CATEGORY_LABEL, DOC_TYPE_LABEL, post } from "@/lib/api";

type Field = { id: string; name: string; value: string; unit: string; ref_range: string; source_page: number; source_text: string; confidence: number; flag: string; review_status: string; patient_flag: string };
type Page = { page_no: number; text: string; method: string; ocr_confidence: number | null };
type Detail = { document: { id: string; filename: string; doc_type: string; user_category: string; doc_date: string; page_count: number; mime: string; review_status: string; created_at: number }; pages: Page[]; fields: Field[] };
type Section = { id?: string; title: string; summary: string; items: { text: string; field_id?: string; source?: { page: number }; citation?: { page?: number; kind?: string; title?: string } }[] };
type Explanation = { language: string; speech_language: string; sections: Section[]; spoken_text: string; disclaimer: string; clinician_review_message: string; extraction_warnings: string[]; translation_notice?: string | null };

const TABS = [{ id: "summary", label: "Summary", Icon: ScrollText }, { id: "results", label: "Key Results", Icon: BarChart3 }, { id: "detail", label: "Detailed View", Icon: FileText }, { id: "questions", label: "Questions", Icon: MessagesSquare }] as const;
const LANGS = [["en", "English"], ["hi", "हिन्दी"], ["hinglish", "Hinglish"]] as const;

function flagInfo(f: Field): { tone: "good" | "warn" | "info"; label: string } {
  switch (f.flag) {
    case "printed_flag_low": return { tone: "warn", label: "Marked low by report" };
    case "printed_flag_high": return { tone: "warn", label: "Marked high by report" };
    case "outside_printed_range": return { tone: "warn", label: "Outside printed range" };
    case "within_printed_range": return { tone: "good", label: "Inside printed range" };
    default: return { tone: "info", label: f.ref_range ? "Range printed" : "No range printed" };
  }
}

export default function ReportPage() {
  const params = useParams<{ id?: string }>();
  const search = useSearchParams();
  const id = params?.id ?? search.get("id") ?? "";            // /reports/<id> on the web; /reports/view/?id=<id> in the Android bundle (one static page)
  const toast = useToast();
  const [d, setD] = useState<Detail | null>(null);
  const [ex, setEx] = useState<Explanation | null>(null);
  const [err, setErr] = useState("");
  const [lang, setLang] = useState("en");
  const [tab, setTab] = useState<(typeof TABS)[number]["id"]>("summary");
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | null>(null);
  const [orig, setOrig] = useState(false);

  const load = useCallback(() => {
    api<Detail>(`/api/documents/${id}`).then((x) => { setErr(""); setD(x); }).catch((e) => setErr(e.message));
  }, [id]);
  useEffect(load, [load]);
  useEffect(() => { api<Explanation>(`/api/documents/${id}/explanation?lang=${lang}`).then(setEx).catch((e) => setErr(e.message)); }, [id, lang]);

  async function flag(f: Field) {
    try { await post(`/api/documents/${id}/fields/${f.id}/flag`, { reason: "Patient thinks this value was read incorrectly" }); toast("Thanks. This value is flagged for your clinician to check."); load(); }
    catch (e) { toast(e instanceof Error ? e.message : "Could not flag this value.", "bad"); }
  }

  if (err && !d) return <ErrorState message={err} onRetry={load} />;
  if (!d || !ex) return <LoadingState label="Opening your report…" />;
  const doc = d.document;
  const cur = d.pages.find((p) => p.page_no === page) ?? d.pages[0];
  const outside = d.fields.filter((f) => ["printed_flag_low", "printed_flag_high", "outside_printed_range"].includes(f.flag)).length;
  const byId = (k: string) => ex.sections.find((s) => s.id === k);
  const questions = byId("questions") ?? ex.sections.find((s) => s.items.length && !s.items[0].source && !s.items[0].field_id);
  const found = byId("found") ?? ex.sections[0], uncertain = byId("uncertain") ?? ex.sections[1];
  const printed = ex.sections.filter((s) => ["facts", "medicines", "background"].includes(s.id ?? "") && (s.items.length > 0 || s.summary));

  return (
    <div className="space-y-6">
      <ScreenBar back="/reports" />
      <PageHeading eyebrow="Report analysis" title="Your Report Summary" subtitle="A simple explanation of what your report says, in easy language." />

      <section className="card flex flex-wrap items-center gap-4 !p-4" aria-label="Report">
        <AuthImage src={`/api/documents/${id}/thumbnail`} alt="" className="h-20 w-16 rounded-xl border border-line" fallback={<FileText className="h-6 w-6" />} />
        <div className="min-w-[9rem] flex-1 basis-40"><h3 className="break-words font-sans !text-lg">{doc.filename}</h3>
          <p className="text-sm text-muted">{fmtDate(doc.doc_date ? Date.parse(doc.doc_date) / 1000 : doc.created_at)} · {doc.page_count} page{doc.page_count === 1 ? "" : "s"}</p>
          <Pill tone="good">{doc.user_category ? CATEGORY_LABEL[doc.user_category] : DOC_TYPE_LABEL[doc.doc_type]}</Pill></div>
        <button className="btn w-full sm:w-auto" onClick={() => setOrig(true)}>View Original <ExternalLink className="h-4 w-4" aria-hidden /></button>
      </section>

      <div className="scroll-x" role="tablist" aria-label="Report sections">
        {TABS.map(({ id: t, label, Icon }) => <button key={t} role="tab" aria-selected={tab === t} className="chip" onClick={() => setTab(t)}><Icon className="h-5 w-5" aria-hidden />{label}</button>)}
      </div>

      <Note tone="info"><span className="mr-2"><StatusBadge status="clinician_review_required" /></span>{ex.clinician_review_message}{doc.review_status === "reviewed_by_clinician" && " A clinician has reviewed this document."}</Note>
      {ex.extraction_warnings.map((w) => <Note key={w} tone="warn">{w}</Note>)}
      {ex.translation_notice && <Note tone="warn">{ex.translation_notice}</Note>}

      {tab === "summary" && (
        <div className="space-y-5" role="tabpanel">
          <section className="card space-y-4" aria-label="Plain-language explanation">
            <div className="flex flex-wrap items-center justify-between gap-3"><h2>Plain-language explanation</h2>
              <label className="flex items-center gap-2 rounded-full border border-line bg-white px-3 text-sm"><Globe className="h-4 w-4" aria-hidden /><span className="sr-only">Language</span>
                <select className="bg-transparent py-2 font-semibold" value={lang} onChange={(e) => setLang(e.target.value)} aria-label="Language">{LANGS.map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label></div>
            <Pill tone="gold"><Check className="h-3.5 w-3.5" aria-hidden /> Based only on what your report states</Pill>
            <div className="space-y-3 rounded-2xl bg-sage p-4"><p>{found.summary}</p>
              {outside > 0 && <p>{outside} result{outside === 1 ? " is" : "s are"} marked low/high or outside the range printed on the report. See Key Results.</p>}
              <p className="text-muted">{uncertain.summary}</p></div>
            <AudioPlayer key={`${lang}:${ex.spoken_text.length}`} text={ex.spoken_text} language={ex.speech_language} />
          </section>

          {printed.map((s) => (
            <section key={s.id} className="card space-y-3" aria-label={s.title}><h2>{s.title}</h2>
              {s.summary && <p className="text-sm text-muted">{s.summary}</p>}
              <ul className="space-y-2">{s.items.map((it, i) => (
                <li key={i} className="rounded-2xl bg-sage p-3"><p className="whitespace-pre-wrap">{it.text}</p>
                  {it.citation?.kind === "document" && it.citation.page ? <button className="btn btn-sm mt-2" onClick={() => { setPage(it.citation!.page!); setTab("detail"); }}>See original page {it.citation.page}</button> : null}</li>))}</ul>
            </section>))}

          <section className="card space-y-3" aria-label="Key results preview"><div className="flex items-center justify-between"><h2>Key Results</h2><button className="font-semibold text-teal" onClick={() => setTab("results")}>View all results</button></div>
            {d.fields.length === 0 ? <p className="text-muted">We couldn&apos;t confidently read individual results from this document. Please check the original or upload a clearer copy.</p>
              : <ResultRows fields={d.fields.slice(0, 3)} open={open} setOpen={setOpen} flag={flag} goto={(p) => { setPage(p); setTab("detail"); }} />}</section>
        </div>
      )}

      {tab === "results" && (
        <section className="card space-y-3" role="tabpanel" aria-label="All results">
          <div><h2>Results from your report</h2><p className="text-sm text-muted">Values and units are copied exactly as printed. We never convert units or add ranges that the report doesn&apos;t print.</p></div>
          {d.fields.length === 0 ? <p className="text-muted">No individual results were found.</p> : <ResultRows fields={d.fields} open={open} setOpen={setOpen} flag={flag} goto={(p) => { setPage(p); setTab("detail"); }} />}
        </section>
      )}

      {tab === "detail" && (
        <section className="card space-y-4" role="tabpanel" aria-label="Detailed view">
          <div className="flex flex-wrap items-center justify-between gap-2"><h2>Original text, page {cur?.page_no}</h2>
            <div className="flex flex-wrap gap-2">{d.pages.map((p) => <button key={p.page_no} className="chip !px-4 !py-2" aria-pressed={p.page_no === cur?.page_no} onClick={() => setPage(p.page_no)} aria-label={`Page ${p.page_no}`}>{p.page_no}</button>)}</div></div>
          <pre className="max-h-[28rem] overflow-auto whitespace-pre-wrap rounded-2xl bg-ivory p-4 text-sm">{cur?.text || "No readable text was found on this page. Please upload a clearer copy."}</pre>
          <p className="text-sm text-muted">Read by: {cur?.method === "embedded" ? "text inside the file" : cur?.method === "ocr" ? "text recognition (may contain mistakes)" : cur?.method}</p>
          <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Scrollable table"><table><thead><tr><th>Test</th><th>Value</th><th>Unit</th><th>Printed range</th><th>Page</th><th>Confidence</th><th>Review</th></tr></thead>
            <tbody>{d.fields.filter((f) => f.source_page === cur?.page_no).map((f) => <tr key={f.id}><td>{f.name}</td><td>{f.value}</td><td>{f.unit}</td><td>{f.ref_range || "—"}</td><td>{f.source_page}</td><td>{Math.round(f.confidence * 100)}%</td><td>{f.review_status}</td></tr>)}</tbody></table></div>
        </section>
      )}

      {tab === "questions" && (
        <section className="card space-y-4" role="tabpanel" aria-label="Questions">
          <h2>{questions ? questions.title : "Questions you could ask your doctor"}</h2>
          <ul className="space-y-2">{(questions?.items ?? []).map((q) => <li key={q.text} className="rounded-2xl bg-sage p-3">{q.text}</li>)}</ul>
          <Link className="btn btn-primary" href="/ask"><MessageCircle className="h-5 w-5" aria-hidden /> Ask a follow-up question</Link>
        </section>
      )}

      <section className="card-sage flex flex-wrap items-center gap-4"><Lightbulb className="h-9 w-9 text-gold" strokeWidth={1.4} aria-hidden />
        <div className="min-w-[min(12rem,100%)] flex-1 basis-48"><h3>What this generally means</h3><p className="text-muted">Clinic-approved explanations of common terms are available in the assistant.</p></div>
        <Link className="btn btn-primary" href="/ask?q=What%20is%20a%20reference%20range%3F">Read explanation</Link></section>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Link className="btn !py-3" href="/ask"><MessageCircle className="h-5 w-5" aria-hidden /> Ask a Question</Link>
        <Link className="btn btn-primary !py-3" href="/care">Doctor review</Link>
      </div>
      <p className="text-sm text-muted">{ex.disclaimer}</p>
      {orig && <DocumentPreview id={doc.id} name={doc.filename} mime={doc.mime} onClose={() => setOrig(false)} />}
    </div>
  );
}

function ResultRows({ fields, open, setOpen, flag, goto }: { fields: Field[]; open: string | null; setOpen: (s: string | null) => void; flag: (f: Field) => void; goto: (page: number) => void }) {
  return (
    <ul className="divide-y divide-line">
      {fields.map((f) => {
        const fi = flagInfo(f), isOpen = open === f.id;
        return (
          <li key={f.id}>
            <button className="flex w-full items-center gap-3 py-3 text-left" onClick={() => setOpen(isOpen ? null : f.id)} aria-expanded={isOpen}>
              <span className={`grid h-11 w-11 shrink-0 place-items-center rounded-full ${fi.tone === "good" ? "bg-[var(--good-bg)] text-[var(--good-ink)]" : fi.tone === "warn" ? "bg-[var(--bad-bg)] text-[var(--bad-ink)]" : "bg-sage text-teal"}`}>{fi.tone === "good" ? <Check className="h-5 w-5" aria-hidden /> : <AlertTriangle className="h-5 w-5" aria-hidden />}</span>
              <span className="min-w-0 flex-1"><span className="block font-semibold">{f.name}</span><span className="block whitespace-nowrap text-muted">{f.value} {f.unit}</span></span>
              <span className="max-w-[46%] text-right"><Pill tone={fi.tone}>{fi.label}</Pill><span className="mt-1 block text-xs text-muted">{f.ref_range ? `Printed range: ${f.ref_range}` : "No printed range"}</span></span>
              <ChevronDown className={`h-5 w-5 shrink-0 text-muted transition ${isOpen ? "rotate-180" : ""}`} aria-hidden />
            </button>
            {isOpen && (
              <div className="pop mb-3 space-y-2 rounded-2xl bg-ivory p-3 text-sm">
                <p><strong>From your report (page {f.source_page}):</strong></p>
                <p className="rounded-xl bg-white p-2 font-mono text-xs">{f.source_text}</p>
                <p className="text-muted">Read with {Math.round(f.confidence * 100)}% confidence · Review status: {f.review_status}{f.patient_flag ? " · flagged by you" : ""}</p>
                <div className="flex flex-wrap gap-2"><button className="btn btn-sm" onClick={() => goto(f.source_page)}>See original page</button>
                  {!f.patient_flag && <button className="btn btn-sm" onClick={() => flag(f)}><FlagTriangleRight className="h-4 w-4" aria-hidden /> This looks wrong</button>}</div>
              </div>)}
          </li>);
      })}
    </ul>
  );
}
