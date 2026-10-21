"use client";

import { AlertTriangle, ArrowRight, BookOpen, CalendarPlus, Camera, Check, ExternalLink, FileText, Info, Phone, ShieldCheck, Sparkles, Stethoscope, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Details, Note } from "@/components/ui";
import { ApiError, post } from "@/lib/api";
import { Card, CardAction, CardSection, ReportItem } from "@/lib/assistant";
import { reportHref } from "@/lib/links";

type Outcome = { state: string; result?: { message?: string; code?: string } | null; replayed?: boolean };

const SECTION_EDGE: Record<string, string> = { urgent: "border-[var(--bad-ink)]", avoid: "border-[var(--warn-ink)]", care: "border-[var(--good-ink)]", info: "border-line" };
const BANNER: Record<string, string> = { emergency: "note-bad", urgent: "note-warn", prompt: "note-gold" };
const STATUS_CLASS: Record<ReportItem["status"], string> = { outside: "note-warn", within: "note-good", unknown: "note-info" };

const isPrimary = (a: CardAction) => a.id === "book" || a.id === "book-urgent" || a.id === "booking-page";
const hostOf = (u: string) => { try { return new URL(u).hostname.replace(/^www\./, ""); } catch { return ""; } };

function ActionIcon({ a }: { a: CardAction }) {
  const cls = "h-4 w-4 shrink-0";
  if (a.kind === "external") return a.href?.startsWith("tel:") ? <Phone className={cls} aria-hidden /> : <ExternalLink className={cls} aria-hidden />;
  if (a.kind === "report") return <FileText className={cls} aria-hidden />;
  if (a.kind === "navigate") return a.id.startsWith("book") ? <CalendarPlus className={cls} aria-hidden /> : a.id === "take-photo" ? <Camera className={cls} aria-hidden /> : a.id === "upload-report" ? <FileText className={cls} aria-hidden /> : <ArrowRight className={cls} aria-hidden />;
  return <Sparkles className={cls} aria-hidden />;
}

/** One action of a card. A message sends its question as if the person had typed it; the others open a screen, a link or the phone's dialer. Nothing here changes anything by itself. */
export function Action({ a, busy, onSend }: { a: CardAction; busy: boolean; onSend: (text: string) => void }) {
  const cls = `${isPrimary(a) ? "btn btn-primary btn-sm" : "chip"} !whitespace-normal text-left no-underline`;
  const body = <><ActionIcon a={a} /><span>{a.label}</span></>;
  if (a.kind === "message") return <button type="button" className={cls} disabled={busy} data-action={a.id} onClick={() => onSend(a.text ?? "")}>{body}</button>;
  if (a.kind === "navigate") return <Link href={a.route ?? "/"} className={cls} data-action={a.id}>{body}</Link>;
  if (a.kind === "report") return <Link href={reportHref(a.document_id)} className={cls} data-action={a.id}>{body}</Link>;
  if (a.kind === "external") return <a href={a.href} className={cls} data-action={a.id} {...(a.href?.startsWith("https://") ? { target: "_blank", rel: "noopener noreferrer" } : {})}>{body}</a>;
  return null;
}

/** A change the assistant proposed (for example "ask the clinic to review my report"). It is done only when the person presses the button; the server records it and says what happened. */
function Proposal({ a }: { a: CardAction }) {
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<Outcome | string | null>(null);
  async function decide(verb: "confirm" | "cancel") {
    setBusy(true);
    try { setOut(await post<Outcome>(`/api/v1/agent/actions/${encodeURIComponent(a.proposal_id ?? "")}/${verb}`)); }
    catch (e) { setOut(e instanceof ApiError ? e.message : "That could not be done. Please try again."); }
    finally { setBusy(false); }
  }
  return (
    <div className="space-y-2 rounded-2xl border border-line bg-ivory p-3" data-proposal={a.proposal_id}>
      <p className="font-semibold">{a.label}</p>
      {!out && (
        <div className="flex flex-wrap gap-2">
          <button type="button" className="btn btn-primary btn-sm" disabled={busy} onClick={() => void decide("confirm")}><Check className="h-4 w-4" aria-hidden /> Yes, do this</button>
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => void decide("cancel")}><X className="h-4 w-4" aria-hidden /> No, don&apos;t</button>
        </div>)}
      {typeof out === "string" && <Note tone="bad">{out}</Note>}
      {out && typeof out !== "string" && (out.state === "executed" ? <Note tone="good">Done.{out.replayed ? " (This had already been done.)" : ""}</Note>
        : out.state === "cancelled" ? <Note tone="info">Cancelled. Nothing was done.</Note>
        : <Note tone="bad">That could not be done{out.result?.message ? `: ${out.result.message}` : "."}</Note>)}
    </div>
  );
}

function Section({ s }: { s: CardSection }) {
  const tone = s.tone ?? "info";
  return (
    <section className={`space-y-1.5 border-l-4 pl-3 ${SECTION_EDGE[tone] ?? SECTION_EDGE.info}`} aria-label={s.title || undefined} data-section={s.id}>
      {s.title && <h4 className="flex items-center gap-1.5 font-semibold text-teal-dark">{tone === "urgent" || tone === "avoid" ? <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden /> : null}{s.title}</h4>}
      {s.items.length === 1 && !s.title ? <p>{s.items[0]}</p> : <ul className="list-disc space-y-1 pl-5">{s.items.map((t, i) => <li key={i}>{t}</li>)}</ul>}
    </section>
  );
}

