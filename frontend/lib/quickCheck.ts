"use client";

/** A BASIC check of a photo on the phone, before anything is sent. Its scope, exactly: it measures how dark, bright and blurry the picture is and how many pixels it has, with the same
 *  measures the server uses (mean grey level, variance of the Laplacian on a picture scaled to 800 pixels), and it flags a photo ONLY when it is clearly beyond the server's own threshold.
 *  It cannot see faces, framing, something covering the frame or whether the photo suits the chosen area; the full check runs on the server after the person continues and is the one that
 *  counts. The screen says so, and says nothing like "suitable": a quick check that finds nothing has not approved the photo. */
export type QuickIssue = { id: "dark" | "bright" | "blurry" | "small"; message: string };
export type QuickResult = { checked: true; issues: QuickIssue[]; width: number; height: number } | { checked: false };

// The server's thresholds (backend/app/imaging/suitability.py QUALITY_PROFILES), by area. The face profile is the stricter one for sharpness and looser for exposure.
const PROFILE = { face: { dark: 55, bright: 215, sharp: 40 }, other: { dark: 85, bright: 200, sharp: 10.5 } };

export async function quickCheck(blob: Blob, region: string): Promise<QuickResult> {
  try {
    const bmp = await createImageBitmap(blob, { imageOrientation: "from-image" });
    const width = bmp.width, height = bmp.height;
    const k = Math.min(1, 800 / Math.max(width, height));
    const w = Math.max(3, Math.round(width * k)), h = Math.max(3, Math.round(height * k));
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    const ctx = c.getContext("2d", { willReadFrequently: true });
    if (!ctx) { bmp.close(); return { checked: false }; }
    ctx.drawImage(bmp, 0, 0, w, h);
    bmp.close();
    const px = ctx.getImageData(0, 0, w, h).data;
    const g = new Float32Array(w * h);
    let sum = 0;
    for (let i = 0, j = 0; i < px.length; i += 4, j++) { const v = 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2]; g[j] = v; sum += v; }
    const mean = sum / (w * h);
    let n = 0, s1 = 0, s2 = 0;
    for (let y = 1; y < h - 1; y++) for (let x = 1; x < w - 1; x++) {
      const i = y * w + x, lap = g[i - 1] + g[i + 1] + g[i - w] + g[i + w] - 4 * g[i];
      s1 += lap; s2 += lap * lap; n++;
    }
    const sharp = s2 / n - (s1 / n) ** 2;
    const p = region === "face" ? PROFILE.face : PROFILE.other;
    const issues: QuickIssue[] = [];
    if (Math.min(width, height) < 300) issues.push({ id: "small", message: "This photo is small, so it may not have enough detail. A photo taken with the phone's own camera works best." });
    if (mean < p.dark * 0.9) issues.push({ id: "dark", message: "This photo looks dark. Try brighter, even light, for example facing a window." });
    if (mean > Math.min(250, p.bright * 1.04)) issues.push({ id: "bright", message: "This photo looks washed out. Avoid flash and direct sun." });
    if (sharp < p.sharp * 0.6) issues.push({ id: "blurry", message: "This photo looks blurry. Hold the phone steady, tap the screen to focus, and try the rear camera." });
    return { checked: true, issues, width, height };
  } catch {
    return { checked: false };
  }
}
