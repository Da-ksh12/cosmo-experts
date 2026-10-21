// Pure geometry of the photo viewer (lib/viewerMath.ts). Run:  node --test tests/        (Node 22.6 or later strips the TypeScript types itself)
import assert from "node:assert/strict";
import test from "node:test";
import { anchorOf, boundsOf, clamp, clampPan, fit, panToCentre, rotate, toScreen, validGeometry, zoomAround, zoomForShape } from "../lib/viewerMath.ts";

const near = (a, b, eps = 1e-9) => assert.ok(Math.abs(a - b) <= eps, `${a} is not within ${eps} of ${b}`);
const nearPt = (p, x, y, eps = 1e-9) => { near(p.x, x, eps); near(p.y, y, eps); };

test("fit: a portrait photo in a square container keeps its aspect ratio (never stretched)", () => {
  const f = fit({ w: 400, h: 600 }, { w: 300, h: 300 }, 0);
  near(f.scale, 0.5); assert.deepEqual(f.el, { w: 200, h: 300 }); assert.deepEqual(f.bbox, { w: 200, h: 300 });
  near(f.el.w / f.el.h, 400 / 600);
});

test("fit: a quarter turn swaps the bounding box but not the element's own size", () => {
  const f = fit({ w: 400, h: 600 }, { w: 300, h: 300 }, 90);
  near(f.scale, 0.5); assert.deepEqual(f.el, { w: 200, h: 300 }); assert.deepEqual(f.bbox, { w: 300, h: 200 });
  const wide = fit({ w: 600, h: 400 }, { w: 300, h: 600 }, 90);                     // turned it is 400 wide and 600 tall: the container's width (300) is the limit
  near(wide.scale, 0.75); assert.deepEqual(wide.el, { w: 450, h: 300 }); assert.deepEqual(wide.bbox, { w: 300, h: 450 });
});

test("toScreen: the corners and the centre of the photo land where the transform puts them", () => {
  const box = { w: 300, h: 300 }, el = { w: 200, h: 300 };
  nearPt(toScreen({ x: 0.5, y: 0.5 }, el, 0, 1, { x: 0, y: 0 }, box), 150, 150);
  nearPt(toScreen({ x: 0, y: 0 }, el, 0, 1, { x: 0, y: 0 }, box), 50, 0);
  nearPt(toScreen({ x: 1, y: 1 }, el, 0, 1, { x: 0, y: 0 }, box), 250, 300);
  nearPt(toScreen({ x: 0, y: 0 }, el, 0, 2, { x: 0, y: 0 }, box), 150 - 200, 150 - 300);        // zoom 2 about the centre: the corner moves twice as far out
  nearPt(toScreen({ x: 0.5, y: 0.5 }, el, 0, 2, { x: 30, y: -20 }, box), 180, 130);            // pan moves everything
});

test("toScreen: rotating a quarter turn clockwise moves the top-left corner to the top-right", () => {
  const box = { w: 300, h: 300 }, el = { w: 200, h: 300 };
  nearPt(toScreen({ x: 0, y: 0 }, el, 90, 1, { x: 0, y: 0 }, box), 300, 50);                 // local (-100,-150) -> (150,-100) from the centre
  nearPt(toScreen({ x: 1, y: 0 }, el, 90, 1, { x: 0, y: 0 }, box), 300, 250);
  nearPt(toScreen({ x: 0, y: 0 }, el, 180, 1, { x: 0, y: 0 }, box), 250, 300);
  nearPt(toScreen({ x: 0, y: 0 }, el, 270, 1, { x: 0, y: 0 }, box), 0, 250);
  for (const r of [0, 90, 180, 270]) nearPt(toScreen({ x: 0.5, y: 0.5 }, el, r, 3, { x: 0, y: 0 }, box), 150, 150);   // the centre never moves with rotation or zoom
  assert.deepEqual(rotate(rotate({ x: 3, y: 4 }, 90), 270), { x: 3, y: 4 });
});

test("zoomAround keeps the point under the finger or cursor fixed", () => {
  const box = { w: 300, h: 400 }, el = { w: 300, h: 400 };
  const anchor = { x: 220, y: 90 };
  let pan = { x: 0, y: 0 }, z = 1;
  // the normalised photo point that is under the anchor at zoom 1
  const n = { x: (anchor.x - box.w / 2) / el.w + 0.5, y: (anchor.y - box.h / 2) / el.h + 0.5 };
  for (const z1 of [1.5, 2.25, 4, 3]) {
    pan = zoomAround(pan, z, z1, anchor, box); z = z1;
    const p = toScreen(n, el, 0, z, pan, box);
    nearPt(p, anchor.x, anchor.y, 1e-6);
  }
});