/** The values of a report as it printed them. A result is called "outside the printed range" only when the report printed a range; otherwise it says so. It never says what a value means for the person. */
function ReportItems({ items, warnings }: { items: ReportItem[]; warnings?: string[] }) {
  return (
    <section className="space-y-2" aria-label="Values in your report" data-report-items>
      {items.length > 0 && <ul className="space-y-2">{items.map((it) => (
        <li key={it.field_id} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-2xl border border-line bg-white p-3">
          <span className="min-w-0 flex-1 basis-40 font-semibold">{it.name}</span>
          <span className="tabular-nums">{it.value}{it.unit ? ` ${it.unit}` : ""}</span>
          <span className="text-sm text-muted">{it.range ? `Printed range: ${it.range}` : "No range printed"}</span>
          <span className={`badge ${STATUS_CLASS[it.status]}`}>{it.status_text}</span>
          {it.page != null && <span className="text-xs text-muted">page {it.page}</span>}
        </li>))}</ul>}
      {warnings && warnings.length > 0 && <Note tone="warn"><ul className="list-disc pl-4">{warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></Note>}
    </section>
  );
}

export default function AssistantCard({ card, busy = false, latest = false, onSend }: { card: Card; busy?: boolean; latest?: boolean; onSend: (text: string) => void }) {
  const esc = card.escalation;
  const acts = (card.actions ?? []).filter((a) => a.kind !== "confirm");
  const proposals = (card.actions ?? []).filter((a) => a.kind === "confirm");
  const refTier = card.review?.tier === "reference";
  const sources = card.sources ?? [];

  return (
    <div className="space-y-3" data-card-route={card.route}>
      {esc && (
        <section className={`note ${BANNER[esc.level] ?? "note-info"} !flex-col !items-stretch`} role={esc.level === "emergency" ? "alert" : "status"} data-escalation={esc.level} aria-label={esc.title}>
          <p className="flex items-center gap-2 font-semibold"><AlertTriangle className="h-5 w-5 shrink-0" aria-hidden />{esc.title}</p>
          {esc.message && <p className="whitespace-pre-line">{esc.message}</p>}
          {esc.level === "emergency" && esc.reason && <p className="text-sm" data-escalation-reason>Why I am saying this: {esc.reason}</p>}
          {esc.actions && esc.actions.length > 0 && <div className="flex flex-wrap gap-2 pt-1">{esc.actions.map((a) => <Action key={a.id} a={a} busy={busy} onSend={onSend} />)}</div>}
        </section>)}

      {card.title && <h3 className="font-serif text-xl text-teal-dark">{card.title}</h3>}
      {card.summary && <p className="whitespace-pre-line">{card.summary}</p>}
      {card.noted && card.noted.length > 0 && <p className="flex flex-wrap items-center gap-1.5 text-sm text-muted"><span>What you told me:</span>{card.noted.map((n) => <span key={n} className="badge note-info">{n}</span>)}</p>}

      {card.report && <ReportItems items={card.report.items} warnings={card.report.extraction_warnings} />}
      {card.sections.map((s) => <Section key={s.id} s={s} />)}

      {card.terms && card.terms.length > 0 && (
        <Details title="What these words mean"><dl className="space-y-1.5 text-sm">{card.terms.map((t) => <div key={t.term}><dt className="inline font-semibold">{t.term}: </dt><dd className="inline">{t.plain}</dd></div>)}</dl></Details>)}

      {latest && card.follow_ups && card.follow_ups.length > 0 && (
        <div className="space-y-3" data-follow-ups>
          {card.follow_ups.map((q) => (
            <div key={q.id} className="space-y-1.5">
              <p className="font-semibold">{q.text}</p>
              <div className="flex flex-wrap gap-2">{q.choices.map((c) => <button key={c.label} type="button" className="chip !whitespace-normal text-left" disabled={busy} data-follow-up={q.id} onClick={() => onSend(c.reply)}>{c.label}</button>)}</div>
            </div>))}
        </div>)}

      {proposals.map((a) => <Proposal key={a.id} a={a} />)}
      {acts.length > 0 && <div className="flex flex-wrap gap-2" aria-label="What you can do next" data-card-actions>{acts.map((a) => <Action key={a.id} a={a} busy={busy} onSend={onSend} />)}</div>}

      {card.review?.label && (refTier
        ? <p className="flex items-start gap-2 rounded-2xl bg-[var(--gold-soft)] px-3 py-2 text-sm text-[#6d4f1c]" data-review-tier="reference"><Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />{card.review.label}</p>
        : <p className="flex items-start gap-2 text-xs text-muted" data-review-tier={card.review.tier}><ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-teal" aria-hidden />{card.review.label}</p>)}

      {sources.length > 0 && (
        <Details title={`Sources (${sources.length})`}>
          <ul className="space-y-2 text-sm">{sources.map((s) => (
            <li key={s.id} className="flex items-start gap-2"><BookOpen className="mt-0.5 h-4 w-4 shrink-0 text-teal" aria-hidden />
              <span className="min-w-0"><a href={s.url} target="_blank" rel="noopener noreferrer" className="font-semibold underline">{s.publisher}: {s.title}</a>
                <span className="block text-xs text-muted">{hostOf(s.url)}{s.page_reviewed ? ` · page checked ${s.page_reviewed}` : ""}</span></span></li>))}</ul>
          {card.attributions && card.attributions.length > 0 && <p className="mt-2 text-xs text-muted">{card.attributions.join(" ")}</p>}
        </Details>)}

      {card.limits && card.limits.length > 0 && <p className="flex items-start gap-2 text-xs text-muted"><Stethoscope className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />{card.limits.join(" ")}</p>}
    </div>
  );
}
