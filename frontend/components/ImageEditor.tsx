"use client";

import { FlipHorizontal2, RotateCw, RotateCcw, Crop, Minus, Plus } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

export type Edit = { rot: number; flip: boolean; zoom: number; x: number; y: number; aspect: number };
export const ASPECTS = [{ id: "1:1", v: 1, label: "Square" }, { id: "3:4", v: 3 / 4, label: "Portrait" }, { id: "4:3", v: 4 / 3, label: "Landscape" }, { id: "9:16", v: 9 / 16, label: "Story" }];
export const defaultEdit = (aspect = 3 / 4): Edit => ({ rot: 0, flip: false, zoom: 1, x: 0, y: 0, aspect });
const OUT_MAX = 2048;
const SRC_MAX = 4096;

/** Decode honouring EXIF orientation, and cap the working size so very large photos stay responsive. */
export async function loadBitmap(file: Blob): Promise<ImageBitmap> {
  let bmp: ImageBitmap;
  try { bmp = await createImageBitmap(file, { imageOrientation: "from-image" }); }
  catch { throw new Error("We couldn't read this image. Please try another photo."); }
  const long = Math.max(bmp.width, bmp.height);
  if (long <= SRC_MAX) return bmp;
  const k = SRC_MAX / long;
  const small = await createImageBitmap(bmp, { resizeWidth: Math.round(bmp.width * k), resizeHeight: Math.round(bmp.height * k), resizeQuality: "high" });
  bmp.close();
  return small;
}

type Geo = { rw: number; rh: number; base: number; s: number; maxX: number; maxY: number };
function geometry(bmp: { width: number; height: number }, e: Edit, cw: number, ch: number): Geo {
  const rotated = e.rot % 180 !== 0;
  const rw = rotated ? bmp.height : bmp.width, rh = rotated ? bmp.width : bmp.height;
  const base = Math.max(cw / rw, ch / rh);
  const s = base * e.zoom;
  return { rw, rh, base, s, maxX: Math.max(0, (rw * s - cw) / 2), maxY: Math.max(0, (rh * s - ch) / 2) };
}
const clamp = (v: number, lim: number) => Math.max(-lim, Math.min(lim, v));

function draw(ctx: CanvasRenderingContext2D, bmp: ImageBitmap, e: Edit, ox: number, oy: number, scale: number) {
  ctx.save();
  ctx.translate(ox, oy);
  ctx.rotate((e.rot * Math.PI) / 180);
  ctx.scale(e.flip ? -scale : scale, scale);
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(bmp, -bmp.width / 2, -bmp.height / 2);
  ctx.restore();
}

function cropSize(vw: number, vh: number, aspect: number) {
  const pad = 20, w = vw - pad * 2, h = vh - pad * 2;
  return aspect >= w / h ? { cw: w, ch: w / aspect } : { cw: h * aspect, ch: h };
}

/** Render the cropped result at (up to) source resolution, capped to 2048 px on the long side. */
export function exportEdit(bmp: ImageBitmap, e: Edit, vw = 600, vh = 600): Promise<Blob> {
  const { cw, ch } = cropSize(vw, vh, e.aspect);
  const g = geometry(bmp, e, cw, ch);
  const x = clamp(e.x, g.maxX), y = clamp(e.y, g.maxY);
  const k = Math.min(1 / g.s, OUT_MAX / Math.max(cw, ch));
  const c = document.createElement("canvas");
  c.width = Math.round(cw * k); c.height = Math.round(ch * k);
  const ctx = c.getContext("2d")!;
  draw(ctx, bmp, e, c.width / 2 + x * k, c.height / 2 + y * k, g.s * k);
  return new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error("Could not save the edited photo."))), "image/jpeg", 0.92));
}

function Tool({ label, onClick, children }: { label: string; onClick: () => void; children: React.ReactNode }) {
  return (
    <button className="flex min-h-[56px] min-w-[64px] flex-col items-center justify-center gap-1 rounded-2xl px-2 text-xs font-semibold text-white/90 hover:bg-white/10" onClick={onClick} aria-label={label}>{children}<span>{label}</span></button>
  );
}

