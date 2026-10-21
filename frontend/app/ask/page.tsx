"use client";

import { Bot, Crosshair, FileText, History, Loader2, Send, Sparkles, Trash2, UserRound, X } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import AssistantCard, { Action } from "@/components/AssistantCard";
import AudioPlayer from "@/components/AudioPlayer";
import PhotoAnalysisCard, { AskRequest, FocusInfo, Highlight, PendingUpload } from "@/components/PhotoAnalysisCard";
import { ScreenBar } from "@/components/Shell";
import VoiceInput from "@/components/VoiceInput";
import { ConfirmDialog, Dialog, Note, PageHeading, fmtDateTime, useToast } from "@/components/ui";
import { analysisStore, useActivePending, usePending } from "@/lib/analysisStore";
import { api, ApiError, del, post } from "@/lib/api";
import { Card, cardToSpeech, FALLBACK_CONCERNS, loadStarters, parseCard, Source, splitSources, StarterSet } from "@/lib/assistant";
import { reportHref } from "@/lib/links";
import { getAnalysis, nextNonce, TERMINAL } from "@/lib/photoAnalysis";

/** Ties a question to one photo assessment (and, if the person selected one, to one area of it). The server checks that it is theirs. */
type PhotoCtx = { analysis_id: string; observation_id?: string; intent?: string };
type Msg = { role: "patient" | "assistant"; content: string; sources?: Source[]; card?: Card | null; notice?: string; failed?: string; photoIds?: string[]; ctx?: PhotoCtx };
type Conv = { id: string; created_at: number; preview: string };
type Row = { role: "patient" | "assistant"; content: string; sources: Source[] };
type Reply = { answer: string; sources: Source[]; status: string; abstained: boolean; notice?: string; highlight?: string[]; analysis_id?: string; card?: unknown };

/** The assessment message the server records when photos are sent has one source per photo with its position; answers about a photo have sources without one. */
const assessmentIds = (sources?: Source[]) => (sources ?? []).filter((s) => s.kind === "photo_analysis" && typeof s.index === "number" && s.analysis_id).sort((a, b) => a.index! - b.index!).map((s) => s.analysis_id!);
const toMsg = (r: Row): Msg => {
  if (r.role !== "assistant") return { role: r.role, content: r.content, sources: r.sources };
  const { card, chips } = splitSources(r.sources);
  const ids = assessmentIds(chips);
  return ids.length ? { role: "assistant", content: r.content, photoIds: ids } : { role: "assistant", content: r.content, sources: chips, card };
};
const RESTORE_MS = 15 * 60_000;

