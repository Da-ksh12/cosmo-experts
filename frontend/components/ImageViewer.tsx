"use client";

import { Eye, EyeOff, Maximize2, Minus, Plus, RotateCcw, RotateCw, Scan, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { anchorOf, clamp, clampPan, fit, Geometry, MAX_ZOOM, MIN_ZOOM, panToCentre, Pt, Rot, zoomAround, zoomForShape } from "@/lib/viewerMath";

export type Marker = { id: string; label: string; number: number; geometry: Geometry; tone: "ok" | "attention" | "info" };
export type FocusRequest = { id: string; nonce: number };

type Props = {
  src: string; alt: string;
  width: number; height: number;                                  // natural size of the photo AS DISPLAYED: the reference of every coordinate
  markers?: Marker[]; selectedId?: string | null; onSelect?: (id: string | null) => void;
  showAnnotations?: boolean; onToggleAnnotations?: () => void;
  focus?: FocusRequest | null;                                    // "View on photo": zoom to a marker and highlight it
  fullscreen?: boolean; onFullscreen?: () => void; onClose?: () => void;
};

const STROKE = { ok: "#1f7a55", attention: "#b4570a", info: "#064f50" } as const;
const rotateBy = (r: Rot, d: number) => ((r + d + 360) % 360) as Rot;

/** The photo, exactly as stored (never stretched or cropped: it is fitted whole into the frame), with pinch / wheel / double-tap zoom, panning, quarter-turn rotation, reset and a full-screen
 *  view. Markers and outlines are drawn INSIDE the transformed photo at percentage positions, so they share its transform and stay on the same spot of the photo at every zoom, pan, rotation
 *  and frame size; only their size is counter-scaled so they stay the same size on screen. The geometry of this file is pinned by tests/viewerMath.test.mjs and e2e/photo-analysis.mjs. */
export default function ImageViewer({ src, alt, width, height, markers = [], selectedId = null, onSelect, showAnnotations = true, onToggleAnnotations, focus = null, fullscreen = false, onFullscreen, onClose }: Props) {
  const frame = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [zoom, setZoom] = useState(1);
  const [rot, setRot] = useState<Rot>(0);
  const [pan, setPan] = useState<Pt>({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const [mismatch, setMismatch] = useState(false);
  const pointers = useRef(new Map<number, Pt>());
  const gesture = useRef<{ kind: "none" | "pan" | "pinch"; pan0: Pt; z0: number; p0: Pt; d0: number; mid0: Pt; moved: boolean; t0: number }>({ kind: "none", pan0: { x: 0, y: 0 }, z0: 1, p0: { x: 0, y: 0 }, d0: 1, mid0: { x: 0, y: 0 }, moved: false, t0: 0 });
  const lastTap = useRef<{ t: number; p: Pt } | null>(null);

  // frame size: full width of its container; its height follows the photo's own shape (a tall photo is limited to about half the screen), or fills the space in full-screen
  useEffect(() => {
    const el = frame.current;
    if (!el) return;
    const measure = () => {
      const w = el.clientWidth;
      if (fullscreen) { setSize({ w, h: el.clientHeight }); return; }
      const ratio = rot % 180 === 0 ? height / width : width / height;
      setSize({ w, h: Math.round(Math.min(w * ratio, Math.max(220, window.innerHeight * 0.56))) });
    };
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    window.addEventListener("resize", measure);
    return () => { ro.disconnect(); window.removeEventListener("resize", measure); };
  }, [fullscreen, width, height, rot]);

  const box = size;
  const f = box.w > 0 && box.h > 0 && width > 0 && height > 0 ? fit({ w: width, h: height }, box, rot) : null;
  const setView = useCallback((z: number, p: Pt) => {
    if (!f) return;
    const z1 = clamp(z, MIN_ZOOM, MAX_ZOOM);
    setZoom(z1);
    setPan(z1 <= 1.001 ? { x: 0, y: 0 } : clampPan(p, f.bbox, box, z1));
  }, [f, box]);
  const zoomAt = useCallback((z1: number, anchor?: Pt) => {
    const a = anchor ?? { x: box.w / 2, y: box.h / 2 };
    setView(z1, zoomAround(pan, zoom, clamp(z1, MIN_ZOOM, MAX_ZOOM), a, box));
  }, [box, pan, zoom, setView]);
  const reset = () => { setZoom(1); setPan({ x: 0, y: 0 }); };
  const turn = (d: number) => { setRot((r) => rotateBy(r, d)); reset(); };

  // "View on photo"
  useEffect(() => {
    if (!focus || !f) return;
    const m = markers.find((x) => x.id === focus.id);
    if (!m) return;
    const z = zoomForShape(m.geometry, f.el, rot, box);
    // a one-off response to the request to view an area (its size comes from the measured frame, so it cannot be worked out while rendering)
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setZoom(z);
    setPan(clampPan(panToCentre(anchorOf(m.geometry), f.el, rot, z), f.bbox, box, z));
  }, [focus?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  // wheel: ctrl/cmd + wheel (a trackpad pinch) zooms at the cursor; a plain wheel pans a zoomed photo and otherwise scrolls the page as usual
  useEffect(() => {
    const el = frame.current;
    if (!el || !f) return;
    const onWheel = (e: WheelEvent) => {
      const r = el.getBoundingClientRect();
      if (e.ctrlKey || e.metaKey) { e.preventDefault(); zoomAt(zoom * Math.exp(-e.deltaY * 0.01), { x: e.clientX - r.left, y: e.clientY - r.top }); }
      else if (zoom > 1.001) { e.preventDefault(); setPan((p) => clampPan({ x: p.x - e.deltaX, y: p.y - e.deltaY }, f.bbox, box, zoom)); }
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [f, box, zoom, zoomAt]);

  const rel = (e: { clientX: number; clientY: number }): Pt => { const r = frame.current!.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  const onDown = (e: React.PointerEvent) => {
    if ((e.target as HTMLElement).closest("[data-marker]")) return;               // a marker is a button: it takes the tap
    pointers.current.set(e.pointerId, rel(e));
    frame.current?.setPointerCapture?.(e.pointerId);
    const g = gesture.current, pts = [...pointers.current.values()];
    g.moved = false; g.t0 = Date.now();
    if (pts.length === 2) {
      g.kind = "pinch"; g.z0 = zoom; g.pan0 = pan; g.d0 = Math.max(1, Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y)); g.mid0 = { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 };
      setDragging(true);
    } else if (pts.length === 1) {
      g.kind = "pan"; g.pan0 = pan; g.p0 = pts[0];
      if (zoom > 1.001) setDragging(true);
    }
  };
  const onMove = (e: React.PointerEvent) => {
    if (!f || !pointers.current.has(e.pointerId)) return;
    pointers.current.set(e.pointerId, rel(e));
    const g = gesture.current, pts = [...pointers.current.values()];
    if (g.kind === "pinch" && pts.length >= 2) {
      const d = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y), mid = { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 };
      const z1 = clamp(g.z0 * (d / g.d0), MIN_ZOOM, MAX_ZOOM);
      const p = zoomAround(g.pan0, g.z0, z1, g.mid0, box);
      g.moved = true;
      setZoom(z1); setPan(z1 <= 1.001 ? { x: 0, y: 0 } : clampPan({ x: p.x + (mid.x - g.mid0.x), y: p.y + (mid.y - g.mid0.y) }, f.bbox, box, z1));
    } else if (g.kind === "pan" && pts.length === 1 && zoom > 1.001) {
      const dx = pts[0].x - g.p0.x, dy = pts[0].y - g.p0.y;
      if (Math.hypot(dx, dy) > 4) g.moved = true;
      setPan(clampPan({ x: g.pan0.x + dx, y: g.pan0.y + dy }, f.bbox, box, zoom));
    } else if (g.kind === "pan" && Math.hypot(pts[0].x - g.p0.x, pts[0].y - g.p0.y) > 6) g.moved = true;
  };
  const onUp = (e: React.PointerEvent) => {
    const had = pointers.current.delete(e.pointerId);
    if (!had) return;
    frame.current?.releasePointerCapture?.(e.pointerId);
    const g = gesture.current, left = [...pointers.current.values()];
    if (left.length === 1) { g.kind = "pan"; g.pan0 = pan; g.p0 = left[0]; g.moved = true; }
    else if (left.length === 0) {
      setDragging(false);
      const tap = !g.moved && Date.now() - g.t0 < 350 && g.kind === "pan";
      g.kind = "none";
      if (tap) {
        const p = rel(e), now = Date.now(), prev = lastTap.current;
        if (prev && now - prev.t < 320 && Math.hypot(p.x - prev.p.x, p.y - prev.p.y) < 30) { lastTap.current = null; if (zoom > 1.2) reset(); else zoomAt(2.5, p); }
        else { lastTap.current = { t: now, p }; onSelect?.(null); }
      }
      if (zoom < 1.02) reset();
    }
  };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "+" || e.key === "=") { e.preventDefault(); zoomAt(zoom * 1.4); }
    else if (e.key === "-") { e.preventDefault(); zoomAt(zoom / 1.4); }
    else if (e.key === "0") { e.preventDefault(); reset(); }
    else if (f && zoom > 1.001 && e.key.startsWith("Arrow")) {
      e.preventDefault();
      const s = 40, d = { ArrowLeft: [s, 0], ArrowRight: [-s, 0], ArrowUp: [0, s], ArrowDown: [0, -s] }[e.key] ?? [0, 0];
      setPan((p) => clampPan({ x: p.x + d[0], y: p.y + d[1] }, f.bbox, box, zoom));
    }
  };

  const btn = "grid h-11 w-11 place-items-center rounded-full border border-line bg-white text-teal-dark transition hover:bg-teal-soft disabled:opacity-40";
  const shown = showAnnotations && !mismatch && markers.length > 0;
  const tr = dragging ? "none" : "transform 220ms cubic-bezier(.2,.7,.2,1)";

  const viewer = (
    <div className={fullscreen ? "flex h-full min-h-0 flex-col gap-3" : "space-y-2"}>
      <div ref={frame} role="group" aria-label={`Photo viewer. ${alt}. Pinch or use the plus and minus keys to zoom.`} tabIndex={0} onKeyDown={onKey}
        data-viewer data-zoom={zoom.toFixed(3)} data-rotation={rot}
        className={`relative w-full select-none overflow-hidden rounded-[22px] border border-line ${fullscreen ? "min-h-0 flex-1 bg-black" : "bg-[#efe9dd]"}`}
        style={{ height: fullscreen ? undefined : box.h || undefined, touchAction: zoom > 1.001 ? "none" : "pan-y", cursor: zoom > 1.001 ? (dragging ? "grabbing" : "grab") : "default" }}
        onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
        {f && (
          <div data-stage className="absolute left-1/2 top-1/2" style={{ width: f.el.w, height: f.el.h, marginLeft: -f.el.w / 2, marginTop: -f.el.h / 2, transformOrigin: "center", transition: tr,
            transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom}) rotate(${rot}deg)` }}>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={src} alt={alt} draggable={false} className="block h-full w-full select-none"
              onLoad={(e) => { const i = e.currentTarget; setMismatch(Math.abs(i.naturalWidth / i.naturalHeight - width / height) > 0.012); }} />
            {shown && (
              <svg viewBox="0 0 1 1" preserveAspectRatio="none" className="pointer-events-none absolute inset-0 h-full w-full overflow-visible" aria-hidden>
                {markers.filter((m) => m.geometry.type !== "point").map((m) => {
                  const sel = m.id === selectedId, s = STROKE[m.tone];
                  const shape = m.geometry.type === "box" ? { tag: "rect" as const, props: { x: m.geometry.x, y: m.geometry.y, width: m.geometry.w, height: m.geometry.h, rx: 0.01 } }
                    : { tag: "polygon" as const, props: { points: (m.geometry as Extract<Geometry, { type: "polygon" }>).points.map((p) => p.join(",")).join(" ") } };
                  const common = { fill: sel ? `${s}22` : "none", strokeLinejoin: "round" as const, vectorEffect: "non-scaling-stroke" as const };
                  return (
                    <g key={m.id} data-outline={m.id} data-selected={sel ? "true" : "false"}>
                      {shape.tag === "rect" ? <rect {...shape.props} {...common} stroke="rgba(0,0,0,.45)" strokeWidth={sel ? 4.5 : 3} /> : <polygon {...shape.props} {...common} stroke="rgba(0,0,0,.45)" strokeWidth={sel ? 4.5 : 3} />}
                      {shape.tag === "rect" ? <rect {...shape.props} {...common} fill="none" stroke={sel ? "#fff" : "rgba(255,255,255,.9)"} strokeWidth={sel ? 2.5 : 1.5} strokeDasharray={sel ? undefined : "6 4"} />
                        : <polygon {...shape.props} {...common} fill="none" stroke={sel ? "#fff" : "rgba(255,255,255,.9)"} strokeWidth={sel ? 2.5 : 1.5} strokeDasharray={sel ? undefined : "6 4"} />}
                    </g>);
                })}
              </svg>)}
            {shown && markers.map((m) => {
              const a = anchorOf(m.geometry), sel = m.id === selectedId;
              return (
                <button key={m.id} type="button" data-marker={m.id} data-number={m.number} aria-label={`Area ${m.number}: ${m.label}`} aria-pressed={sel}
                  onClick={(e) => { e.stopPropagation(); onSelect?.(sel ? null : m.id); }}
                  className={`absolute grid place-items-center rounded-full text-[11px] font-bold leading-none text-white shadow-md outline-none ring-2 transition-[box-shadow,background-color] focus-visible:ring-4 focus-visible:ring-gold ${sel ? "ring-4 ring-white" : "ring-white/90"}`}
                  style={{ left: `${a.x * 100}%`, top: `${a.y * 100}%`, width: 24, height: 24, background: sel ? "#b58a45" : STROKE[m.tone], transformOrigin: "center",
                    transform: `translate(-50%, -50%) rotate(${-rot}deg) scale(${(sel ? 1.18 : 1) / zoom})` }}>{m.number}</button>);
            })}
          </div>)}
        {mismatch && <p role="status" className="absolute inset-x-3 bottom-3 rounded-xl bg-black/70 px-3 py-2 text-center text-xs text-white">The photo&apos;s orientation could not be confirmed, so the markers are hidden.</p>}
        {fullscreen && onClose && <button type="button" className={`${btn} absolute right-3 top-3`} onClick={onClose} aria-label="Close full screen view"><X className="h-5 w-5" aria-hidden /></button>}
      </div>
      <div role="toolbar" aria-label="Photo view controls" className={`flex flex-wrap items-center gap-2 ${fullscreen ? "justify-center pb-[max(env(safe-area-inset-bottom),8px)]" : ""}`}>
        <button type="button" className={btn} onClick={() => zoomAt(zoom / 1.5)} disabled={zoom <= 1.001} aria-label="Zoom out"><Minus className="h-5 w-5" aria-hidden /></button>
        <button type="button" className={btn} onClick={() => zoomAt(zoom * 1.5)} disabled={zoom >= MAX_ZOOM - 0.001} aria-label="Zoom in"><Plus className="h-5 w-5" aria-hidden /></button>
        <button type="button" className={btn} onClick={reset} disabled={zoom <= 1.001 && pan.x === 0 && pan.y === 0} aria-label="Reset zoom"><Scan className="h-5 w-5" aria-hidden /></button>
        <button type="button" className={btn} onClick={() => turn(-90)} aria-label="Rotate left"><RotateCcw className="h-5 w-5" aria-hidden /></button>
        <button type="button" className={btn} onClick={() => turn(90)} aria-label="Rotate right"><RotateCw className="h-5 w-5" aria-hidden /></button>
        {onFullscreen && !fullscreen && <button type="button" className={btn} onClick={onFullscreen} aria-label="Full screen"><Maximize2 className="h-5 w-5" aria-hidden /></button>}
        {markers.length > 0 && onToggleAnnotations && (
          <button type="button" className="btn btn-sm ml-auto" onClick={onToggleAnnotations} aria-pressed={showAnnotations}>{showAnnotations ? <EyeOff className="h-4 w-4" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />} {showAnnotations ? "Hide" : "Show"} annotations</button>)}
        <span className="sr-only" aria-live="polite">{`Zoom ${Math.round(zoom * 100)} percent`}</span>
      </div>
    </div>);

  if (!fullscreen) return viewer;
  return createPortal(
    <div role="dialog" aria-modal="true" aria-label="Full screen photo" className="fixed inset-0 z-[70] flex flex-col bg-black/95 p-3 pt-[max(env(safe-area-inset-top),12px)]"
      onKeyDown={(e) => { if (e.key === "Escape") onClose?.(); }}>
      {viewer}
    </div>, document.body);
}
