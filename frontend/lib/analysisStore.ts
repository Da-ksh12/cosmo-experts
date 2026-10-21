"use client";

import { useSyncExternalStore } from "react";
import { ApiError } from "@/lib/api";
import { submitPhotos } from "@/lib/photoAnalysis";
import type { Region } from "@/lib/photoFlow";

/** The photos that are on their way to the server, kept OUTSIDE React so that leaving the screen does not lose them: an upload that has started goes on, and when the person comes back the
 *  chat shows its real state. In memory only: a photo is never written to storage on the phone. Once the server has accepted the photos the session exists server-side (a conversation with
 *  its assessment card), and that, not this store, is what survives leaving the app.
 *
 *  A store entry holds the local copy of each photo, so that the assessment can show the person's own photo while it is processed and for a photo the server did not keep (data
 *  minimisation: an unusable photo is not stored, so there is nothing to download for it). */
export type PendingPhoto = { key: string; blob: Blob; name: string; url: string };
export type Pending = {
  id: string; region: Region; photos: PendingPhoto[];
  stage: "uploading" | "submitted" | "failed" | "cancelled";
  progress: number;                       // fraction of the bytes the server has received: a real measurement
  error: string; conversationId: string | null; analysisIds: string[]; startedAt: number;
};

const items = new Map<string, Pending>();
const listeners = new Set<() => void>();
const controllers = new Map<string, AbortController>();
const keys = new Map<string, string>();                   // idempotency key per entry: a retry after a dropped connection cannot create the session twice
const previews = new Map<string, string>();               // analysis id -> local photo (object URL)
let seq = 0;

const emit = () => listeners.forEach((l) => l());
const set = (id: string, patch: Partial<Pending>) => { const p = items.get(id); if (p) { items.set(id, { ...p, ...patch }); emit(); } };
const newKey = () => crypto.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(36).slice(2)}`;

async function run(id: string) {
  const p = items.get(id);
  if (!p) return;
  const ctl = new AbortController();
  controllers.set(id, ctl);
  if (!keys.has(id)) keys.set(id, newKey());
  set(id, { stage: "uploading", progress: 0, error: "" });
  try {
    const res = await submitPhotos(p.region, p.photos, { signal: ctl.signal, idempotencyKey: keys.get(id), onProgress: (f) => set(id, { progress: f }) });
    res.analyses.forEach((a, i) => { const url = p.photos[i]?.url; if (url) previews.set(a.id, url); });
    set(id, { stage: "submitted", progress: 1, conversationId: res.conversation_id, analysisIds: res.analyses.map((a) => a.id) });
  } catch (e) {
    const aborted = ctl.signal.aborted;
    if (aborted || !(e instanceof ApiError && e.status === 0)) keys.delete(id);       // only a dropped connection (outcome unknown) keeps the key; a cancel or a server answer starts fresh
    set(id, aborted ? { stage: "cancelled", error: "" } : { stage: "failed", error: e instanceof Error ? e.message : "We couldn't send your photo. Please try again." });
  } finally { controllers.delete(id); }
}

export const analysisStore = {
  subscribe(l: () => void) { listeners.add(l); return () => { listeners.delete(l); }; },
  get(id: string | null | undefined): Pending | null { return id ? items.get(id) ?? null : null; },
  /** Starts sending the photos (exactly as given: nothing is changed here) and returns the entry's id. */
  start(region: Region, photos: { blob: Blob; name: string }[]): string {
    const id = `local-${Date.now()}-${++seq}`;
    items.set(id, { id, region, photos: photos.map((p, i) => ({ key: `${id}-${i}`, blob: p.blob, name: p.name, url: URL.createObjectURL(p.blob) })), stage: "uploading", progress: 0, error: "", conversationId: null, analysisIds: [], startedAt: Date.now() });
    emit();
    void run(id);
    return id;
  },
  retry(id: string) { if (items.get(id)?.stage === "failed" || items.get(id)?.stage === "cancelled") void run(id); },
  cancel(id: string) { controllers.get(id)?.abort(); },
  /** Forget the entry. The local photo copies stay available to the cards that already know them (see previewFor). */
  dismiss(id: string) { items.delete(id); emit(); },
  /** The person's own copy of the photo behind an analysis, while this app session lasts. */
  previewFor(analysisId: string): string | null { return previews.get(analysisId) ?? null; },
  /** The entry whose photos produced this analysis, if it is still in memory (it holds the local copies, so a failed session can be sent again). */
  byAnalysis(analysisId: string): Pending | null { for (const p of items.values()) if (p.analysisIds.includes(analysisId)) return p; return null; },
  /** Sends the same photos again as a NEW session (used after the server reported that a session failed) and returns the new entry's id. */
  rerun(id: string): string | null { const p = items.get(id); return p ? analysisStore.start(p.region, p.photos.map(({ blob, name }) => ({ blob, name }))) : null; },
  /** The entry that is still on its way, if any (a person who left the chat and came back finds it). */
  active(): Pending | null { for (const p of [...items.values()].reverse()) if (p.stage === "uploading" || p.stage === "failed" || p.stage === "cancelled") return p; return null; },
  /** The newest entry the server accepted within the given time, if the app has been open since (so a person who left while the assessment was running finds the same conversation). */
  lastSubmitted(maxAgeMs: number): Pending | null { const now = Date.now(); for (const p of [...items.values()].reverse()) if (p.stage === "submitted" && p.conversationId && now - p.startedAt <= maxAgeMs) return p; return null; },
};

/** The live state of one entry (null when the app does not know it, for example after the app was restarted). */
export function usePending(id: string | null | undefined): Pending | null {
  return useSyncExternalStore(analysisStore.subscribe, () => analysisStore.get(id), () => null);
}
/** The entry that is still on its way, whichever it is. */
export function useActivePending(): Pending | null {
  return useSyncExternalStore(analysisStore.subscribe, analysisStore.active, () => null);
}
