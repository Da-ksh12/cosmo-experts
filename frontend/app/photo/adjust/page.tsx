"use client";

import { ArrowRight, Plus, RefreshCw } from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ScreenBar } from "@/components/Shell";
import { Note, PageHeading, ProgressSteps, useToast } from "@/components/ui";
import { defaultEdit, Edit, exportEdit, loadBitmap } from "@/components/ImageEditor";
import { usePhotoFlow } from "@/lib/photoFlow";

const ImageEditor = dynamic(() => import("@/components/ImageEditor"), { ssr: false });

export default function Adjust() {
  const router = useRouter();
  const toast = useToast();
  const flow = usePhotoFlow();
  const fileRef = useRef<HTMLInputElement>(null);
  const [sel, setSel] = useState(0);
  const [bitmaps, setBitmaps] = useState<Record<string, ImageBitmap>>({});
  const [edits, setEdits] = useState<Record<string, Edit>>({});
  const [thumbs, setThumbs] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [loadError, setLoadError] = useState("");

  useEffect(() => { if (flow.shots.length === 0) router.replace("/photo"); }, [flow.shots.length, router]);

  // decode every new source image once
  useEffect(() => {
    flow.shots.forEach((s) => {
      if (bitmaps[s.key]) return;
      loadBitmap(s.source).then((b) => {
        setBitmaps((m) => ({ ...m, [s.key]: b }));
        setEdits((m) => ({ ...m, [s.key]: m[s.key] ?? defaultEdit() }));
        setThumbs((t) => ({ ...t, [s.key]: URL.createObjectURL(s.source) }));
      }).catch((e) => setLoadError(e.message));
    });
  }, [flow.shots]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => Object.values(thumbs).forEach(URL.revokeObjectURL), []); // eslint-disable-line react-hooks/exhaustive-deps

  const idx = Math.min(sel, flow.shots.length - 1);
  const shot = flow.shots[idx];
  const bmp = shot && bitmaps[shot.key];
  const edit = useMemo(() => (shot ? edits[shot.key] ?? defaultEdit() : defaultEdit()), [shot, edits]);

  async function next() {
    setBusy(true);
    try {
      for (const s of flow.shots) {
        const b = bitmaps[s.key] ?? (await loadBitmap(s.source));
        flow.setEdited(s.key, await exportEdit(b, edits[s.key] ?? defaultEdit()));
      }
      router.push("/photo/check");
    } catch (e) { toast(e instanceof Error ? e.message : "Could not prepare the photos.", "bad"); setBusy(false); }
  }

  function retake() {
    if (!shot) return;
    flow.remove(shot.key);
    if (flow.shots.length <= 1) router.push("/photo");
    setSel(0);
  }

  function addMore(f: File) {
    if (!/^image\/(jpeg|png)$/.test(f.type)) { toast("Please choose a JPG or PNG photo.", "bad"); return; }
    if (flow.shots.length >= 4) { toast("You can add up to 4 photos at a time.", "info"); return; }
    flow.add(f); setSel(flow.shots.length);
  }

  if (!shot) return null;
  return (
    <div className="space-y-6">
      <ScreenBar back="/photo" right={<ProgressSteps step={2} total={3} />} />
      <div className="hidden justify-end md:flex"><ProgressSteps step={2} total={3} /></div>
      <PageHeading eyebrow="Adjust your photo" title="Crop & Adjust" subtitle="Move and zoom so the area is clearly visible and well framed." />
      {loadError && <Note tone="bad">{loadError}</Note>}
      <div className="mx-auto max-w-2xl space-y-5">
        {bmp ? <ImageEditor key={shot.key} bitmap={bmp} edit={edit} onChange={(e) => setEdits((m) => ({ ...m, [shot.key]: e }))} />
          : <div className="h-[460px] animate-pulse rounded-[26px] bg-line" aria-label="Loading photo" />}
        <div className="scroll-x" role="listbox" aria-label="Your photos">
          {flow.shots.map((s, i) => (
            <button key={s.key} role="option" aria-selected={i === idx} className={`h-20 w-20 overflow-hidden rounded-2xl border-2 ${i === idx ? "border-teal" : "border-transparent"}`} onClick={() => setSel(i)} aria-label={`Photo ${i + 1}`}>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              {thumbs[s.key] && <img src={thumbs[s.key]} alt="" className="h-full w-full object-cover" />}</button>))}
          <button className="grid h-20 w-24 place-items-center rounded-2xl border-2 border-dashed border-line bg-white/60 px-2 text-center text-xs font-semibold text-muted" onClick={() => fileRef.current?.click()}><Plus className="h-5 w-5" aria-hidden />Add another photo</button>
          <input ref={fileRef} type="file" accept="image/jpeg,image/png" className="sr-only" aria-label="Add another photo" onChange={(e) => { const f = e.target.files?.[0]; if (f) addMore(f); e.target.value = ""; }} />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <button className="btn !py-3" onClick={retake}><RefreshCw className="h-5 w-5" aria-hidden /> Retake Photo</button>
          <button className="btn btn-primary !py-3" onClick={next} disabled={busy || !bmp}>{busy ? "Preparing…" : <>Continue <ArrowRight className="h-5 w-5" aria-hidden /></>}</button>
        </div>
        <p className="text-center text-sm text-muted">Your edits are only applied to this photo on your device. Nothing is sent until you tap Continue.</p>
      </div>
    </div>
  );
}
