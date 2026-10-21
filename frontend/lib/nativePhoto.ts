"use client";

import { IS_APP } from "@/lib/config";

/** Photos on the phone.
 *  In the Android app, "Take a photo" opens the phone's OWN camera app and "Upload from gallery" opens Android's system photo picker, both through the Capacitor Camera plugin. Neither needs a
 *  camera or storage permission in this app (the camera app has its own; the photo picker hands over only what the person chooses). On the web the same two buttons use the browser's camera
 *  and file chooser (see app/photo/page.tsx). Nothing here sends anything: a photo is only held in memory until the person confirms. */

export const MAX_PHOTOS = 4;
export const MAX_SOURCE_BYTES = 25 * 1024 * 1024;        // the editor re-exports a JPEG of at most 2048 px (a few hundred KB), so this only guards phones against decoding something enormous
export const NATIVE_PHOTOS = IS_APP;

export type Picked = { kind: "photos"; files: File[] } | { kind: "cancelled" } | { kind: "error"; message: string };

type PluginError = { code?: string; message?: string };

/** The plugin rejects with a code (CameraErrorCode). Cancelling is not an error; everything else becomes one plain sentence that says what to do next. */
function fromError(e: unknown, what: "camera" | "gallery"): Picked {
  const { code = "", message = "" } = (e ?? {}) as PluginError;
  if (["OS-PLUG-CAMR-0006", "OS-PLUG-CAMR-0020", "OS-PLUG-CAMR-0013"].includes(code) || /cancel/i.test(message)) return { kind: "cancelled" };
  if (code === "OS-PLUG-CAMR-0003") return { kind: "error", message: "Camera access was denied. You can allow it in your phone's Settings, or choose a photo from your gallery instead." };
  if (code === "OS-PLUG-CAMR-0005") return { kind: "error", message: "Access to your photos was denied. You can allow it in your phone's Settings, or take a photo instead." };
  if (code === "OS-PLUG-CAMR-0007") return { kind: "error", message: "This phone has no camera we can open. Please choose a photo from your gallery instead." };
  return { kind: "error", message: what === "camera" ? "We couldn't take the photo. Please try again, or choose one from your gallery." : "We couldn't open your gallery. Please try again, or take a photo instead." };
}

async function toFile(webPath: string, name: string, type: string): Promise<File> {
  const res = await fetch(webPath);
  if (!res.ok) throw new Error("The photo could not be read.");
  const blob = await res.blob();
  return new File([blob], name, { type: blob.type || type });
}

/** Opens the phone's camera app. Resolves with the photo, or "cancelled" when the person backs out. */
export async function takeNativePhoto(): Promise<Picked> {
  try {
    const { Camera, EncodingType } = await import("@capacitor/camera");
    const r = await Camera.takePhoto({ quality: 90, correctOrientation: true, encodingType: EncodingType.JPEG, saveToGallery: false, editable: "no" });
    if (!r.webPath) return { kind: "error", message: "The camera did not return a photo. Please try again." };
    return { kind: "photos", files: [await toFile(r.webPath, `camera-${Date.now()}.jpg`, "image/jpeg")] };
  } catch (e) { return fromError(e, "camera"); }
}

/** Opens Android's photo picker. `limit` is how many more photos can be added (the picker enforces it on Android 13 and later; the result is trimmed on older versions). */
export async function chooseNativePhotos(limit: number): Promise<Picked> {
  try {
    const { Camera, MediaTypeSelection } = await import("@capacitor/camera");
    const r = await Camera.chooseFromGallery({ mediaType: MediaTypeSelection.Photo, allowMultipleSelection: limit > 1, limit, quality: 90, correctOrientation: true, includeMetadata: true, editable: "no" });
    const picked = (r.results ?? []).filter((m) => m.webPath).slice(0, limit);
    if (!picked.length) return { kind: "cancelled" };
    const files: File[] = [];
    for (const [i, m] of picked.entries()) {
      const fmt = (m.metadata?.format ?? "jpeg").toLowerCase().replace("jpg", "jpeg");
      files.push(await toFile(m.webPath!, `gallery-${Date.now()}-${i + 1}.${fmt === "jpeg" ? "jpg" : fmt}`, `image/${fmt}`));
    }
    return { kind: "photos", files };
  } catch (e) { return fromError(e, "gallery"); }
}

/** Client-side checks before a photo goes anywhere: it is an image, not empty, not enormous, and it can actually be decoded. The server checks again (type, size, quality): this only
 *  gives the person an immediate, specific message. Returns "" when the photo is fine. */
export async function photoProblem(file: Blob, label = "This photo"): Promise<string> {
  if (!file.size) return `${label} is empty. Please choose another photo.`;
  if (file.size > MAX_SOURCE_BYTES) return `${label} is larger than ${MAX_SOURCE_BYTES / 1024 / 1024} MB. Please choose a smaller photo.`;
  if (file.type && !file.type.startsWith("image/")) return `${label} is not a photo. Please choose a JPG or PNG image.`;
  try {
    const small = await createImageBitmap(file, { resizeWidth: 32, resizeHeight: 32, resizeQuality: "low" });     // a scaled decode: proves the data is a readable image without a full-size decode
    small.close();
    return "";
  } catch {
    return `We couldn't read ${label.toLowerCase()}. Its format may not be supported: please take a new photo or choose a JPG or PNG.`;
  }
}

/** Runs photoProblem over several files; the readable ones are returned with the first problem found (so one bad file does not lose the good ones). */
export async function screenPhotos(files: File[]): Promise<{ good: File[]; problem: string }> {
  const good: File[] = [];
  let problem = "";
  for (const f of files) {
    const p = await photoProblem(f);
    if (p) problem ||= p; else good.push(f);
  }
  return { good, problem };
}