export default function ImageEditor({ bitmap, edit, onChange }: { bitmap: ImageBitmap; edit: Edit; onChange: (e: Edit) => void }) {
  const box = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ w: 600, h: 460 });
  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const last = useRef<{ d: number; zoom: number } | null>(null);

  useEffect(() => {
    const el = box.current; if (!el) return;
    const ro = new ResizeObserver(() => { const w = el.clientWidth; setSize({ w, h: Math.round(Math.min(w * 0.95, 520)) }); });
    ro.observe(el); return () => ro.disconnect();
  }, []);

  const { cw, ch } = cropSize(size.w, size.h, edit.aspect);
  const g = geometry(bitmap, edit, cw, ch);

  // keep the picture covering the crop box whenever zoom/aspect/rotation changes
  useEffect(() => {
    const nx = clamp(edit.x, g.maxX), ny = clamp(edit.y, g.maxY);
    if (nx !== edit.x || ny !== edit.y) onChange({ ...edit, x: nx, y: ny });
  }, [g.maxX, g.maxY]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const c = canvas.current; if (!c) return;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    c.width = size.w * dpr; c.height = size.h * dpr;
    const ctx = c.getContext("2d")!;
    ctx.scale(dpr, dpr);
    ctx.fillStyle = "#3a3f3e"; ctx.fillRect(0, 0, size.w, size.h);
    draw(ctx, bitmap, edit, size.w / 2 + clamp(edit.x, g.maxX), size.h / 2 + clamp(edit.y, g.maxY), g.s);
    // dim outside the crop box, then draw guides
    const l = (size.w - cw) / 2, t = (size.h - ch) / 2;
    ctx.fillStyle = "rgba(10,20,20,.45)";
    ctx.beginPath(); ctx.rect(0, 0, size.w, size.h); ctx.rect(l, t, cw, ch); ctx.fill("evenodd");
    ctx.strokeStyle = "rgba(255,255,255,.95)"; ctx.lineWidth = 2; ctx.strokeRect(l, t, cw, ch);
    ctx.strokeStyle = "rgba(255,255,255,.35)"; ctx.lineWidth = 1;
    for (let i = 1; i < 3; i++) { ctx.beginPath(); ctx.moveTo(l + (cw * i) / 3, t); ctx.lineTo(l + (cw * i) / 3, t + ch); ctx.moveTo(l, t + (ch * i) / 3); ctx.lineTo(l + cw, t + (ch * i) / 3); ctx.stroke(); }
    ctx.fillStyle = "#fff";
    for (const [px, py] of [[l, t], [l + cw, t], [l, t + ch], [l + cw, t + ch]]) { ctx.beginPath(); ctx.arc(px, py, 7, 0, 7); ctx.fill(); }
  }, [bitmap, edit, size, cw, ch, g.s, g.maxX, g.maxY]);

  const setZoom = useCallback((z: number) => onChange({ ...edit, zoom: Math.max(1, Math.min(4, z)) }), [edit, onChange]);

  const onDown = (e: React.PointerEvent) => { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY }); last.current = null; };
  const onMove = (e: React.PointerEvent) => {
    const p = pointers.current.get(e.pointerId); if (!p) return;
    if (pointers.current.size === 2) {                        // pinch to zoom
      pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
      const [a, b] = [...pointers.current.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (last.current) setZoom((last.current.zoom * d) / last.current.d); else last.current = { d, zoom: edit.zoom };
      return;
    }
    const dx = e.clientX - p.x, dy = e.clientY - p.y;
    pointers.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
    onChange({ ...edit, x: clamp(edit.x + dx, g.maxX), y: clamp(edit.y + dy, g.maxY) });
  };
  const onUp = (e: React.PointerEvent) => { pointers.current.delete(e.pointerId); last.current = null; };
  const onKey = (e: React.KeyboardEvent) => {
    const step = 12, d = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key];
    if (d) { e.preventDefault(); onChange({ ...edit, x: clamp(edit.x + d[0], g.maxX), y: clamp(edit.y + d[1], g.maxY) }); }
    if (e.key === "+" || e.key === "=") setZoom(edit.zoom + 0.1);
    if (e.key === "-") setZoom(edit.zoom - 0.1);
  };

  return (
    <div className="space-y-4">
      <div className="relative">
        <div ref={box} className="w-full overflow-hidden rounded-[26px]" style={{ height: size.h }}>
          <canvas ref={canvas} style={{ width: size.w, height: size.h, touchAction: "none", cursor: "grab" }} tabIndex={0} role="img"
            aria-label="Photo editor. Drag to reposition, use arrow keys to move, plus and minus to zoom."
            onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}
            onWheel={(e) => setZoom(edit.zoom - e.deltaY * 0.002)} onKeyDown={onKey} />
        </div>
        <div className="absolute right-3 top-1/2 flex -translate-y-1/2 flex-col rounded-[22px] bg-[#2a3a3a]/85 p-1 backdrop-blur" role="toolbar" aria-label="Edit tools">
          <Tool label="Rotate" onClick={() => onChange({ ...edit, rot: (edit.rot + 90) % 360 })}><RotateCw className="h-5 w-5" /></Tool>
          <Tool label="Left" onClick={() => onChange({ ...edit, rot: (edit.rot + 270) % 360 })}><RotateCcw className="h-5 w-5" /></Tool>
          <Tool label="Flip" onClick={() => onChange({ ...edit, flip: !edit.flip })}><FlipHorizontal2 className="h-5 w-5" /></Tool>
          <Tool label="Reset" onClick={() => onChange(defaultEdit(edit.aspect))}><Crop className="h-5 w-5" /></Tool>
        </div>
      </div>
      <div className="flex items-center gap-3 px-1">
        <button className="btn btn-icon" onClick={() => setZoom(edit.zoom - 0.25)} aria-label="Zoom out"><Minus className="h-5 w-5" /></button>
        <input className="range" type="range" min={1} max={4} step={0.01} value={edit.zoom} onChange={(e) => setZoom(Number(e.target.value))} aria-label="Zoom" />
        <button className="btn btn-icon" onClick={() => setZoom(edit.zoom + 0.25)} aria-label="Zoom in"><Plus className="h-5 w-5" /></button>
      </div>
      <div className="grid grid-cols-4 gap-2" role="group" aria-label="Crop shape">
        {ASPECTS.map((a) => (
          <button key={a.id} className="chip !flex-col !gap-0 !rounded-2xl !px-2 !py-2" aria-pressed={Math.abs(edit.aspect - a.v) < 0.001} onClick={() => onChange({ ...edit, aspect: a.v, x: 0, y: 0 })}>
            <span>{a.id}</span><span className="text-[.7rem] font-normal opacity-80">{a.label}</span></button>))}
      </div>
    </div>
  );
}
