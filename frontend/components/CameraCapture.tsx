"use client";

import { useEffect, useRef, useState } from "react";

/** Explicit-permission camera. The stream is stopped as soon as a photo is taken or the dialog closes. */
export default function CameraCapture({ onCapture, onClose }: { onCapture: (f: File) => void; onClose: () => void }) {
  const video = useRef<HTMLVideoElement>(null);
  const stream = useRef<MediaStream | null>(null);
  const [error, setError] = useState("");
  const [shot, setShot] = useState<{ url: string; blob: Blob } | null>(null);

  const stop = () => { stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null; };

  async function start() {
    try {
      const s = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "user", width: { ideal: 1280 } }, audio: false });
      stream.current = s;
      if (video.current) { video.current.srcObject = s; await video.current.play(); }
    } catch {
      setError("We couldn't open the camera. Please allow camera access in your browser, or upload a photo instead.");
    }
  }

  // Opening the camera synchronises with an external device; setError only runs after the async permission result.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void start(); return stop; }, []);

  function snap() {
    const v = video.current;
    if (!v || !v.videoWidth) return;
    const c = document.createElement("canvas");
    c.width = v.videoWidth; c.height = v.videoHeight;
    c.getContext("2d")!.drawImage(v, 0, 0);
    c.toBlob((b) => { if (b) { setShot({ url: URL.createObjectURL(b), blob: b }); stop(); } }, "image/jpeg", 0.92);
  }

  function retake() { if (shot) URL.revokeObjectURL(shot.url); setShot(null); setError(""); void start(); }
  function use() { if (shot) { onCapture(new File([shot.blob], `camera-${Date.now()}.jpg`, { type: "image/jpeg" })); URL.revokeObjectURL(shot.url); onClose(); } }
  function close() { stop(); onClose(); }

  return (
    <div role="dialog" aria-modal="true" aria-label="Take a photo" className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="card w-full max-w-xl space-y-4">
        <h2>Take a photo</h2>
        {error ? <p className="note note-bad" role="alert">{error}</p> : shot ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={shot.url} alt="Your captured photo" className="w-full rounded-xl" />
        ) : (
          <video ref={video} playsInline muted className="w-full rounded-xl bg-black" aria-label="Live camera preview" />
        )}
        <div className="flex flex-wrap gap-3">
          {shot ? (<><button className="btn btn-primary" onClick={use}>Use this photo</button><button className="btn" onClick={retake}>Retake</button></>)
            : !error && <button className="btn btn-primary" onClick={snap}>📷 Take photo</button>}
          <button className="btn" onClick={close}>Cancel</button>
        </div>
      </div>
    </div>
  );
}
