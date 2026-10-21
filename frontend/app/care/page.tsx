"use client";

import { BadgeCheck, CalendarCheck, Check, CircleDot, ClipboardList, FileText, MessageCircle, Stethoscope, StickyNote, UserRoundCheck } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ScreenBar } from "@/components/Shell";
import { AuthImage, EmptyState, ErrorState, LoadingState, Note, PageHeading, Pill, StatusBadge, fmtDate } from "@/components/ui";
import { api, DOC_TYPE_LABEL } from "@/lib/api";
import { reportHref } from "@/lib/links";

type Stage = { title: string; detail: string; status: "planned" | "in_progress" | "done"; done_at?: number | null };
type Care = {
  plan: { id: string; assessment: string; findings: string[]; stages: Stage[]; notes: string; recommended_item_ids: string[]; reviewed_at: number; progress: { done: number; total: number; percent: number | null } } | null;
  doctor: { name: string; credentials: string; verified: boolean } | null;
  photos: { id: string; region: string; verdict: string; created_at: number }[];
  documents: { id: string; filename: string; doc_type: string; created_at: number; review_status: string }[];
  awaiting_review: boolean;
};
const TABS = [["summary", "Summary"], ["findings", "Findings"], ["plan", "Care Plan"], ["notes", "Notes"]] as const;

export default function CarePage() {
  const [c, setC] = useState<Care | null>(null);
  const [err, setErr] = useState("");
  const [tab, setTab] = useState<(typeof TABS)[number][0]>("summary");
  const load = useCallback(() => { api<Care>("/api/care-plan").then((x) => { setErr(""); setC(x); }).catch((e) => setErr(e.message)); }, []);
  useEffect(load, [load]);

  if (err) return <ErrorState message={err} onRetry={load} />;
  if (!c) return <LoadingState />;
  const { plan, doctor } = c;

  return (
    <div className="space-y-6">
      <ScreenBar back="/" />
      <PageHeading eyebrow="Doctor review" title="Expert Review & Care Plan" />

      {doctor && (
        <section className="card flex flex-wrap items-center gap-4 !p-4" aria-label="Reviewing clinician">
          <span className="grid h-16 w-16 place-items-center rounded-full bg-teal text-white"><Stethoscope className="h-8 w-8" strokeWidth={1.4} aria-hidden /></span>
          <div className="min-w-[10rem] flex-1 basis-44"><h3 className="font-sans !text-lg">{doctor.name}{doctor.verified && <BadgeCheck className="ml-1 inline h-5 w-5 text-teal" aria-label="Verified" />}</h3>
            <p className="text-sm text-muted">{doctor.credentials || "Credentials not provided"}</p></div>
          {doctor.verified ? <Pill tone="gold"><BadgeCheck className="h-3.5 w-3.5" aria-hidden /> Verified clinician</Pill> : <Pill tone="info">Credentials not yet verified</Pill>}
        </section>
      )}

      {!plan && (
        <EmptyState icon={<ClipboardList className="h-7 w-7" />} title="No approved care plan yet"
          action={<Link href="/photo" className="btn btn-primary">Add a photo</Link>}>
          {c.awaiting_review ? "A plan appears here once a doctor has reviewed and approved your submissions." : "Add a photo or a report and your care team will review it."}
        </EmptyState>
      )}

      {plan && (
        <>
          <div className="scroll-x" role="tablist" aria-label="Care plan sections">{TABS.map(([id, label]) => <button key={id} role="tab" aria-selected={tab === id} className="chip" onClick={() => setTab(id)}>{label}</button>)}</div>

          {tab === "summary" && (
            <div className="space-y-5" role="tabpanel">
              <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                <section className="card space-y-3" aria-label="Your photos"><h3>Your Photos</h3>
                  {c.photos.length === 0 ? <p className="text-sm text-muted">No photos on file.</p> : <div className="grid grid-cols-4 gap-2">{c.photos.slice(0, 4).map((p) => <AuthImage key={p.id} src={`/api/images/${p.id}/file?thumb=1`} alt={`${p.region} photo`} className="aspect-square w-full rounded-xl" />)}</div>}
                  <Link href="/history?kind=photos" className="text-sm font-semibold text-teal">View all ({c.photos.length})</Link></section>
                <section className="card space-y-3" aria-label="Your reports"><h3>Your Reports</h3>
                  {c.documents.length === 0 ? <p className="text-sm text-muted">No reports on file.</p> : <ul className="space-y-2">{c.documents.slice(0, 3).map((d) => (
                    <li key={d.id}><Link href={reportHref(d.id)} className="flex items-center gap-3 no-underline"><AuthImage src={`/api/documents/${d.id}/thumbnail`} alt="" className="h-12 w-10 rounded-lg border border-line" fallback={<FileText className="h-4 w-4" />} />
                      <span className="min-w-0"><span className="block truncate text-sm font-semibold">{d.filename}</span><span className="text-xs text-muted">{DOC_TYPE_LABEL[d.doc_type]} · {fmtDate(d.created_at)}</span></span></Link></li>))}</ul>}
                  <Link href="/history?kind=reports" className="text-sm font-semibold text-teal">View all ({c.documents.length})</Link></section>
              </div>

              <section className="card-sage space-y-3" aria-label="Doctor's assessment">
                <div className="flex flex-wrap items-center justify-between gap-2"><h2 className="flex items-center gap-2"><UserRoundCheck className="h-6 w-6 text-teal" aria-hidden /> Doctor&apos;s Assessment</h2>
                  <span className="flex items-center gap-2 text-sm text-muted">Reviewed {fmtDate(plan.reviewed_at)} <Pill tone="good"><Check className="h-3.5 w-3.5" aria-hidden /> Clinician-approved</Pill></span></div>
                <p className="whitespace-pre-wrap">{plan.assessment}</p>
                {plan.findings.length > 0 && <div className="flex flex-wrap gap-2">{plan.findings.map((f) => <Pill key={f} tone="gold">{f}</Pill>)}</div>}
              </section>
              <PlanTimeline plan={plan} />
            </div>
          )}

          {tab === "findings" && (
            <div className="space-y-4" role="tabpanel">
              <section className="card space-y-3"><h2>Clinician&apos;s findings</h2><Pill tone="good"><Check className="h-3.5 w-3.5" aria-hidden /> Approved by your clinician</Pill>
                {plan.findings.length ? <ul className="list-disc space-y-1 pl-5">{plan.findings.map((f) => <li key={f}>{f}</li>)}</ul> : <p className="text-muted">No individual findings were listed.</p>}</section>
              <section className="card-flat space-y-2"><h3>Automated checks on your submissions</h3><Pill tone="info">Not a clinical finding</Pill>
                <p className="text-sm text-muted">{c.photos.length} photo{c.photos.length === 1 ? "" : "s"} passed the photo-quality check and {c.documents.length} document{c.documents.length === 1 ? " was" : "s were"} read by the software. Software output is only a convenience for your clinician; it is never a diagnosis or a treatment decision.</p></section>
            </div>
          )}

          {tab === "plan" && <div role="tabpanel"><PlanTimeline plan={plan} detailed /></div>}

          {tab === "notes" && (
            <section className="card space-y-2" role="tabpanel"><h2 className="flex items-center gap-2"><StickyNote className="h-6 w-6 text-teal" aria-hidden /> Doctor&apos;s Notes</h2>
              {plan.notes ? <p className="whitespace-pre-wrap rounded-2xl bg-ivory p-4">{plan.notes}</p> : <p className="text-muted">Your doctor hasn&apos;t added notes.</p>}</section>
          )}

          <Note tone="info">This plan was written and approved by your clinician. This app does not choose treatments or medicines. If something changes or worries you, contact the clinic.</Note>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Link href="/ask" className="btn !py-3"><MessageCircle className="h-5 w-5" aria-hidden /> Ask a Question</Link>
            <Link href="/quote" className="btn btn-primary !py-3">View Treatment Options</Link>
          </div>
        </>
      )}
      <p className="sr-only"><StatusBadge status="clinician_review_required" /></p>
    </div>
  );
}

