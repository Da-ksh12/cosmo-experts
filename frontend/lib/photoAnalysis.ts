"use client";

import { useCallback, useEffect, useState } from "react";
import { api, ApiError, post, uploadWithProgress } from "@/lib/api";
import { Geometry, validGeometry } from "@/lib/viewerMath";

/** The photo-analysis contract (v1), mirrored from backend/app/photo_analysis/schema.py (documented in docs/PHOTO_ANALYSIS.md).
 *  Every coordinate is a fraction (0 to 1) of the stored photo AS DISPLAYED (its EXIF orientation already applied), origin top-left, y down. Nothing in here is generated on the phone:
 *  the app only renders what the backend measured, and it refuses (drops) any geometry that does not validate rather than repairing it. */
export type Tone = "ok" | "attention" | "info";
export type Status = "queued" | "processing" | "completed" | "uncertain" | "rejected" | "failed" | "cancelled";
export const TERMINAL: Status[] = ["completed", "uncertain", "rejected", "failed", "cancelled"];
export type Intent = "measurements" | "observation" | "better_photo" | "what_next" | "summary" | "compare" | "capabilities" | "care";

export type Evidence = { text: string; metric?: string | null; value?: number | string | null; threshold?: string | null };
export type Observation = {
  id: string; kind: "photo_quality" | "framing" | "face_region" | "skin_feature"; group: "observe" | "area"; label: string; tone: Tone; summary: string;
  evidence: Evidence[]; limitations: string[]; confidence: { value: number; calibrated: boolean; basis: string } | null; geometry: Geometry | null; provenance: number; validated: boolean;
};
export type NextStep = { id: string; kind: "retake" | "add_photo" | "clinician" | "summary" | "reports" | "info"; label: string; detail: string; route: string | null };
export type Question = { id: string; text: string; intent: Intent; observation_id: string | null };
/** Contract 1.1 (additive; older servers do not send it): the distinctions a person needs, each from a real source. `may_explain` is filled only from an approved model's findings and is empty today. */
export type Guidance = {
  basis: "photo_quality_only" | "model_findings"; visible: string[]; may_explain: string[]; cannot_establish: string[]; history: string[];
  clinician_advice: { recommended: boolean; level: "routine" | "prompt" | "urgent" | null; reason: string };
  knowledge: { topic: string; article_id: string; title: string; tier: "approved" | "reference"; ask?: string }[];
};
export type Analysis = {
  api_version: "photo-analysis/1"; contract_minor?: number; guidance?: Guidance | null; id: string; conversation_id: string | null; message_id: string | null; index: number; status: Status; stage: string | null; error: string | null;
  created_at: number; finished_at: number | null;
  image: { id: string | null; kept: boolean; url: string | null; thumb_url: string | null; width: number | null; height: number | null; region: string; region_label: string; taken_at: number | null } | null;
  transform: { width: number; height: number; exif_orientation: number | null; server_modified: false } | null;
  outcome: { route: string; verdict: "accepted" | "uncertain" | "rejected" | null; reason: string | null; headline: string; message: string; clinician_review: boolean } | null;
  provenance: { capability: string; version: string; kind: "rule_based" | "model"; approval: string; validated: boolean; note: string }[];
  observations: Observation[];
  sections: { observe: string[]; areas: string[]; meaning: string[]; cannot: string[]; next_steps: NextStep[]; questions: Question[] };
  capabilities: { task: string; name: string; available: boolean; status: string; note: string }[];
  clinician_review: { status: "pending" | "reviewed" | "not_applicable"; message: string } | null;
  disclaimers: string[];
};

const arr = <T,>(v: unknown): T[] => (Array.isArray(v) ? (v as T[]) : []);

let nonceSeq = 0;
/** A number that is different every time: the "do it again" signal for the viewer (asking to view the same area twice must zoom twice). */
export const nextNonce = () => ++nonceSeq;

