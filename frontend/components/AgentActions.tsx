"use client";

import { Check, FileText, Send, Sparkles, X } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { Note, Pill } from "@/components/ui";
import { ApiError, post } from "@/lib/api";

type Citation = { kind: string; document_id?: string; page?: number; title?: string; excerpt?: string; item?: string };
type Proposed = { id: string; tool: string; summary: string; state: string };
type Reply = { status: string; answer: string; sources: Citation[]; actions: Proposed[]; intent: string; notes: string[]; disclaimers: string[] };
type Outcome = { state: string; result?: { message?: string; code?: string } | null; replayed?: boolean };

/** Things the assistant can do for the person, by tool. Anything that CHANGES something is only proposed: it is carried out by the person pressing Confirm, never by the chat itself. */
const SHORTCUTS = [
  { label: "Show my documents", message: "show my documents" },
  { label: "Ask the clinic to look at my latest report", message: "ask the clinic to review my latest report" },
  { label: "Mark my notifications as read", message: "mark my notifications as read" },
  { label: "See the price list", message: "show the price list" },
];

const TONE: Record<string, "info" | "warn" | "bad" | "good"> = { urgent: "warn", refused: "info", abstained: "info", clarification: "info", error: "bad", needs_confirmation: "warn", answered: "good" };
const LABEL: Record<string, string> = { urgent: "Please read", refused: "I can't help with that", abstained: "Nothing reliable found", clarification: "I need a little more", error: "That didn't work", needs_confirmation: "Waiting for you", answered: "Answer" };

export default function AgentActions() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [reply, setReply] = useState<Reply | null>(null);
  const [outcome, setOutcome] = useState<Record<string, Outcome | string>>({});

  async function ask(message: string) {
    if (!message.trim()) return;
    setBusy(true); setErr(""); setReply(null); setOutcome({}); setText("");
    try { setReply(await post<Reply>("/api/v1/agent/chat", { message })); }
    catch (e) { setErr(e instanceof ApiError ? e.message : "We couldn't reach the assistant. Please try again."); }
    finally { setBusy(false); }
  }

  async function decide(a: Proposed, verb: "confirm" | "cancel") {
    setBusy(true);
    try {
      const o = await post<Outcome>(`/api/v1/agent/actions/${a.id}/${verb}`);
      setOutcome((x) => ({ ...x, [a.id]: o }));
    } catch (e) {
      setOutcome((x) => ({ ...x, [a.id]: e instanceof ApiError ? e.message : "That could not be done. Please try again." }));
    } finally { setBusy(false); }
  }

  return (
    <section className="card space-y-3" aria-label="Things the assistant can do for you">
      <h3 className="flex items-center gap-2"><Sparkles className="h-5 w-5 text-gold" aria-hidden /> Things I can do for you</h3>
      <p className="text-sm text-muted">I can look things up in your own records. If something would be sent to the clinic or changed, I show you what it is and wait for you to confirm.</p>
      <div className="flex flex-wrap gap-2">{SHORTCUTS.map((s) => <button key={s.label} className="chip" style={{ whiteSpace: "normal", textAlign: "left" }} disabled={busy} onClick={() => void ask(s.message)}>{s.label}</button>)}</div>
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void ask(text); }}>
        <label htmlFor="agent-q" className="sr-only">Ask the assistant to do something</label>
        <input id="agent-q" className="input flex-1" maxLength={1000} value={text} onChange={(e) => setText(e.target.value)} placeholder="For example: explain my latest report" />
        <button className="btn btn-primary shrink-0" type="submit" disabled={busy || !text.trim()} aria-label="Send"><Send className="h-5 w-5" aria-hidden /></button>
      </form>

      {busy && !reply && <p className="text-muted" role="status">Working on it…</p>}
      {err && <Note tone="bad">{err}</Note>}

      {reply && (
        <div className="space-y-3" aria-live="polite">
          <Pill tone={TONE[reply.status] ?? "info"}>{LABEL[reply.status] ?? reply.status}</Pill>
          <p className="whitespace-pre-wrap">{reply.answer}</p>
          {reply.sources.length > 0 && (
            <div className="flex flex-wrap gap-2">{reply.sources.map((s, i) => s.kind === "document" && s.document_id
              ? <Link key={i} href={`/reports/${s.document_id}`} className="badge note-info no-underline"><FileText className="h-3.5 w-3.5" aria-hidden /> Your {s.item === "medication" ? "prescription" : "report"} · page {s.page}</Link>
              : <span key={i} className="badge note-gold">Clinic-approved: {s.title}</span>)}</div>)}

          {reply.actions.map((a) => {
            const o = outcome[a.id];
            return (
              <div key={a.id} className="rounded-2xl border border-line bg-ivory p-3 space-y-2">
                <p className="font-semibold">{a.summary}</p>
                {!o && (
                  <div className="flex flex-wrap gap-2">
                    <button className="btn btn-primary" disabled={busy} onClick={() => void decide(a, "confirm")}><Check className="h-5 w-5" aria-hidden /> Yes, do this</button>
                    <button className="btn" disabled={busy} onClick={() => void decide(a, "cancel")}><X className="h-5 w-5" aria-hidden /> No, don&apos;t</button>
                  </div>)}
                {typeof o === "string" && <Note tone="bad">{o}</Note>}
                {o && typeof o !== "string" && (o.state === "executed" ? <Note tone="good">Done.{o.replayed ? " (This had already been done.)" : ""}</Note>
                  : o.state === "cancelled" ? <Note tone="info">Cancelled. Nothing was done.</Note>
                  : <Note tone="bad">That could not be done{o.result?.message ? `: ${o.result.message}` : "."}</Note>)}
              </div>);
          })}

          {reply.notes.length > 0 && <p className="text-xs text-muted">{reply.notes.join(" ")}</p>}
          {reply.status !== "urgent" && <p className="text-xs text-muted">{reply.disclaimers.join(" ")} A clinician reviews what matters; I cannot diagnose or choose medicines.</p>}
        </div>)}
    </section>
  );
}
