"use client";

import { Camera, Check, Flower2, Focus, Glasses, Grid2x2Plus, Image as ImageIcon, Loader2, ScanFace, Sun, User } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useRef, useState } from "react";
import CameraPanel from "@/components/CameraPanel";
import { ScreenBar } from "@/components/Shell";
import { Note, PageHeading, ProgressSteps } from "@/components/ui";
import { chooseNativePhotos, MAX_PHOTOS, NATIVE_PHOTOS, Picked, screenPhotos, takeNativePhoto } from "@/lib/nativePhoto";
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
  const flow = usePhotoFlow();
  const [cam, setCam] = useState(false);                         // web only: the inline browser camera
  const [busy, setBusy] = useState<"" | "camera" | "gallery">("");
  const [problem, setProblem] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  const qp = params.get("region") as Region | null;
  useEffect(() => { if (qp && REGIONS.some((r) => r.id === qp)) flow.setRegion(qp); }, [qp]); // eslint-disable-line react-hooks/exhaustive-deps
  const cur = REGIONS.find((r) => r.id === flow.region)!;
  const room = MAX_PHOTOS - flow.shots.length;

  /** Validate on the phone first (type, size, readable), then hand the good photos to the adjust screen. A problem is shown here, in place, with what to do next. */
  async function accept(files: File[]) {
    setProblem("");
    if (!files.length) return;
    if (room <= 0) { setProblem(`You can add up to ${MAX_PHOTOS} photos at a time. Continue with the photos you have, or remove one first.`); return; }
    const { good, problem: p } = await screenPhotos(files.slice(0, room));
    if (!good.length) { setProblem(p || "We couldn't use that photo. Please try another."); return; }
    good.forEach((f) => flow.add(f));
    router.push("/photo/adjust");
  }
  async function native(kind: "camera" | "gallery") {
    setProblem(""); setBusy(kind);
    const r: Picked = kind === "camera" ? await takeNativePhoto() : await chooseNativePhotos(Math.max(1, room));
    setBusy("");
    if (r.kind === "error") setProblem(r.message);
    else if (r.kind === "photos") await accept(r.files);                     // "cancelled" (the person backed out) is not an error: nothing to say
  }
  const takePhoto = () => { setProblem(""); if (NATIVE_PHOTOS) void native("camera"); else setCam(true); };
  const fromGallery = () => { setProblem(""); if (NATIVE_PHOTOS) void native("gallery"); else fileRef.current?.click(); };

  return (
    <div className="space-y-5">
      <ScreenBar back="/" right={<ProgressSteps step={1} total={2} />} />
      <div className="hidden justify-end md:flex"><ProgressSteps step={1} total={2} /></div>
      <PageHeading eyebrow="Photo capture" title="Take a Clear Photo" />

      <div className="scroll-x" role="group" aria-label="Which area is the photo of?">
        {REGIONS.map(({ id, label, Icon }) => (
          <button key={id} className="chip !flex-col !gap-1 !rounded-2xl !px-6 !py-4" aria-pressed={flow.region === id} onClick={() => flow.setRegion(id)}>
            <Icon className="h-7 w-7" strokeWidth={1.5} aria-hidden />{label}{flow.region === id && <Check className="absolute right-2 top-2 h-4 w-4" aria-hidden />}
          </button>))}
      </div>
      <p className="-mt-1 text-sm text-muted">{cur.hint}.</p>

      {!NATIVE_PHOTOS && cam && <section aria-label="Camera"><CameraPanel key={flow.region} hint={cur.hint} active={cam} onActiveChange={setCam} onCapture={(f) => { void accept([f]); }} /></section>}

      <div className="grid gap-3">
        <button className="btn btn-primary !py-4 text-lg" onClick={takePhoto} disabled={busy !== "" || cam}>
          {busy === "camera" ? <Loader2 className="spin h-5 w-5" aria-hidden /> : <Camera className="h-5 w-5" aria-hidden />} {busy === "camera" ? "Opening camera…" : "Take a photo"}
        </button>
        <button className="btn !py-4 text-lg" onClick={fromGallery} disabled={busy !== ""}>
          {busy === "gallery" ? <Loader2 className="spin h-5 w-5" aria-hidden /> : <ImageIcon className="h-5 w-5" aria-hidden />} {busy === "gallery" ? "Opening gallery…" : "Upload from gallery"}
        </button>
        <input ref={fileRef} type="file" accept="image/*" multiple className="sr-only" aria-label="Upload a photo" onChange={(e) => { const f = Array.from(e.target.files ?? []); e.target.value = ""; void accept(f); }} />
      </div>
      {problem && <Note tone="bad">{problem}</Note>}
      {flow.shots.length > 0 && <p className="text-center text-sm"><Link href="/photo/adjust" className="font-semibold text-teal-dark underline">Continue with your {flow.shots.length} photo{flow.shots.length === 1 ? "" : "s"}</Link></p>}

      <section className="card space-y-3" aria-label="Photo tips">
        <h2 className="!text-lg">For a useful photo</h2>
        <ul className="space-y-3">{cur.tips.map(({ Icon, title, sub }) => (
          <li key={title} className="flex items-start gap-3"><span className="grid h-10 w-10 shrink-0 place-items-center rounded-full bg-gold-soft text-gold"><Icon className="h-5 w-5" aria-hidden /></span>
            <span><span className="block font-semibold">{title}</span><span className="block text-sm text-muted">{sub}</span></span></li>))}</ul>
      </section>
      <p className="text-center text-sm text-muted">Your photo is private, kept on the clinic&apos;s system and reviewed by your clinician. This is not a diagnosis.</p>
    </div>
  );
}

export default function PhotoPage() {
  return <Suspense fallback={null}><Capture /></Suspense>;
}
