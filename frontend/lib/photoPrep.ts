"use client";

/** Getting the chosen photos ready to send, WITHOUT changing what the person chose:
 *  - a photo that was not edited is sent exactly as it is (the same bytes, including its EXIF orientation, which the server applies and reports);
 *  - a photo the person cropped or rotated in the adjust step is sent as that edited result (they asked for it);
 *  - a format the server does not read (WebP, GIF, HEIC the phone could decode, ...) is converted to a JPEG of the SAME pixels (full field of view, nothing cropped);
 *  - only when the photos together would exceed what one request may carry are the oversized ones re-encoded smaller, step by step, still uncropped.
 *  The server's own size limit (15 MB per request) and its quality check remain the authority. */
export const UPLOAD_BUDGET = 14 * 1024 * 1024;

export type Prepared = { blob: Blob; name: string; reencoded: boolean };
type Source = { source: Blob; edited: Blob | null };

async function toJpeg(blob: Blob, maxSide: number | null, quality: number): Promise<Blob> {
  const bmp = await createImageBitmap(blob, { imageOrientation: "from-image" });
  const k = maxSide ? Math.min(1, maxSide / Math.max(bmp.width, bmp.height)) : 1;
  const w = Math.max(1, Math.round(bmp.width * k)), h = Math.max(1, Math.round(bmp.height * k));
  const c = document.createElement("canvas");
  c.width = w; c.height = h;
  const ctx = c.getContext("2d");
  if (!ctx) { bmp.close(); throw new Error("This phone could not prepare the photo."); }
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(bmp, 0, 0, w, h);
  bmp.close();
  return new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error("This phone could not prepare the photo."))), "image/jpeg", quality));
}

const SENDABLE = /^image\/(jpeg|png)$/;
const ext = (b: Blob) => (b.type === "image/png" ? "png" : "jpg");

export async function prepare(photos: Source[]): Promise<Prepared[]> {
  const out: Prepared[] = [];
  for (const [i, p] of photos.entries()) {
    if (p.edited) out.push({ blob: p.edited, name: `photo-${i + 1}.jpg`, reencoded: false });          // the person's own crop or rotation
    else if (SENDABLE.test(p.source.type)) out.push({ blob: p.source, name: `photo-${i + 1}.${ext(p.source)}`, reencoded: false });
    else out.push({ blob: await toJpeg(p.source, null, 0.92), name: `photo-${i + 1}.jpg`, reencoded: true });
  }
  const total = () => out.reduce((n, p) => n + p.blob.size, 0);
  for (const [maxSide, q] of [[4096, 0.9], [3072, 0.85], [2048, 0.8]] as const) {
    if (total() <= UPLOAD_BUDGET) break;
    for (const [i, p] of out.entries()) {
      if (photos[i].edited || p.blob.size <= UPLOAD_BUDGET / Math.max(out.length, 1)) continue;
      out[i] = { blob: await toJpeg(photos[i].source, maxSide, q), name: `photo-${i + 1}.jpg`, reencoded: true };
    }
  }
  if (total() > UPLOAD_BUDGET) throw new Error("These photos are too large to send together. Please send fewer photos, or retake them.");
  return out;
}
