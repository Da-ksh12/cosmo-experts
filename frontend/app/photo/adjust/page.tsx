"use client";

import { ArrowRight, Camera, Check, Crop, Image as ImageIcon, Loader2, Plus, RefreshCw, Trash2, X } from "lucide-react";
import dynamic from "next/dynamic";
import { useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { ScreenBar } from "@/components/Shell";
import { Dialog, LocalImage, Note, PageHeading, ProgressSteps } from "@/components/ui";
import { defaultEdit, Edit, exportEdit, loadBitmap } from "@/components/ImageEditor";
import { analysisStore } from "@/lib/analysisStore";
import { chooseNativePhotos, MAX_PHOTOS, NATIVE_PHOTOS, Picked, screenPhotos, takeNativePhoto } from "@/lib/nativePhoto";
import { usePhotoFlow } from "@/lib/photoFlow";
import { prepare } from "@/lib/photoPrep";
import { quickCheck, QuickResult } from "@/lib/quickCheck";

const ImageEditor = dynamic(() => import("@/components/ImageEditor"), { ssr: false });

/** The preview of the photo(s) the person chose, exactly as they will be sent. From here: Retake (the camera again), Replace (another photo from the gallery), Remove, an optional crop or
 *  rotation, and Continue, which sends the photo and opens the conversation where the assessment appears. Nothing is sent before Continue. */
export default function Adjust() {
  const router = useRouter();
  const flow = usePhotoFlow();
  const fileRef = useRef<HTMLInputElement>(null);
  const replaceRef = useRef<HTMLInputElement>(null);
  const leaving = useRef(false);
  const [sel, setSel] = useState(0);
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [quick, setQuick] = useState<Record<string, QuickResult>>({});
  const [busy, setBusy] = useState(false);
  const [picking, setPicking] = useState<"" | "camera" | "gallery">("");
  const [adding, setAdding] = useState(false);
  const [problem, setProblem] = useState("");
  const [editing, setEditing] = useState<{ key: string; bitmap: ImageBitmap; edit: Edit } | null>(null);

  useEffect(() => { if (flow.shots.length === 0 && !leaving.current) router.replace("/photo"); }, [flow.shots.length, router]);

  // a preview address for each photo as chosen, and for its edited version if there is one
  useEffect(() => {
    const made: string[] = [];
    const next: Record<string, string> = {};
    for (const s of flow.shots) { const u = URL.createObjectURL(s.edited ?? s.source); made.push(u); next[s.key] = u; }
    setUrls(next);
    return () => made.forEach(URL.revokeObjectURL);
  }, [flow.shots]);

  // the basic check on the phone (advisory; its scope is stated on screen): one photo at a time, only for photos not checked yet
  useEffect(() => {
    let live = true;
    const todo = flow.shots.filter((s) => !quick[s.key]);
    (async () => { for (const s of todo) { const r = await quickCheck(s.edited ?? s.source, flow.region); if (!live) return; setQuick((q) => ({ ...q, [s.key]: r })); } })();
    return () => { live = false; };
  }, [flow.shots, flow.region]); // eslint-disable-line react-hooks/exhaustive-deps

  const idx = Math.min(sel, flow.shots.length - 1);
  const shot = flow.shots[idx];
  const q = shot ? quick[shot.key] : undefined;
  const room = MAX_PHOTOS - flow.shots.length;
  const sizeText = useMemo(() => (q?.checked ? `${q.width} × ${q.height} px` : ""), [q]);

  async function addFiles(files: File[], replaceCurrent = false) {
    setProblem("");
    const { good, problem: p } = await screenPhotos(files.slice(0, replaceCurrent ? 1 : Math.max(0, room)));
    if (!good.length) { setProblem(p || (room <= 0 ? `You can add up to ${MAX_PHOTOS} photos at a time.` : "We couldn't use that photo. Please try another.")); return; }
    if (replaceCurrent && shot) flow.remove(shot.key);
    good.forEach((f) => flow.add(f));
    setSel(99);                                                      // clamped to the last photo
  }
  async function native(kind: "camera" | "gallery", replaceCurrent = false) {
    setProblem(""); setAdding(false); setPicking(kind);
    const r: Picked = kind === "camera" ? await takeNativePhoto() : await chooseNativePhotos(Math.max(1, replaceCurrent ? 1 : room));
    setPicking("");
    if (r.kind === "error") setProblem(r.message);
    else if (r.kind === "photos") await addFiles(r.files, replaceCurrent);
  }
  /** Retake: in the app the phone's camera opens straight away and the new photo replaces this one; on the web the person returns to the capture screen. */
  function retake() {
    if (!shot) return;
    if (NATIVE_PHOTOS) { void native("camera", true); return; }
    flow.remove(shot.key);
    if (flow.shots.length <= 1) router.push("/photo");
    setSel(0);
  }
  const replaceFromGallery = () => { if (NATIVE_PHOTOS) void native("gallery", true); else replaceRef.current?.click(); };
  const remove = () => { if (shot) { flow.remove(shot.key); setSel(0); } };
  const addAnother = () => { if (room <= 0) { setProblem(`You can add up to ${MAX_PHOTOS} photos at a time.`); return; } if (NATIVE_PHOTOS) setAdding(true); else fileRef.current?.click(); };

  async function openEditor() {
    if (!shot) return;
    setProblem("");
    try { const bitmap = await loadBitmap(shot.source); setEditing({ key: shot.key, bitmap, edit: defaultEdit(bitmap.width / bitmap.height) }); }       // the frame starts as the photo itself: nothing is cropped until the person asks
    catch (e) { setProblem(e instanceof Error ? e.message : "We couldn't open this photo for editing."); }
  }
  async function applyEdit() {
    if (!editing) return;
    try { flow.setEdited(editing.key, await exportEdit(editing.bitmap, editing.edit)); setQuick((x) => { const { [editing.key]: _gone, ...rest } = x; void _gone; return rest; }); setEditing(null); }
    catch (e) { setProblem(e instanceof Error ? e.message : "We couldn't apply your changes."); }
  }

  async function next() {
    setBusy(true); setProblem("");
    try {
      const prepared = await prepare(flow.shots.map((s) => ({ source: s.source, edited: s.edited })));
      const id = analysisStore.start(flow.region, prepared.map((p) => ({ blob: p.blob, name: p.name })));
      leaving.current = true;
      flow.clear();
      router.replace(`/ask?pending=${id}`);                         // the preview is not a place to come back to once the photo is sent
    } catch (e) { setProblem(e instanceof Error ? e.message : "We couldn't prepare the photos. Please try again."); setBusy(false); }
  }

  if (!shot) return null;
  return (
    <div className="space-y-5">
      <ScreenBar back="/photo" right={<ProgressSteps step={2} total={2} />} />
      <div className="hidden justify-end md:flex"><ProgressSteps step={2} total={2} /></div>
      <PageHeading eyebrow="Photo preview" title="Your Photo" />
      {problem && <Note tone="bad">{problem}</Note>}

      <div className="mx-auto max-w-2xl space-y-4">
        {editing && editing.key === shot.key ? (
          <div className="space-y-3">
            <ImageEditor key={editing.key} bitmap={editing.bitmap} edit={editing.edit} onChange={(e) => setEditing((x) => (x ? { ...x, edit: e } : x))} />
            <div className="grid grid-cols-2 gap-3">
              <button className="btn !py-3" onClick={() => setEditing(null)}><X className="h-5 w-5" aria-hidden /> Cancel</button>
              <button className="btn btn-primary !py-3" onClick={() => void applyEdit()}><Check className="h-5 w-5" aria-hidden /> Apply</button>
            </div>
          </div>
        ) : (
          <>
            <figure className="overflow-hidden rounded-[26px] border border-line bg-[#efe9dd]">
              {urls[shot.key] ? <LocalImage src={urls[shot.key]} alt={`Your selected photo${flow.shots.length > 1 ? ` ${idx + 1} of ${flow.shots.length}` : ""}`} className="mx-auto block max-h-[52dvh] w-full object-contain" data-preview />
                : <div className="h-72 animate-pulse bg-line" aria-label="Loading photo" />}
            </figure>
            {q && (q.checked && q.issues.length > 0
              ? <Note tone="warn"><p className="font-semibold">Quick check on your phone</p><ul className="mt-1 list-disc space-y-0.5 pl-5 text-sm">{q.issues.map((i) => <li key={i.id}>{i.message}</li>)}</ul>
                  <p className="mt-1 text-xs">You can still continue: the full check happens after you do.</p></Note>
              : <p className="text-center text-xs text-muted" data-quickcheck>{q.checked ? `Quick check on your phone (brightness, blur, size) found nothing obvious${sizeText ? ` · ${sizeText}` : ""}. The full photo check runs after you continue.` : "The quick check on your phone could not run. The full photo check runs after you continue."}</p>)}

            {flow.shots.length > 1 || room > 0 ? (
              <div className="scroll-x" role="listbox" aria-label="Your photos">
                {flow.shots.map((s, i) => (
                  <button key={s.key} role="option" aria-selected={i === idx} className={`h-20 w-20 shrink-0 overflow-hidden rounded-2xl border-2 ${i === idx ? "border-teal" : "border-transparent"}`} onClick={() => setSel(i)} aria-label={`Photo ${i + 1}`}>
                    {urls[s.key] && <LocalImage src={urls[s.key]} alt="" className="h-full w-full object-cover" />}</button>))}
                {room > 0 && <button className="grid h-20 w-24 shrink-0 place-items-center rounded-2xl border-2 border-dashed border-line bg-white/60 px-2 text-center text-xs font-semibold text-muted" onClick={addAnother}><Plus className="h-5 w-5" aria-hidden />Add another photo</button>}
              </div>) : null}
            <input ref={fileRef} type="file" accept="image/*" multiple className="sr-only" aria-label="Add another photo" onChange={(e) => { const f = Array.from(e.target.files ?? []); e.target.value = ""; void addFiles(f); }} />
            <input ref={replaceRef} type="file" accept="image/*" className="sr-only" aria-label="Replace this photo" onChange={(e) => { const f = Array.from(e.target.files ?? []); e.target.value = ""; void addFiles(f, true); }} />

            <div className="grid grid-cols-3 gap-2">
              <button className="btn !px-2 !py-3" onClick={retake} disabled={picking !== ""}>{picking === "camera" ? <Loader2 className="spin h-5 w-5" aria-hidden /> : <Camera className="h-5 w-5" aria-hidden />} Retake</button>
              <button className="btn !px-2 !py-3" onClick={replaceFromGallery} disabled={picking !== ""}>{picking === "gallery" ? <Loader2 className="spin h-5 w-5" aria-hidden /> : <ImageIcon className="h-5 w-5" aria-hidden />} Replace</button>
              <button className="btn !px-2 !py-3" onClick={remove}><Trash2 className="h-5 w-5" aria-hidden /> Remove</button>
            </div>
            <div className="flex flex-wrap items-center justify-center gap-3 text-sm">
              <button type="button" className="inline-flex items-center gap-1 font-semibold text-teal-dark underline" onClick={() => void openEditor()}><Crop className="h-4 w-4" aria-hidden /> Crop or rotate</button>
              {shot.edited && <button type="button" className="inline-flex items-center gap-1 font-semibold text-teal-dark underline" onClick={() => { flow.setEdited(shot.key, null); setQuick((x) => { const { [shot.key]: _g, ...rest } = x; void _g; return rest; }); }}><RefreshCw className="h-4 w-4" aria-hidden /> Use the original</button>}
              {shot.edited && <span className="text-xs text-muted">Edited: this version will be sent.</span>}
            </div>
            <button className="btn btn-primary w-full !py-3" onClick={() => void next()} disabled={busy || picking !== ""}>{busy ? <><Loader2 className="spin h-5 w-5" aria-hidden /> Preparing…</> : <>Continue <ArrowRight className="h-5 w-5" aria-hidden /></>}</button>
            <p className="text-center text-sm text-muted">Your photo is sent as you see it. Nothing is sent until you tap Continue.</p>
          </>)}
      </div>
      {adding && (
        <Dialog title="Add another photo" onClose={() => setAdding(false)}>
          <div className="grid gap-3">
            <button className="btn btn-primary !py-3" onClick={() => void native("camera")}><Camera className="h-5 w-5" aria-hidden /> Take a photo</button>
            <button className="btn !py-3" onClick={() => void native("gallery")}><ImageIcon className="h-5 w-5" aria-hidden /> Upload from gallery</button>
          </div>
        </Dialog>
      )}
    </div>
  );
}
