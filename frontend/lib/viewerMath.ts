// The geometry of the photo viewer, as pure functions (no DOM, no React) so that it can be tested exactly.
//
// A photo is described by its NATURAL size (pixels of the stored photo as displayed). Marker and outline geometry arrive as fractions (0 to 1) of that size, origin top-left, y downwards
// (the contract in backend/app/photo_analysis/schema.py). The viewer draws the photo as an element of its fitted size, centred in a container, and applies
//        translate(pan) scale(zoom) rotate(rot)
// to it (CSS applies the functions right to left: rotate first, then scale, then move). Markers live INSIDE that element at percentage positions, so they share its transform: they cannot
// drift away from the photo whatever the zoom, pan, rotation or container size. These functions compute the same positions in screen terms, for focusing a marker, for clamping the pan and
// for the tests that pin the alignment.

export type Size = { w: number; h: number };
export type Pt = { x: number; y: number };
export type Rot = 0 | 90 | 180 | 270;
export type Geometry =
  | { type: "point"; x: number; y: number }
  | { type: "box"; x: number; y: number; w: number; h: number }
  | { type: "polygon"; points: [number, number][] };

export const MIN_ZOOM = 1;
export const MAX_ZOOM = 8;
export const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const unit = (v: unknown): v is number => typeof v === "number" && Number.isFinite(v) && v >= 0 && v <= 1;

/** The photo fitted ('contain') into the container before any zoom. `el` is the element's own (unrotated) size; `bbox` is the size of its bounding box once rotated by `rot`. */
export function fit(natural: Size, box: Size, rot: Rot): { el: Size; bbox: Size; scale: number } {
  const turned = rot % 180 !== 0;
  const rw = turned ? natural.h : natural.w, rh = turned ? natural.w : natural.h;
  const scale = Math.min(box.w / rw, box.h / rh);
  return { scale, el: { w: natural.w * scale, h: natural.h * scale }, bbox: { w: rw * scale, h: rh * scale } };
}

/** How far the photo may be moved: it cannot leave gaps inside the container, and an axis on which it is smaller than the container stays centred. */
export function clampPan(pan: Pt, bbox: Size, box: Size, zoom: number): Pt {
  const mx = Math.max(0, (bbox.w * zoom - box.w) / 2), my = Math.max(0, (bbox.h * zoom - box.h) / 2);
  return { x: clamp(pan.x, -mx, mx), y: clamp(pan.y, -my, my) };
}

/** The pan that keeps the point under `anchor` (container coordinates, origin top-left) where it is while the zoom changes from z0 to z1. */
export function zoomAround(pan: Pt, z0: number, z1: number, anchor: Pt, box: Size): Pt {
  const dx = anchor.x - box.w / 2, dy = anchor.y - box.h / 2;
  const k = z1 / z0;
  return { x: dx - (dx - pan.x) * k, y: dy - (dy - pan.y) * k };
}

/** A clockwise rotation (CSS rotate(), y down) of an offset from the centre. */
export function rotate(p: Pt, rot: Rot): Pt {
  switch (rot) {
    case 90: return { x: -p.y, y: p.x };
    case 180: return { x: -p.x, y: -p.y };
    case 270: return { x: p.y, y: -p.x };
    default: return { x: p.x, y: p.y };
  }
}

/** Where a normalised point of the photo appears, in container coordinates (origin top-left). */
export function toScreen(n: Pt, el: Size, rot: Rot, zoom: number, pan: Pt, box: Size): Pt {
  const r = rotate({ x: (n.x - 0.5) * el.w, y: (n.y - 0.5) * el.h }, rot);
  return { x: box.w / 2 + pan.x + zoom * r.x, y: box.h / 2 + pan.y + zoom * r.y };
}

/** The pan that brings a normalised point to the centre of the container at the given zoom (before clamping). */
export function panToCentre(n: Pt, el: Size, rot: Rot, zoom: number): Pt {
  const r = rotate({ x: (n.x - 0.5) * el.w, y: (n.y - 0.5) * el.h }, rot);
  return { x: -zoom * r.x, y: -zoom * r.y };
}

// ---------------------------------------------------------------- geometry
/** Is this a geometry the viewer may draw? The server validates the same rules; a shape that fails them is dropped, never repaired. */
export function validGeometry(g: unknown): g is Geometry {
  if (!g || typeof g !== "object") return false;
  const o = g as Record<string, unknown>;
  if (o.type === "point") return unit(o.x) && unit(o.y);
  if (o.type === "box") return unit(o.x) && unit(o.y) && unit(o.w) && unit(o.h) && (o.w as number) > 0 && (o.h as number) > 0 && (o.x as number) + (o.w as number) <= 1 + 1e-6 && (o.y as number) + (o.h as number) <= 1 + 1e-6;
  if (o.type === "polygon") return Array.isArray(o.points) && o.points.length >= 3 && o.points.length <= 64 && o.points.every((p) => Array.isArray(p) && p.length === 2 && unit(p[0]) && unit(p[1]));
  return false;
}

export function boundsOf(g: Geometry): { x: number; y: number; w: number; h: number } {
  if (g.type === "point") return { x: g.x, y: g.y, w: 0, h: 0 };
  if (g.type === "box") return { x: g.x, y: g.y, w: g.w, h: g.h };
  const xs = g.points.map((p) => p[0]), ys = g.points.map((p) => p[1]);
  const x = Math.min(...xs), y = Math.min(...ys);
  return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
}

/** The point a marker is drawn at: the point itself, the centre of a box, the centroid of a polygon's area. */
export function anchorOf(g: Geometry): Pt {
  if (g.type === "point") return { x: g.x, y: g.y };
  if (g.type === "box") return { x: g.x + g.w / 2, y: g.y + g.h / 2 };
  let a = 0, cx = 0, cy = 0;
  for (let i = 0; i < g.points.length; i++) {
    const [x0, y0] = g.points[i], [x1, y1] = g.points[(i + 1) % g.points.length];
    const f = x0 * y1 - x1 * y0;
    a += f; cx += (x0 + x1) * f; cy += (y0 + y1) * f;
  }
  if (Math.abs(a) < 1e-12) { const b = boundsOf(g); return { x: b.x + b.w / 2, y: b.y + b.h / 2 }; }
  return { x: cx / (3 * a), y: cy / (3 * a) };
}

/** The zoom that shows a shape comfortably (about `fill` of the container's smaller side), never less than a visible step in and never more than the limit. */
export function zoomForShape(g: Geometry, el: Size, rot: Rot, box: Size, fill = 0.5, lo = 1.8, hi = 5): number {
  const b = boundsOf(g);
  const turned = rot % 180 !== 0;
  const sw = (turned ? b.h : b.w) * (turned ? el.h : el.w), sh = (turned ? b.w : b.h) * (turned ? el.w : el.h);
  const want = Math.min(box.w / Math.max(sw, 1), box.h / Math.max(sh, 1)) * fill;
  return clamp(Number.isFinite(want) ? want : lo, lo, hi);
}
