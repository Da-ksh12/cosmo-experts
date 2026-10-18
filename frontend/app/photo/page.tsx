"use client";

import { Check, Flower2, Grid2x2Plus, Image as ImageIcon, ScanFace, Sun, User, X, Focus, Glasses } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import CameraPanel from "@/components/CameraPanel";
import { ScreenBar } from "@/components/Shell";
import { Note, PageHeading, ProgressSteps, useToast } from "@/components/ui";
import { Region, usePhotoFlow } from "@/lib/photoFlow";

const REGIONS: { id: Region; label: string; Icon: typeof ScanFace; hint: string; tips: { Icon: typeof Sun; title: string; sub: string }[] }[] = [
  { id: "face", label: "Face / Skin", Icon: ScanFace, hint: "Position your face within the frame",
    tips: [{ Icon: Sun, title: "Use good natural light", sub: "Avoid dark or harsh lighting." }, { Icon: Focus, title: "Keep your face in the centre", sub: "Make sure your full face is visible." },
      { Icon: Grid2x2Plus, title: "Remove filters", sub: "Use a clear, unedited photo." }, { Icon: Glasses, title: "Face front or a slight angle", sub: "Look at the camera naturally; remove glasses if you can." }] },
  { id: "hair_scalp", label: "Hair / Scalp", Icon: Flower2, hint: "Part the hair so the scalp is visible",
    tips: [{ Icon: Sun, title: "Bright, even light", sub: "No flash and no harsh shadows." }, { Icon: Focus, title: "Part the hair", sub: "Show the scalp and hairline clearly." },
      { Icon: Grid2x2Plus, title: "Stay 15–20 cm away", sub: "Keep the camera steady and in focus." }, { Icon: Glasses, title: "No hats or accessories", sub: "Nothing covering the area." }] },
  { id: "body", label: "Body Area", Icon: User, hint: "Show the whole area with a little surrounding skin",
    tips: [{ Icon: Sun, title: "Good natural light", sub: "Even light, no flash." }, { Icon: Focus, title: "Include the whole area", sub: "Show some surrounding skin for context." },
      { Icon: Grid2x2Plus, title: "No filters or edits", sub: "A plain photo is most useful." }, { Icon: Glasses, title: "Hold steady", sub: "Rest your elbows or use a stable surface." }] },
  { id: "other", label: "Other", Icon: Grid2x2Plus, hint: "Show the area clearly in even light",
    tips: [{ Icon: Sun, title: "Good natural light", sub: "Even light, no flash." }, { Icon: Focus, title: "Centre the area", sub: "Keep it sharp and in the middle." },
      { Icon: Grid2x2Plus, title: "No filters or edits", sub: "A plain photo is most useful." }, { Icon: Glasses, title: "Hold steady", sub: "Avoid motion blur." }] },
];

function Capture() {
  const router = useRouter();
  const params = useSearchParams();
  const toast = useToast();
  const flow = usePhotoFlow();
  const [cam, setCam] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const qp = params.get("region") as Region | null;
  useEffect(() => { if (qp && REGIONS.some((r) => r.id === qp)) flow.setRegion(qp); }, [qp]); // eslint-disable-line react-hooks/exhaustive-deps
  const cur = REGIONS.find((r) => r.id === flow.region)!;

  function take(f: File) {
    if (!/^image\/(jpeg|png)$/.test(f.type)) { toast("Please choose a JPG or PNG photo.", "bad"); return; }
    if (f.size > 15 * 1024 * 1024) { toast("That photo is larger than 15 MB. Please choose a smaller one.", "bad"); return; }
    flow.add(f);
    router.push("/photo/adjust");
  }

  return (
    <div className="space-y-6">
      <ScreenBar back="/" right={<ProgressSteps step={1} total={3} />} />
      <div className="hidden justify-end md:flex"><ProgressSteps step={1} total={3} /></div>
      <PageHeading eyebrow="Photo capture" title="Take a Clear Photo" subtitle="Follow the simple guide below so the clinic can use your photo." />

      <div className="scroll-x" role="group" aria-label="Which area is the photo of?">
        {REGIONS.map(({ id, label, Icon }) => (
          <button key={id} className="chip !flex-col !gap-1 !rounded-2xl !px-6 !py-4" aria-pressed={flow.region === id} onClick={() => flow.setRegion(id)}>
            <Icon className="h-7 w-7" strokeWidth={1.5} aria-hidden />{label}{flow.region === id && <Check className="absolute right-2 top-2 h-4 w-4" aria-hidden />}
          </button>))}
      </div>

      <div className="grid grid-cols-1 gap-5 md:grid-cols-2">
        <section className="card space-y-4" aria-label="Photo tips">
          <div><h2>Photo Tips</h2><p className="text-sm text-muted">For the most useful photo</p></div>
          <ul className="space-y-3">{cur.tips.map(({ Icon, title, sub }) => (
            <li key={title} className="flex items-start gap-3"><span className="grid h-11 w-11 shrink-0 place-items-center rounded-full bg-gold-soft text-gold"><Icon className="h-5 w-5" aria-hidden /></span>
              <span><span className="block font-semibold">{title}</span><span className="block text-sm text-muted">{sub}</span></span></li>))}</ul>
          <div className="grid grid-cols-2 gap-3 text-sm">
            <div className="rounded-2xl bg-sage p-3"><p className="flex items-center gap-1 font-semibold text-[var(--good-ink)]"><Check className="h-4 w-4" aria-hidden /> Good</p><p className="text-muted">Clear, well lit, area fully visible</p></div>
            <div className="rounded-2xl bg-[var(--bad-bg)] p-3"><p className="flex items-center gap-1 font-semibold text-[var(--bad-ink)]"><X className="h-4 w-4" aria-hidden /> Not good</p><p className="text-[var(--bad-ink)]">Dark, blurry, filtered or cropped</p></div>
          </div>
        </section>
        <section aria-label="Camera" className="order-first md:order-none"><CameraPanel key={flow.region} hint={cur.hint} active={cam} onActiveChange={setCam} onCapture={take} /></section>
      </div>

      <div className="card flex flex-wrap items-center justify-between gap-3">
        <button className="btn !rounded-2xl" onClick={() => fileRef.current?.click()}><ImageIcon className="h-5 w-5" aria-hidden /> Upload Photo <span className="hidden text-sm font-normal text-muted sm:inline">from your gallery</span></button>
        <input ref={fileRef} type="file" accept="image/jpeg,image/png" className="sr-only" aria-label="Upload a photo" onChange={(e) => { const f = e.target.files?.[0]; if (f) take(f); e.target.value = ""; }} />
        <button className="btn btn-primary" onClick={() => setCam(true)} disabled={cam}>Take Photo</button>
      </div>
      <Note tone="info">Your photo is only checked for quality here. It is private, kept on the clinic&apos;s system and reviewed by your clinician. This is not a diagnosis.</Note>
    </div>
  );
}

export default function PhotoPage() {
  return <Suspense fallback={null}><Capture /></Suspense>;
}
