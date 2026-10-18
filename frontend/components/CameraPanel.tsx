"use client";

import { Camera, CameraOff, SwitchCamera } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

type Facing = "user" | "environment";

/** Inline camera. Opened only on an explicit tap; the stream is stopped on capture, on close and on unmount. */
export default function CameraPanel({ initialFacing = "user", hint, onCapture, active, onActiveChange }:
  { initialFacing?: Facing; hint: string; onCapture: (f: File) => void; active: boolean; onActiveChange: (a: boolean) => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [facing, setFacing] = useState<Facing>(initialFacing);
  const [error, setError] = useState<"" | "denied" | "unsupported" | "failed">("");
  const [starting, setStarting] = useState(false);
  const [multi, setMulti] = useState(false);

  const stop = useCallback(() => { stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null; }, []);

  const start = useCallback(async (f: Facing) => {
    if (!navigator.mediaDevices?.getUserMedia) { setError("unsupported"); onActiveChange(false); return; }
    setStarting(true); setError("");
    stop();
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: f, width: { ideal: 1600 } }, audio: false });
      stream.current = s;
      if (video.current) { video.current.srcObject = s; await video.current.play().catch(() => {}); }
      setMulti((await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === "videoinput").length > 1);
    } catch (e) {
      setError((e as DOMException)?.name === "NotAllowedError" ? "denied" : "failed");
      onActiveChange(false);
    } finally { setStarting(false); }
  }, [onActiveChange, stop]);

  // Starting the camera synchronises React with an external device, so state updates here are intentional.
  useEffect(() => {
    if (active) void start(facing); else stop(); // eslint-disable-line react-hooks/set-state-in-effect
    return stop;
  }, [active]); // eslint-disable-line react-hooks/exhaustive-deps

  function snap() {
    const v = video.current;
    if (!v || !v.videoWidth) return;
    const c = document.createElement("canvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    const ctx = c.getContext("2d")!;
    if (facing === "user") { ctx.translate(c.width, 0); ctx.scale(-1, 1); }   // un-mirror so the saved photo matches what others see
    ctx.drawImage(v, 0, 0);
    c.toBlob((b) => { if (b) { stop(); onActiveChange(false); onCapture(new File([b], `camera-${Date.now()}.jpg`, { type: "image/jpeg" })); } }, "image/jpeg", 0.92);
  }

  const flip = () => { const n = facing === "user" ? "environment" : "user"; setFacing(n); void start(n); };

  const msg = { denied: "Camera access was blocked. Allow it in your browser settings, or upload a photo instead.", unsupported: "This browser can't open the camera. Please upload a photo instead.", failed: "We couldn't start the camera. Please upload a photo instead." }[error as "denied"];

  return (
    <div className="relative aspect-[4/5] max-h-[68dvh] w-full overflow-hidden rounded-[26px] bg-gradient-to-br from-[#d9e4e0] to-[#efe6d6]">
      {active ? (
        <>
          <video ref={video} playsInline muted aria-label="Live camera preview" className={`h-full w-full object-cover ${facing === "user" ? "-scale-x-100" : ""}`} />
          <div className="pointer-events-none absolute inset-[12%] rounded-[28px] border-2 border-white/80 [mask-image:linear-gradient(#000,#000)]" aria-hidden />
          <p className="absolute bottom-4 left-1/2 w-[88%] -translate-x-1/2 rounded-full bg-white/90 px-4 py-2 text-center text-sm font-medium" role="status">{starting ? "Starting camera…" : hint}</p>
          <button className="btn btn-icon absolute right-3 top-3 bg-black/50 !text-white" onClick={() => { stop(); onActiveChange(false); }} aria-label="Close camera"><CameraOff className="h-5 w-5" /></button>
          {multi && <button className="btn btn-icon absolute left-3 top-3 bg-black/50 !text-white" onClick={flip} aria-label="Switch camera"><SwitchCamera className="h-5 w-5" /></button>}
          <button className="absolute bottom-16 left-1/2 grid h-[72px] w-[72px] -translate-x-1/2 place-items-center rounded-full border-4 border-white bg-teal text-white shadow-xl" onClick={snap} aria-label="Take photo" disabled={starting}><Camera className="h-8 w-8" /></button>
        </>
      ) : (
        <div className="grid h-full place-items-center p-6 text-center">
          <div className="space-y-3">
            <span className="mx-auto grid h-16 w-16 place-items-center rounded-full bg-white/80 text-teal"><Camera className="h-8 w-8" strokeWidth={1.4} /></span>
            {error ? <p className="note note-warn" role="alert">{msg}</p> : <p className="text-muted">Open the camera to take a photo, or upload one from your gallery.</p>}
            <button className="btn btn-primary" onClick={() => onActiveChange(true)}><Camera className="h-5 w-5" aria-hidden /> {error ? "Try the camera again" : "Open camera"}</button>
          </div>
        </div>
      )}
    </div>
  );
}