test("zoomAround also keeps the point fixed when the photo is rotated", () => {
  const box = { w: 300, h: 400 }, f = fit({ w: 600, h: 400 }, box, 90);
  const anchor = { x: 120, y: 310 };
  const z0 = 1, pan0 = { x: 0, y: 0 };
  // find the normalised point under the anchor at zoom 1 by inverting toScreen numerically on a grid (the function is affine, so two probes are enough)
  const p00 = toScreen({ x: 0, y: 0 }, f.el, 90, z0, pan0, box), p10 = toScreen({ x: 1, y: 0 }, f.el, 90, z0, pan0, box), p01 = toScreen({ x: 0, y: 1 }, f.el, 90, z0, pan0, box);
  const ax = { x: p10.x - p00.x, y: p10.y - p00.y }, ay = { x: p01.x - p00.x, y: p01.y - p00.y };
  const det = ax.x * ay.y - ax.y * ay.x, d = { x: anchor.x - p00.x, y: anchor.y - p00.y };
  const n = { x: (d.x * ay.y - d.y * ay.x) / det, y: (ax.x * d.y - ax.y * d.x) / det };
  const pan1 = zoomAround(pan0, z0, 3.5, anchor, box);
  nearPt(toScreen(n, f.el, 90, 3.5, pan1, box), anchor.x, anchor.y, 1e-6);
});

test("clampPan: no gaps when zoomed, centred when smaller than the container", () => {
  const box = { w: 300, h: 300 }, bbox = { w: 200, h: 300 };
  assert.deepEqual(clampPan({ x: 500, y: 500 }, bbox, box, 1), { x: 0, y: 0 });                    // at zoom 1 the photo is not wider than the container: nothing to pan sideways
  const z2 = clampPan({ x: 500, y: -500 }, bbox, box, 2);
  nearPt(z2, (200 * 2 - 300) / 2, -(300 * 2 - 300) / 2);                                           // 50 sideways, 150 up and down
  assert.deepEqual(clampPan({ x: 10, y: 20 }, bbox, box, 2), { x: 10, y: 20 });                    // inside the limits: untouched
});

test("panToCentre brings a normalised point to the middle of the container at any zoom and rotation", () => {
  const box = { w: 300, h: 300 }, el = { w: 200, h: 300 };
  for (const rot of [0, 90, 180, 270]) for (const z of [1.8, 3, 5]) for (const n of [{ x: 0.2, y: 0.8 }, { x: 0.9, y: 0.1 }]) {
    const pan = panToCentre(n, el, rot, z);
    nearPt(toScreen(n, el, rot, z, pan, box), 150, 150, 1e-9);
  }
});

test("geometry validation refuses what the server also refuses", () => {
  assert.ok(validGeometry({ type: "point", x: 0, y: 1 }));
  assert.ok(validGeometry({ type: "box", x: 0.1, y: 0.2, w: 0.3, h: 0.4 }));
  assert.ok(validGeometry({ type: "polygon", points: [[0.1, 0.1], [0.3, 0.1], [0.2, 0.3]] }));
  for (const bad of [null, {}, { type: "circle" }, { type: "point", x: -0.01, y: 0.5 }, { type: "point", x: NaN, y: 0.5 }, { type: "point", x: "0.5", y: 0.5 },
    { type: "box", x: 0.9, y: 0, w: 0.3, h: 0.1 }, { type: "box", x: 0, y: 0, w: 0, h: 0.1 }, { type: "polygon", points: [[0, 0], [1, 1]] }, { type: "polygon", points: [[0, 0], [0.5, 2], [1, 1]] }])
    assert.equal(validGeometry(bad), false, JSON.stringify(bad));
});

test("anchors: a box is marked at its centre, a polygon at the centroid of its area", () => {
  nearPt(anchorOf({ type: "box", x: 0.2, y: 0.4, w: 0.2, h: 0.2 }), 0.3, 0.5);
  nearPt(anchorOf({ type: "polygon", points: [[0, 0], [0.6, 0], [0, 0.3]] }), 0.2, 0.1);              // a right triangle: one third along each leg
  nearPt(anchorOf({ type: "polygon", points: [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5], [0.1, 0.5]] }), 0.3, 0.3);
  const b = boundsOf({ type: "polygon", points: [[0.1, 0.5], [0.4, 0.2], [0.3, 0.9]] });
  near(b.x, 0.1); near(b.y, 0.2); near(b.w, 0.3); near(b.h, 0.7);
});

test("zoomForShape is bounded and larger for small shapes", () => {
  const box = { w: 300, h: 300 }, el = { w: 300, h: 300 };
  const small = zoomForShape({ type: "box", x: 0.4, y: 0.4, w: 0.05, h: 0.05 }, el, 0, box);
  const big = zoomForShape({ type: "box", x: 0.1, y: 0.1, w: 0.6, h: 0.6 }, el, 0, box);
  assert.ok(small > big && small <= 5 && big >= 1.8);
  assert.equal(zoomForShape({ type: "point", x: 0.5, y: 0.5 }, el, 0, box), 5);
  assert.equal(clamp(9, 1, 8), 8);
});
