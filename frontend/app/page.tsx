"use client";

import { ArrowRight, Bot, ClipboardCheck, FileText, Flower2, Image as ImageIcon, ScanFace, Sparkles, User } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { AuthImage, Dialog, EmptyState, Leaf, Pill, fmtDate, useToast } from "@/components/ui";
import { useUser } from "@/components/Shell";
import { usePhotoFlow, Region } from "@/lib/photoFlow";

type HistoryItem = { kind: string; id: string; title: string; subtitle: string; ts: number; status: string; link: string; has_file?: boolean };

const ACTIONS = [
  { href: "/photo?region=face", Icon: ScanFace, title: "Face Analysis", sub: "Check a face photo is ready for the clinic" },
  { href: "/photo?region=hair_scalp", Icon: Flower2, title: "Hair & Scalp", sub: "Share a clear hair or scalp photo" },
  { href: "/photo?region=body", Icon: User, title: "Body Analysis", sub: "Photograph another skin area" },
  { href: "/reports", Icon: FileText, title: "Upload Reports", sub: "Lab reports, prescriptions, notes" },
];

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function HomePage() {
  const user = useUser();
  const router = useRouter();
  const toast = useToast();
  const { setRegion } = usePhotoFlow();
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [guide, setGuide] = useState(false);

  useEffect(() => { api<HistoryItem[]>("/api/history").then(setItems).catch(() => { setItems([]); toast("We couldn't load your recent activity.", "bad"); }); }, [toast]);

  const pick = (region: Region | "report" | "ask") => {
    setGuide(false);
    if (region === "report") router.push("/reports");
    else if (region === "ask") router.push("/ask");
    else { setRegion(region); router.push(`/photo?region=${region}`); }
  };

  return (
    <div className="space-y-8">
      <header className="relative rise pr-20 sm:pr-48">
        <Leaf className="pointer-events-none absolute -right-[8px] -top-6 h-36 w-36 sm:h-52 sm:w-52" />
        <p className="text-lg text-muted">{greeting()}{user ? `, ${user.name.split(" ")[0]}` : ""}</p>
        <h1 className="mt-1">How can we help you today?</h1>
        <p className="mt-3 max-w-lg text-muted">Share a photo, upload your reports or ask a question. We explain what your information says, and a doctor makes every medical decision.</p>
      </header>

      <section aria-label="Start here" className="grid grid-cols-[repeat(2,minmax(0,1fr))] gap-3 md:grid-cols-4">
        {ACTIONS.map(({ href, Icon, title, sub }, i) => (
          <Link key={href} href={href} className={`card flex min-h-[210px] flex-col justify-start gap-3 rise-${Math.min(i + 1, 3)}`}>
            <span className="grid h-24 place-items-center rounded-2xl bg-gradient-to-br from-sage to-gold-soft text-teal"><Icon className="h-11 w-11" strokeWidth={1.4} aria-hidden /></span>
            <span className="min-w-0 break-words"><span className="block font-serif text-lg font-semibold text-teal-dark">{title}</span><span className="block text-sm leading-snug text-muted">{sub}</span></span>
            <ArrowRight className="mt-auto h-5 w-5 self-end text-gold" aria-hidden />
          </Link>
        ))}
      </section>

      <section className="card-teal rise-2 flex flex-wrap items-center justify-between gap-4" aria-label="Guided assessment">
        <div className="max-w-md"><h2>Not sure what to choose?</h2>
          <p className="mt-1 text-white/85">Answer one quick question and we&apos;ll point you to the right place to start.</p>
          <button className="btn btn-gold mt-4" onClick={() => setGuide(true)}>Start Guided Assessment <ArrowRight className="h-4 w-4" aria-hidden /></button></div>
        <ClipboardCheck className="hidden h-24 w-24 text-gold/80 sm:block" strokeWidth={1.1} aria-hidden />
      </section>

      <section aria-label="Recent activity" className="space-y-3">
        <div className="flex items-center justify-between"><h2>Recent Activity</h2><Link href="/history" className="font-semibold text-teal">View all</Link></div>
        {items === null ? <div className="grid gap-3 sm:grid-cols-2"><div className="h-24 animate-pulse rounded-3xl bg-line" /><div className="h-24 animate-pulse rounded-3xl bg-line" /></div>
          : items.length === 0 ? <EmptyState icon={<Sparkles className="h-7 w-7" />} title="Nothing here yet">Your photos, reports and quotations will appear here after you add them.</EmptyState>
          : <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">{items.slice(0, 4).map((it) => (
            <Link key={`${it.kind}${it.id}`} href={it.link} className="card flex min-w-0 items-center gap-3 !p-3">
              <span className="h-16 w-16 shrink-0 overflow-hidden rounded-2xl">
                {it.kind === "photo" && it.has_file ? <AuthImage src={`/api/images/${it.id}/file?thumb=1`} alt="" className="h-16 w-16" fallback={<ImageIcon className="h-6 w-6" />} />
                  : it.kind === "report" || it.kind === "prescription" ? <AuthImage src={`/api/documents/${it.id}/thumbnail`} alt="" className="h-16 w-16" fallback={<FileText className="h-6 w-6" />} />
                  : <span className="grid h-16 w-16 place-items-center bg-sage text-teal"><FileText className="h-6 w-6" /></span>}
              </span>
              <span className="min-w-0 flex-1"><span className="block truncate font-semibold">{it.title}</span><span className="block truncate text-sm text-muted">{fmtDate(it.ts)}</span>
                <StatusPill kind={it.kind} status={it.status} /></span>
            </Link>))}</div>}
      </section>

      <Link href="/ask" className="card-sage rise-3 flex flex-wrap items-center gap-4 no-underline">
        <span className="grid h-16 w-16 place-items-center rounded-full bg-white text-teal"><Bot className="h-9 w-9" strokeWidth={1.4} aria-hidden /></span>
        <span className="min-w-[min(12rem,100%)] flex-1 basis-48"><span className="block font-serif text-xl font-semibold text-teal-dark">Chat with our AI Assistant</span>
          <span className="block text-muted">Ask about your reports. Answers show their sources.</span></span>
        <span className="btn btn-primary">Start Chat <ArrowRight className="h-4 w-4" aria-hidden /></span>
      </Link>

      {guide && (
        <Dialog title="What would you like to share?" onClose={() => setGuide(false)}>
          <p className="mb-4 text-muted">This only picks where to start. It is not a medical assessment.</p>
          <div className="grid gap-2">
            {([["face", "A concern on my face or skin"], ["hair_scalp", "A hair or scalp concern"], ["body", "A concern on another part of my body"], ["report", "I have medical reports to upload"], ["ask", "I have a question about my reports"]] as const).map(([k, label]) => (
              <button key={k} className="btn justify-start !rounded-2xl !py-3 text-left" onClick={() => pick(k)}>{label}</button>))}
          </div>
        </Dialog>
      )}
    </div>
  );
}

function StatusPill({ kind, status }: { kind: string; status: string }) {
  if (kind === "photo") return status === "reviewed" ? <Pill tone="good">Reviewed by clinician</Pill> : status === "accepted" ? <Pill tone="good">Photo accepted</Pill> : status === "uncertain" ? <Pill tone="warn">Check uncertain</Pill> : <Pill tone="bad">Needs retake</Pill>;
  if (kind === "quotation") return status === "approved" ? <Pill tone="good">Approved</Pill> : <Pill tone="warn">Awaiting approval</Pill>;
  if (kind === "report" || kind === "prescription") return status === "reviewed_by_clinician" ? <Pill tone="good">Reviewed</Pill> : <Pill tone="info">Under review</Pill>;
  return <Pill tone="good">Completed</Pill>;
}