/** The server validates every geometry; the phone checks again and DROPS a shape that fails (the observation stays, as text, without a marker). */
export function parseAnalysis(raw: unknown): Analysis {
  const a = raw as Analysis;
  if (!a || typeof a !== "object" || a.api_version !== "photo-analysis/1" || typeof a.id !== "string") throw new ApiError(0, "This assessment is in a format this app does not understand. Please update the app.");
  const s: Partial<Analysis["sections"]> = a.sections ?? {};
  return {
    ...a,
    observations: arr<Observation>(a.observations).map((o) => ({ ...o, evidence: arr(o.evidence), limitations: arr(o.limitations), geometry: o.geometry && validGeometry(o.geometry) ? o.geometry : null })),
    sections: { observe: arr(s.observe), areas: arr(s.areas), meaning: arr(s.meaning), cannot: arr(s.cannot), next_steps: arr(s.next_steps), questions: arr(s.questions) },
    provenance: arr(a.provenance), capabilities: arr(a.capabilities), disclaimers: arr(a.disclaimers),
    guidance: a.guidance && typeof a.guidance === "object" && a.guidance.clinician_advice ? { ...a.guidance, visible: arr(a.guidance.visible), may_explain: arr(a.guidance.may_explain), cannot_establish: arr(a.guidance.cannot_establish),
      history: arr(a.guidance.history), knowledge: arr(a.guidance.knowledge) } : null,
  };
}

export type Submitted = { conversation_id: string; message_id: string; analyses: Analysis[]; idempotent_replay: boolean };

export async function submitPhotos(region: string, photos: { blob: Blob; name: string }[], opts: { signal?: AbortSignal; onProgress?: (f: number) => void; idempotencyKey?: string; conversationId?: string } = {}): Promise<Submitted> {
  const fd = new FormData();
  fd.append("region", region);
  if (opts.conversationId) fd.append("conversation_id", opts.conversationId);
  photos.forEach((p) => fd.append("files", p.blob, p.name));
  const r = await uploadWithProgress<{ conversation_id: string; message_id: string; analyses: unknown[]; idempotent_replay: boolean }>("/api/v1/photo-analyses", fd, opts.onProgress ?? (() => {}),
    { signal: opts.signal, timeoutMs: 120_000, headers: opts.idempotencyKey ? { "Idempotency-Key": opts.idempotencyKey } : undefined });
  return { ...r, analyses: r.analyses.map(parseAnalysis) };
}

export const getAnalysis = async (id: string) => parseAnalysis(await api<unknown>(`/api/v1/photo-analyses/${encodeURIComponent(id)}`));
export const cancelAnalysis = async (id: string) => parseAnalysis(await post<unknown>(`/api/v1/photo-analyses/${encodeURIComponent(id)}/cancel`));

/** The real, current state of one analysis, polled until it is final. A dropped connection is retried with a growing pause (and reported); a missing analysis stops the polling. */
export function useAnalysis(id: string | null) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [error, setError] = useState("");
  const [gone, setGone] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!id) return;
    let live = true, timer: ReturnType<typeof setTimeout> | undefined, fails = 0;
    const poll = async () => {
      try {
        const a = await getAnalysis(id);
        if (!live) return;
        fails = 0; setError(""); setAnalysis(a);
        if (!TERMINAL.includes(a.status)) timer = setTimeout(poll, a.status === "queued" ? 800 : 500);
      } catch (e) {
        if (!live) return;
        if (e instanceof ApiError && e.status === 404) { setGone(true); setError(e.message); return; }
        fails += 1; setError(e instanceof Error ? e.message : "We couldn't reach the server.");
        timer = setTimeout(poll, Math.min(10_000, 1000 * 2 ** Math.min(fails, 4)));
      }
    };
    void poll();
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, [id, tick]);
  const refresh = useCallback(() => { setError(""); setTick((n) => n + 1); }, []);
  return { analysis, error, gone, refresh };
}