function Chat() {
  const toast = useToast();
  const router = useRouter();
  const params = useSearchParams();
  const cp = params.get("c"), pendingId = params.get("pending"), qp = params.get("q");
  const [cid, setCid] = useState("");
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [busyPhoto, setBusyPhoto] = useState(false);
  const [starters, setStarters] = useState<StarterSet | null>(null);
  const [convs, setConvs] = useState<Conv[] | null>(null);
  const [showHist, setShowHist] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [voiceNote, setVoiceNote] = useState("");
  const [focus, setFocus] = useState<FocusInfo>(null);
  const [highlight, setHighlight] = useState<Highlight>(null);
  const [opening, setOpening] = useState(false);
  const [loadError, setLoadError] = useState("");
  const [missing, setMissing] = useState(false);
  const [watching, setWatching] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const end = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const sent = useRef(false);
  const loaded = useRef("");
  const restored = useRef(false);

  const pending = usePending(pendingId ?? watching);
  const pStage = pending?.stage, pConv = pending?.conversationId;
  const act = useActivePending();
  const sending = pStage === "uploading";

  useEffect(() => {
    let live = true;
    void loadStarters().then((s) => { if (live) setStarters(s); });
    return () => { live = false; };
  }, []);
  useEffect(() => { end.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [msgs, busy, pStage]);

  const openConversation = useCallback(async (id: string) => {
    const rows = await api<Row[]>(`/api/conversations/${encodeURIComponent(id)}`);
    setCid(id); setMsgs(rows.map(toMsg)); setFocus(null); setHighlight(null);
  }, []);

  // 1) a conversation named in the address (a reload, a link, or the place a finished upload moves to)
  useEffect(() => {
    if (!cp || loaded.current === cp) return;
    loaded.current = cp;
    setOpening(true); setLoadError("");
    openConversation(cp).catch((e) => { loaded.current = ""; setLoadError(e instanceof Error ? e.message : "We couldn't open this conversation."); }).finally(() => setOpening(false));
  }, [cp, reload, openConversation]);

  // 2) photos on their way: once the server has accepted them, the conversation with the assessment exists there, and this screen moves into it
  useEffect(() => {
    if (pStage !== "submitted" || !pConv || loaded.current === pConv) return;
    const id = pConv;
    loaded.current = id;
    setOpening(true); setLoadError("");
    openConversation(id).then(() => { setWatching(null); router.replace(`/ask?c=${encodeURIComponent(id)}`); })
      .catch((e) => { loaded.current = ""; setLoadError(e instanceof Error ? e.message : "We couldn't open your conversation."); }).finally(() => setOpening(false));
  }, [pStage, pConv, reload, openConversation, router]);

  // 3) the app does not know this upload (it was restarted): if the server has just recorded one, open that; otherwise say so rather than show something unrelated
  useEffect(() => {
    if (!pendingId || analysisStore.get(pendingId)) return;
    let live = true;
    (async () => {
      try {
        const c = (await api<Conv[]>("/api/conversations"))[0];
        if (!c || Date.now() / 1000 - c.created_at > RESTORE_MS / 1000) { if (live) setMissing(true); return; }
        const rows = await api<Row[]>(`/api/conversations/${encodeURIComponent(c.id)}`);
        if (!live) return;
        if (!rows.some((r) => r.role === "assistant" && assessmentIds(r.sources).length)) { setMissing(true); return; }
        loaded.current = c.id; setCid(c.id); setMsgs(rows.map(toMsg)); router.replace(`/ask?c=${encodeURIComponent(c.id)}`);
      } catch { if (live) setMissing(true); }
    })();
    return () => { live = false; };
  }, [pendingId, router]);

  // 4) coming back with nothing in the address: an upload that is still on its way, or an assessment that is still running, is where the person left it
  if (!cp && !pendingId && !watching && !qp && act) setWatching(act.id);                                  // follow that upload from here on (it leaves the "on its way" list once the server has it)
  useEffect(() => {
    if (cp || pendingId || qp || restored.current) return;
    restored.current = true;
    const last = analysisStore.lastSubmitted(RESTORE_MS);
    if (!last?.conversationId || !last.analysisIds[0]) return;
    const id = last.conversationId;
    getAnalysis(last.analysisIds[0]).then((a) => { if (!TERMINAL.includes(a.status)) { loaded.current = id; return openConversation(id).then(() => router.replace(`/ask?c=${encodeURIComponent(id)}`)); } }).catch(() => {});
  }, [cp, pendingId, qp, openConversation, router]);

  const send = useCallback(async (q: string, o: { ctx?: PhotoCtx | null } = {}) => {
    if (!q.trim()) return;
    const ctx: PhotoCtx | null = o.ctx !== undefined ? o.ctx : focus ? { analysis_id: focus.analysisId, ...(focus.observationId ? { observation_id: focus.observationId } : {}) } : null;
    setBusy(true); setBusyPhoto(!!ctx); setText("");
    if (input.current) input.current.style.height = "auto";
    setMsgs((m) => [...m, { role: "patient", content: q, ctx: ctx ?? undefined }]);
    try {
      let conv = cid;
      if (!conv) { conv = (await post<{ id: string }>("/api/conversations")).id; setCid(conv); }          // the conversation is created when the first question is sent (an empty one is never left behind)
      const r = await post<Reply>(`/api/conversations/${conv}/messages`, ctx ? { text: q, context: ctx } : { text: q });
      setMsgs((m) => [...m, { role: "assistant", content: r.answer, sources: r.sources, card: parseCard(r.card), notice: r.notice }]);
      if (r.highlight?.length && r.analysis_id) setHighlight({ analysisId: r.analysis_id, observationIds: r.highlight, nonce: nextNonce() });
    } catch (e) {
      const why = e instanceof Error ? e.message : "Something went wrong. Please try again.";
      if (ctx && e instanceof ApiError && e.status === 404) setFocus(null);                                  // the photo assessment is gone: stop attaching it
      setMsgs((m) => m.map((x, i) => (i === m.length - 1 && x.role === "patient" ? { ...x, failed: why } : x)));      // the question stays visible and is marked NOT sent (announced to screen readers); nothing pretends to have been answered
    }
    finally { setBusy(false); }
  }, [cid, focus]);
  const retry = (i: number) => { const m = msgs[i]; if (!m?.content) return; setMsgs((x) => x.filter((_, j) => j !== i)); void send(m.content, { ctx: m.ctx ?? null }); };
  const ask = useCallback((q: AskRequest) => { void send(q.text, { ctx: { analysis_id: q.analysisId, ...(q.observationId ? { observation_id: q.observationId } : {}), ...(q.intent ? { intent: q.intent } : {}) } }); }, [send]);

  useEffect(() => { if (qp && !sent.current) { sent.current = true; void send(qp); } }, [qp, send]);

  /** "Shows on the photo": selects the area the answer is about (or only brings the photo into view) and zooms to it. */
  function showOnPhoto(s: Source) {
    if (!s.analysis_id) return;
    setHighlight({ analysisId: s.analysis_id, observationIds: s.observation_id ? [s.observation_id] : [], nonce: nextNonce() });
    const el = document.querySelector(`[data-photo-assessment*="${CSS.escape(s.analysis_id)}"]`);
    el?.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  async function loadHistory() { setShowHist(true); setConvs(null); try { setConvs(await api<Conv[]>("/api/conversations")); } catch { setConvs([]); } }
  async function resume(c: Conv) {
    try { loaded.current = c.id; await openConversation(c.id); setWatching(null); setShowHist(false); router.replace(`/ask?c=${encodeURIComponent(c.id)}`); }
    catch (e) { loaded.current = ""; toast(e instanceof Error ? e.message : "Could not open the conversation.", "bad"); }
  }
  async function clear() {
    setConfirm(false);
    try { await del(`/api/conversations/${cid}`); setMsgs([]); setCid(""); setFocus(null); setHighlight(null); loaded.current = ""; if (cp) router.replace("/ask"); } catch (e) { toast(e instanceof Error ? e.message : "Could not delete.", "bad"); }
  }

  const lastAssistant = msgs.reduce((n, m, i) => (m.role === "assistant" && !m.photoIds ? i : n), -1);
  const lastIsPhoto = msgs.length > 0 && !!msgs[msgs.length - 1].photoIds;
  const lib = starters?.library;
  const askConcern = lastIsPhoto && !busy && lib?.available !== false;
  const empty = msgs.length === 0 && !opening && !pending && !loadError && !missing && !qp;
  const concerns = starters?.concerns ?? FALLBACK_CONCERNS;

  return (
    <div className="space-y-5">
      <ScreenBar back="/" right={<button className="btn btn-icon" onClick={loadHistory} aria-label="Conversation history"><History className="h-5 w-5" /></button>} />
      <div className="flex items-start justify-between gap-3"><div className="min-w-0 flex-1"><PageHeading eyebrow="Skin and skincare guide" title="Let's Understand Together" /></div>
        <button className="btn hidden md:inline-flex" onClick={loadHistory}><History className="h-5 w-5" aria-hidden /> History</button></div>

      <section className="space-y-4" aria-live="polite" aria-label="Conversation">
        {opening && msgs.length === 0 && !pending && <div className="flex items-center gap-2 text-muted" role="status"><Loader2 className="spin h-5 w-5" aria-hidden /> Opening your conversation…</div>}
        {loadError && <Note tone="bad"><p>{loadError}</p><button type="button" className="btn btn-sm mt-2" onClick={() => { setLoadError(""); loaded.current = ""; setReload((n) => n + 1); }}>Try again</button></Note>}
        {missing && !loadError && <Note tone="warn"><p>We couldn&apos;t find that photo upload. If it didn&apos;t finish, please take the photo again.</p><Link href="/photo" className="btn btn-sm mt-2">Take a photo</Link></Note>}

        {empty && !starters && <div className="flex items-center gap-2 text-muted" role="status"><Loader2 className="spin h-5 w-5" aria-hidden /> Getting ready…</div>}
        {empty && starters && (
          <div className="rise flex max-w-[96%] items-start gap-2 sm:max-w-[80%]" data-welcome>
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-sage text-teal"><Bot className="h-6 w-6" aria-hidden /></span>
            <div className="card min-w-0 flex-1 space-y-3 !rounded-tl-md !p-4">
              <h3 className="font-serif text-xl text-teal-dark">How can I help with your skin today?</h3>
              <p>{starters.intro}</p>
              {lib && !lib.available && <Note tone="gold">The clinic&apos;s reviewed skin library has not been published yet, so general skin questions are not available. Photos, reports and appointments still work.</Note>}
              <div className="flex flex-wrap gap-2" aria-label="Suggestions" data-starters>{starters.starters.map((a) => <Action key={a.id} a={a} busy={busy} onSend={(t) => void send(t)} />)}</div>
            </div>
          </div>)}

        {msgs.map((m, i) => m.photoIds ? (
          <div key={i} className="rise w-full max-w-3xl"><PhotoAnalysisCard ids={m.photoIds} onAsk={ask} onFocus={setFocus} highlight={highlight} /></div>
        ) : m.role === "patient" ? (
          <div key={i} className="rise ml-auto flex max-w-[88%] items-end gap-2 sm:max-w-[70%]">
            <div className="min-w-0"><div className={`rounded-[22px] rounded-br-md px-4 py-3 text-white ${m.failed ? "bg-[var(--bad-ink)]" : "bg-teal"}`}><p className="whitespace-pre-wrap">{m.content}</p></div>
              {m.failed && <p className="mt-1 text-right text-xs text-[var(--bad-ink)]" role="alert">Not sent. {m.failed} <button type="button" className="font-semibold underline" onClick={() => retry(i)} disabled={busy}>Try again</button></p>}</div>
            <span className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-gold-soft text-gold"><UserRound className="h-5 w-5" aria-hidden /></span>
          </div>
        ) : (
          <div key={i} className="rise flex max-w-[96%] items-start gap-2 sm:max-w-[80%]">
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-sage text-teal"><Bot className="h-6 w-6" aria-hidden /></span>
            <div className="card min-w-0 flex-1 space-y-3 !rounded-tl-md !p-4" data-answer={m.card?.route ?? "text"}>
              {m.card ? <AssistantCard card={m.card} busy={busy} latest={i === lastAssistant} onSend={(t) => void send(t)} /> : <p className="whitespace-pre-wrap">{m.content}</p>}
              {chipsOf(m).length > 0 && (
                <div className="flex flex-wrap gap-2">{chipsOf(m).map((s, j) => s.kind === "document" ? (
                  <Link key={j} href={reportHref(s.document_id)} className="badge note-info no-underline"><FileText className="h-3.5 w-3.5" aria-hidden /> Your report · page {s.page}</Link>)
                  : s.kind === "knowledge_base" ? <span key={j} className="badge note-gold">Clinic-approved: {s.title}</span>
                  : s.kind === "photo_analysis" ? <button key={j} type="button" className="badge note-info" data-source="photo_analysis" onClick={() => showOnPhoto(s)}><Crosshair className="h-3.5 w-3.5" aria-hidden /> {s.observation_id ? "Show on photo" : "Photo assessment"}: {s.label ?? "your photo"}</button>
                  : <Link key={j} href="/quote" className="badge note-info no-underline">Your approved quotation</Link>)}</div>)}
              {m.notice && <Note tone="warn">{m.notice}</Note>}
              <AudioPlayer compact text={m.card ? cardToSpeech(m.card) : m.content.slice(0, 1500)} />
            </div>
          </div>))}

        {askConcern && (
          <div className="rise flex max-w-[96%] items-start gap-2 sm:max-w-[80%]" data-concern-prompt>
            <span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-sage text-teal"><Bot className="h-6 w-6" aria-hidden /></span>
            <div className="card min-w-0 flex-1 space-y-2 !rounded-tl-md !p-4">
              <p className="font-semibold">{concerns.text}</p>
              <p className="text-sm text-muted">Tell me what brought you here and I will share general guidance for it. I can&apos;t diagnose from a photo. You can also type your own question below.</p>
              <div className="flex flex-wrap gap-2">{concerns.choices.map((c) => <button key={c.label} type="button" className="chip !whitespace-normal text-left" disabled={busy} data-concern={c.label}
                onClick={() => void send(c.reply, focus ? undefined : { ctx: lastPhotoCtx(msgs) })}>{c.label}</button>)}</div>
            </div>
          </div>)}

        {pending && <div className="w-full max-w-3xl"><PendingUpload pending={pending} onDismiss={() => analysisStore.dismiss(pending.id)} /></div>}
        {busy && <div className="flex items-center gap-2 text-muted" role="status"><Sparkles className="spin h-5 w-5" aria-hidden /> {busyPhoto ? "Looking at your photo assessment…" : "Finding the answer…"}</div>}
        <div ref={end} aria-hidden style={{ scrollMarginBottom: "calc(var(--nav-h) + env(safe-area-inset-bottom) + 230px)" }} />      {/* keeps the newest message clear of the sticky composer */}
      </section>

      <form onSubmit={(e) => { e.preventDefault(); void send(text); }} className="card sticky z-20 space-y-2 !p-3" style={{ bottom: "calc(var(--nav-h) + env(safe-area-inset-bottom) + 8px)" }}>
        {focus && (
          <div className="flex items-center gap-2 rounded-2xl bg-sage px-3 py-2 text-sm" data-context-chip={focus.observationId ?? "photo"}>
            <Crosshair className="h-4 w-4 shrink-0 text-teal" aria-hidden />
            <span className="min-w-0 flex-1 truncate">Asking about {focus.observationId ? <b>{focus.label ?? "the selected area"}</b> : "your photo"}</span>
            <button type="button" className="btn btn-icon btn-sm" onClick={() => setFocus(null)} aria-label="Stop asking about this photo"><X className="h-4 w-4" aria-hidden /></button>
          </div>)}
        <label htmlFor="q" className="sr-only">Your question</label>
        <textarea id="q" ref={input} className="input !rounded-2xl" rows={1} maxLength={1000} value={text}
            onChange={(e) => { setText(e.target.value); const el = e.currentTarget; el.style.height = "auto"; el.style.height = `${Math.min(el.scrollHeight, 132)}px`; }}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); if (!sending) void send(text); } }} placeholder={focus ? "Ask about this photo…" : "Ask in English, Hindi or Hinglish…"} />
        <div className="flex flex-wrap items-center justify-between gap-3">
          <VoiceInput onText={(t, note) => { setText(t); setVoiceNote(note ?? "Please check the words and correct them if needed before sending."); input.current?.focus(); }} onState={(s, m) => { if (s === "error") setVoiceNote(m ?? ""); else if (s !== "idle") setVoiceNote(""); }} />
          <button className="btn btn-primary shrink-0" type="submit" disabled={busy || sending || !text.trim()} aria-label="Send question"><Send className="h-5 w-5" aria-hidden /> Send</button>
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

/** The chips under an answer: the person's own report pages, the photo assessment, the clinic's approved article (when there is no card that already names its review) and a quotation. The library's own sources are listed in the card. */
function chipsOf(m: Msg): Source[] {
  return (m.sources ?? []).filter((s) => s.kind === "document" || s.kind === "photo_analysis" || s.kind === "quotation" || (s.kind === "knowledge_base" && !m.card));
}

/** The photo the last assessment message in the conversation is about: a concern chosen after a photo is a statement about that photo's conversation, so it carries the same photo. */
function lastPhotoCtx(msgs: Msg[]): PhotoCtx | null {
  for (let i = msgs.length - 1; i >= 0; i--) { const ids = msgs[i].photoIds; if (ids?.length) return { analysis_id: ids[0] }; }
  return null;
}

export default function Ask() { return <Suspense fallback={null}><Chat /></Suspense>; }