function PlanTimeline({ plan, detailed = false }: { plan: NonNullable<Care["plan"]>; detailed?: boolean }) {
  return (
    <section className="card space-y-4" aria-label="Care plan">
      <div className="flex flex-wrap items-center justify-between gap-2"><h2>Care Plan</h2>
        <Pill tone="good"><Check className="h-3.5 w-3.5" aria-hidden /> Clinician-approved</Pill></div>
      {plan.stages.length === 0 ? <p className="text-muted">No stages were set.</p> : (
        <ol className={detailed ? "space-y-4" : "grid gap-4 sm:grid-cols-4"}>
          {plan.stages.map((s, i) => (
            <li key={i} className={`flex gap-3 ${detailed ? "" : "sm:flex-col sm:items-center sm:text-center"}`}>
              <span className={`grid h-12 w-12 shrink-0 place-items-center rounded-full ${s.status === "done" ? "bg-teal text-white" : s.status === "in_progress" ? "bg-gold-soft text-gold" : "bg-line text-muted"}`}>
                {s.status === "done" ? <Check className="h-6 w-6" aria-hidden /> : s.status === "in_progress" ? <CircleDot className="h-6 w-6" aria-hidden /> : <CalendarCheck className="h-6 w-6" aria-hidden />}</span>
              <span><span className="block font-semibold">{i + 1}. {s.title}</span>
                <span className="block text-sm text-muted">{s.detail}</span>
                <span className="text-xs font-semibold text-muted">{s.status === "done" ? `Done${s.done_at ? ` · ${fmtDate(s.done_at)}` : ""}` : s.status === "in_progress" ? "In progress" : "Planned"}</span></span>
            </li>))}
        </ol>)}
      {plan.progress.percent !== null && <p className="text-sm text-muted">{plan.progress.done} of {plan.progress.total} stages completed, as recorded by your clinic.</p>}
    </section>
  );
}
