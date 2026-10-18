"use client";

import { Bot, FileText, History, Lightbulb, Mic, Send, Sparkles, Stethoscope, Trash2, UserRound, MessageCircle } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import AudioPlayer from "@/components/AudioPlayer";
import { ScreenBar } from "@/components/Shell";
import VoiceInput from "@/components/VoiceInput";
import { ConfirmDialog, Dialog, Note, PageHeading, StatusBadge, fmtDateTime, useToast } from "@/components/ui";
import { api, del, post } from "@/lib/api";

type Source = { kind: string; document_id?: string; page?: number; excerpt?: string; title?: string; quote_id?: string };
type Msg = { role: "patient" | "assistant"; content: string; sources?: Source[]; status?: string; abstained?: boolean };
type Conv = { id: string; created_at: number; preview: string };

const MODES = [
  { Icon: MessageCircle, title: "Ask Anything", sub: "Get clear answers", key: "ask", href: "" },
  { Icon: Mic, title: "Voice Mode", sub: "Speak naturally", key: "voice", href: "" },
  { Icon: Lightbulb, title: "Suggested", sub: "Quick help", key: "suggested", href: "" },
  { Icon: Stethoscope, title: "Doctor Review", sub: "Care plan", key: "care", href: "/care" },
];

function Chat() {
  const toast = useToast();
  const params = useSearchParams();
  const [cid, setCid] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [suggest, setSuggest] = useState<string[]>([]);
  const [convs, setConvs] = useState<Conv[] | null>(null);
  const [showHist, setShowHist] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [voiceTrig, setVoiceTrig] = useState(0);
  const [voiceNote, setVoiceNote] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const sent = useRef(false);

  useEffect(() => {
    let live = true;
    post<{ id: string }>("/api/conversations").then((c) => live && setCid(c.id)).catch((e) => toast(e.message, "bad"));
    api<string[]>("/api/assistant/suggestions").then((s) => live && setSuggest(s)).catch(() => {});
    return () => { live = false; };
  }, [toast]);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, busy]);

  const send = useCallback(async (q: string, id = cid) => {
    if (!q.trim() || !id) return;
    setBusy(true); setText("");
    setMsgs((m) => [...m, { role: "patient", content: q }]);
    try {
      const r = await post<{ answer: string; sources: Source[]; status: string; abstained: boolean }>(`/api/conversations/${id}/messages`, { text: q });
      setMsgs((m) => [...m, { role: "assistant", content: r.answer, sources: r.sources, status: r.status, abstained: r.abstained }]);
    } catch (e) { toast(e instanceof Error ? e.message : "Something went wrong. Please try again.", "bad"); }
    finally { setBusy(false); }
  }, [cid, toast]);

  const qp = params.get("q");
  useEffect(() => { if (qp && cid && !sent.current) { sent.current = true; void send(qp, cid); } }, [qp, cid, send]);

  async function loadHistory() { setShowHist(true); setConvs(null); try { setConvs(await api<Conv[]>("/api/conversations")); } catch { setConvs([]); } }
  async function resume(c: Conv) {
    try {
      const rows = await api<{ role: "patient" | "assistant"; content: string; sources: Source[] }[]>(`/api/conversations/${c.id}`);
      setCid(c.id); setMsgs(rows.map((r) => ({ role: r.role, content: r.content, sources: r.sources }))); setShowHist(false);
    } catch (e) { toast(e instanceof Error ? e.message : "Could not open the conversation.", "bad"); }
  }
  async function clear() {
    setConfirm(false);
    try { await del(`/api/conversations/${cid}`); setMsgs([]); setCid((await post<{ id: string }>("/api/conversations")).id); toast("Conversation deleted."); } catch (e) { toast(e instanceof Error ? e.message : "Could not delete.", "bad"); }
  }

  const firstDoc = msgs.flatMap((m) => m.sources ?? []).find((s) => s.kind === "document");
  const runMode = (key: string) => {
    if (key === "ask") input.current?.focus();
    else if (key === "voice") setVoiceTrig((n) => n + 1);
    else document.getElementById("suggested")?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <div className="space-y-5">
      <ScreenBar back="/" right={<button className="btn btn-icon" onClick={loadHistory} aria-label="Conversation history"><History className="h-5 w-5" /></button>} />
      <div className="flex items-start justify-between gap-3"><div className="min-w-0 flex-1"><PageHeading eyebrow="AI consultation" title="Let's Understand Together" subtitle="Ask about your reports in simple language. Every answer shows where it came from." /></div>
        <button className="btn hidden md:inline-flex" onClick={loadHistory}><History className="h-5 w-5" aria-hidden /> History</button></div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {MODES.map(({ Icon, title, sub, key, href }) => {
          const body = <><Icon className="h-6 w-6 text-teal" strokeWidth={1.5} aria-hidden /><span className="font-semibold">{title}</span><span className="text-xs text-muted">{sub}</span></>;
          return href ? <Link key={title} href={href} className="card flex flex-col items-center gap-1 !p-3 text-center">{body}</Link>
            : <button key={title} className="card flex flex-col items-center gap-1 !p-3 text-center" onClick={() => runMode(key)}>{body}</button>;
        })}
      </div>

      <Note tone="info">I answer from your uploaded reports and the clinic&apos;s approved information. I can&apos;t diagnose or choose medicines. Your doctor does that.</Note>

      <section className="space-y-4" aria-live="polite" aria-label="Conversation">
        {msgs.map((m, i) => m.role === "patient" ? (
          <div key={i} className="rise ml-auto flex max-w-[88%] items-end gap-2 sm:max-w-[70%]">
            <div className="rounded-[22px] rounded-br-md bg-teal px-4 py-3 text-white"><p className="whitespace-pre-wrap">{m.content}</p></div>
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gold-soft text-gold"><UserRound className="h-5 w-5" aria-hidden /></span>
          </div>
        ) : (
          <div key={i} className="rise flex max-w-[96%] items-start gap-2 sm:max-w-[80%]">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-sage text-teal"><Bot className="h-6 w-6" aria-hidden /></span>
            <div className="card min-w-0 flex-1 space-y-3 !rounded-tl-md !p-4">
              <p className="whitespace-pre-wrap">{m.content}</p>
              {m.sources && m.sources.length > 0 && (
                <div className="flex flex-wrap gap-2">{m.sources.map((s, j) => s.kind === "document" ? (
                  <Link key={j} href={`/reports/${s.document_id}`} className="badge note-info no-underline"><FileText className="h-3.5 w-3.5" aria-hidden /> Your report · page {s.page}</Link>)
                  : s.kind === "knowledge_base" ? <span key={j} className="badge note-gold">Clinic-approved: {s.title}</span>
                  : <Link key={j} href="/quote" className="badge note-info no-underline">Your approved quotation</Link>)}</div>)}
              <div className="flex flex-wrap items-center gap-2">{m.status && <StatusBadge status={m.status} />}{m.abstained && <span className="text-sm text-muted">You can ask the clinic team to review this.</span>}</div>
              <AudioPlayer compact text={m.content.slice(0, 1500)} />
            </div>
          </div>))}
        {busy && <div className="flex items-center gap-2 text-muted" role="status"><Sparkles className="spin h-5 w-5" aria-hidden /> Looking through your documents…</div>}
        <div ref={end} />
      </section>

      <div className="flex flex-wrap gap-2" aria-label="Quick actions">
        {firstDoc && <Link href={`/reports/${firstDoc.document_id}`} className="chip"><FileText className="h-4 w-4" aria-hidden /> View Report</Link>}
        <button className="chip" onClick={() => send("What questions should I ask my doctor?")}><Stethoscope className="h-4 w-4" aria-hidden /> Questions for my doctor</button>
        <Link href="/care" className="chip"><Stethoscope className="h-4 w-4" aria-hidden /> Doctor Review</Link>
      </div>

      <section id="suggested" aria-label="Suggested questions" className="space-y-2">
        <h3 className="flex items-center gap-2"><Sparkles className="h-5 w-5 text-gold" aria-hidden /> Suggested Questions</h3>
        {suggest.length === 0 ? <p className="text-sm text-muted">Upload a report to get suggestions based on it.</p>
          : <div className="grid gap-2 sm:grid-cols-3">{suggest.map((s) => <button key={s} className="card !rounded-2xl !p-3 text-left text-sm" onClick={() => send(s)}>{s}</button>)}</div>}
      </section>

      <form onSubmit={(e) => { e.preventDefault(); void send(text); }} className="card sticky z-20 space-y-3 !p-3" style={{ bottom: "calc(var(--nav-h) + 8px)" }}>
        <label htmlFor="q" className="sr-only">Your question</label>
        <textarea id="q" ref={input} className="input !rounded-2xl" rows={2} maxLength={1000} value={text} onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); void send(text); } }} placeholder="Type your question in English, Hindi or Hinglish…" />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <VoiceInput trigger={voiceTrig} onText={(t) => { setText(t); setVoiceNote("Please check the words and correct them if needed before sending."); input.current?.focus(); }} onState={(s, m) => setVoiceNote(s === "error" ? m ?? "" : "")} />
          <button className="btn btn-primary shrink-0" type="submit" disabled={busy || !text.trim()} aria-label="Send question"><Send className="h-5 w-5" aria-hidden /> Send</button>
        </div>
        {voiceNote && <p className="text-sm text-[var(--warn-ink)]" role="status">{voiceNote}</p>}
        <div className="flex justify-between text-xs text-muted"><span>Not for emergencies. If you feel very unwell, contact emergency services.</span>
          {msgs.length > 0 && <button type="button" className="inline-flex items-center gap-1 font-semibold" onClick={() => setConfirm(true)}><Trash2 className="h-3.5 w-3.5" aria-hidden /> Delete conversation</button>}</div>
      </form>

      {showHist && (
        <Dialog title="Conversation history" onClose={() => setShowHist(false)}>
          {convs === null ? <p className="text-muted">Loading…</p> : convs.length === 0 ? <p className="text-muted">No earlier conversations.</p>
            : <ul className="space-y-2">{convs.map((c) => <li key={c.id}><button className="card w-full !p-3 text-left" onClick={() => resume(c)}><span className="block truncate font-semibold">{c.preview}</span><span className="text-sm text-muted">{fmtDateTime(c.created_at)}</span></button></li>)}</ul>}
        </Dialog>)}
      {confirm && <ConfirmDialog title="Delete this conversation?" message="The messages in this conversation will be permanently deleted." confirmLabel="Delete" danger onConfirm={clear} onCancel={() => setConfirm(false)} />}
    </div>
  );
}

export default function Ask() { return <Suspense fallback={null}><Chat /></Suspense>; }
