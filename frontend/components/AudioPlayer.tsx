"use client";

import { Loader2, Pause, Play, RotateCcw, Volume2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { blob } from "@/lib/api";

const LANG_NAME: Record<string, string> = { "en-IN": "English (India)", "hi-IN": "Hindi", "en-US": "English" };
const SPEEDS = [1, 1.25, 1.5, 0.75];
const BARS = Array.from({ length: 44 }, (_, i) => 6 + Math.round(Math.abs(Math.sin(i * 1.7) * 12 + Math.sin(i * 0.6) * 10)));
const mmss = (s: number) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

type Mode = "idle" | "loading" | "playing" | "paused" | "unavailable";

/** Play / pause / resume / replay / speed. Server-side offline TTS first; otherwise an on-device browser voice, but only if it is
 *  a local voice (never a network voice). If neither exists the explanation stays available as text. */
export default function AudioPlayer({ text, language = "en-IN", compact = false }: { text: string; language?: string; compact?: boolean }) {
  const [mode, setMode] = useState<Mode>("idle");
  const [speedIdx, setSpeedIdx] = useState(0);
  const [source, setSource] = useState<"server" | "browser" | "">("");
  const [pos, setPos] = useState({ t: 0, d: 0 });
  const audio = useRef<HTMLAudioElement | null>(null);
  const url = useRef("");
  const rate = SPEEDS[speedIdx];

  useEffect(() => { if (audio.current) audio.current.playbackRate = rate; }, [rate]);

  function stopAll() {
    audio.current?.pause();
    if (url.current) URL.revokeObjectURL(url.current);
    url.current = ""; audio.current = null;
    window.speechSynthesis?.cancel();
  }
  // The parent re-mounts this component (key) when text or language changes, so a stale clip never plays.
  useEffect(() => () => stopAll(), []);

  function browserSpeak(): boolean {
    const synth = window.speechSynthesis;
    if (!synth) return false;
    const want = language.split("-")[0];
    const voice = synth.getVoices().find((v) => v.localService && v.lang.toLowerCase().startsWith(want));
    if (!voice) return false;
    const u = new SpeechSynthesisUtterance(text);
    u.voice = voice; u.lang = voice.lang; u.rate = rate;
    u.onend = () => setMode("idle");
    synth.cancel(); synth.speak(u);
    setSource("browser"); setMode("playing");
    return true;
  }

  async function play() {
    stopAll(); setMode("loading"); setPos({ t: 0, d: 0 });
    try {
      const b = await blob("/api/speech/tts", { method: "POST", body: JSON.stringify({ text, language, rate: 1 }) });
      url.current = URL.createObjectURL(b);
      const a = new Audio(url.current);
      a.playbackRate = rate;
      a.onended = () => setMode("idle");
      a.ontimeupdate = () => setPos({ t: a.currentTime, d: a.duration || 0 });
      a.onloadedmetadata = () => setPos({ t: 0, d: a.duration || 0 });
      audio.current = a; setSource("server");
      await a.play(); setMode("playing");
    } catch { if (!browserSpeak()) setMode("unavailable"); }
  }
  const pause = () => { if (source === "browser") window.speechSynthesis.pause(); else audio.current?.pause(); setMode("paused"); };
  const resume = () => { if (source === "browser") window.speechSynthesis.resume(); else void audio.current?.play(); setMode("playing"); };
  const frac = pos.d ? pos.t / pos.d : 0;
  const busy = mode === "loading";

  return (
    <div className={`rounded-2xl bg-white ${compact ? "p-2" : "p-3 border border-line"}`} role="group" aria-label="Listen to the explanation">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <button className="btn btn-primary btn-icon shrink-0" onClick={mode === "playing" ? pause : mode === "paused" ? resume : play} disabled={busy}
          aria-label={mode === "playing" ? "Pause" : mode === "paused" ? "Resume" : "Listen"}>
          {busy ? <Loader2 className="spin h-5 w-5" /> : mode === "playing" ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5" />}
        </button>
        <div className="waveform min-w-[96px] flex-1 overflow-hidden" aria-hidden>{BARS.map((h, i) => <i key={i} className={i / BARS.length < frac ? "on" : ""} style={{ height: h }} />)}</div>
        <span className="ml-auto w-[5.5rem] text-right text-sm tabular-nums text-muted" role="timer" aria-label="Elapsed time">{pos.d ? `${mmss(pos.t)} / ${mmss(pos.d)}` : source === "browser" && mode !== "idle" ? "Speaking…" : "—"}</span>
        <button className="btn btn-sm !rounded-xl bg-sage" onClick={() => setSpeedIdx((i) => (i + 1) % SPEEDS.length)} aria-label={`Playback speed ${rate} times. Change speed`}>{rate}x</button>
        <button className="btn btn-icon btn-sm" onClick={play} disabled={busy} aria-label="Replay"><RotateCcw className="h-4 w-4" /></button>
      </div>
      {!compact && <p className="mt-2 flex items-center gap-1.5 text-xs text-muted"><Volume2 className="h-3.5 w-3.5" aria-hidden /> Language: {LANG_NAME[language] ?? language}{source === "browser" ? " · on-device voice" : ""}</p>}
      {mode === "unavailable" && <p className="mt-2 text-sm text-[var(--warn-ink)]" role="status">Voice playback isn&apos;t available right now, so the explanation is text only. The clinic team can set up an offline voice.</p>}
    </div>
  );
}
