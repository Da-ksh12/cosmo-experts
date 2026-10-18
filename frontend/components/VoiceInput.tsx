"use client";

import { Loader2, Mic, Square } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import { runUploadJob } from "@/lib/jobs";

/** Push-to-record voice question. No background listening: the microphone is open only between Start and Stop and the
 *  track is released immediately. Audio goes to the LOCAL server for transcription and is discarded; the patient reviews
 *  and can correct the transcript before anything is sent. `trigger` increments to start recording from outside. */
export default function VoiceInput({ onText, onState, trigger = 0 }: { onText: (t: string) => void; onState?: (s: "idle" | "listening" | "transcribing" | "error", msg?: string) => void; trigger?: number }) {
  const [available, setAvailable] = useState<boolean | null>(null);
  const [state, setState] = useState<"idle" | "listening" | "transcribing" | "error">("idle");
  const [secs, setSecs] = useState(0);
  
  const mr = useRef<MediaRecorder | null>(null);
  const chunks = useRef<Blob[]>([]);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);

  const set = useCallback((s: typeof state, m = "") => { setState(s); onState?.(s, m); }, [onState]);
  useEffect(() => { api<{ stt: { available: boolean } }>("/api/speech/status").then((s) => setAvailable(s.stt.available)).catch(() => setAvailable(false)); }, []);

  const start = useCallback(async () => {
    if (!available) { set("error", "Voice questions aren't set up on this server, so please type your question. The clinic team can enable local speech recognition."); return; }
    try {
      const s = await navigator.mediaDevices.getUserMedia({ audio: true });
      const r = new MediaRecorder(s);
      chunks.current = [];
      r.ondataavailable = (e) => chunks.current.push(e.data);
      r.onstop = async () => {
        s.getTracks().forEach((t) => t.stop());
        if (timer.current) clearInterval(timer.current);
        set("transcribing");
        try {
          const fd = new FormData();
          fd.append("file", new Blob(chunks.current, { type: r.mimeType }), "question.webm");
          const job = await runUploadJob<{ text: string }>("/api/v1/speech/transcribe", fd);
          if (job.status !== "completed" || !job.result) throw new Error(job.error ?? "Voice questions aren't available. Please type your question.");
          onText(job.result.text);
          set("idle", "Please check the words and correct them if needed before sending.");
        } catch (e) { set("error", e instanceof Error ? e.message : "Voice questions aren't available. Please type your question."); }
        finally { chunks.current = []; }
      };
      mr.current = r; r.start(); setSecs(0);
      timer.current = setInterval(() => setSecs((x) => x + 1), 1000);
      set("listening");
    } catch { set("error", "Microphone access wasn't allowed. You can type your question instead."); }
  }, [available, onText, set]);

  const stop = () => mr.current?.stop();
  const seen = useRef(0);
  useEffect(() => { if (trigger > seen.current) { seen.current = trigger; void start(); } }, [trigger, start]);
  useEffect(() => () => { mr.current?.stream.getTracks().forEach((t) => t.stop()); if (timer.current) clearInterval(timer.current); }, []);

  return (
    <div className="flex items-center gap-3">
      <button className={`grid h-12 w-12 shrink-0 place-items-center rounded-full text-white shadow-lg transition ${state === "listening" ? "bg-[var(--bad-ink)] ring-4 ring-[var(--bad-bg)]" : "bg-teal"} disabled:opacity-60`}
        onClick={state === "listening" ? stop : start} disabled={state === "transcribing"} aria-pressed={state === "listening"}
        aria-label={state === "listening" ? "Stop recording" : "Ask by voice"}>
        {state === "transcribing" ? <Loader2 className="spin h-6 w-6" /> : state === "listening" ? <Square className="h-5 w-5" fill="currentColor" /> : <Mic className="h-6 w-6" />}
      </button>
      <span className="text-sm font-semibold text-muted" role="status">{state === "listening" ? `Listening… ${secs}s · tap to stop` : state === "transcribing" ? "Converting speech to text…" : "Tap to speak"}</span>
    </div>
  );
}
