"use client";

import { api, ApiError, uploadWithProgress } from "@/lib/api";

export type JobStatus = "queued" | "processing" | "completed" | "uncertain" | "rejected" | "failed" | "cancelled";
export type Job<R = unknown> = {
  id: string; task: string; status: JobStatus; request_id: string; created_at: number; started_at: number | null; finished_at: number | null;
  queue_ms: number | null; processing_ms: number | null; result: R | null; error: string | null;
};
export const TERMINAL: JobStatus[] = ["completed", "uncertain", "rejected", "failed", "cancelled"];

/** Poll the real job until it is terminal. `onStatus` receives every actual state the backend reports (never a simulated one). */
export async function pollJob<R>(id: string, onStatus?: (j: Job<R>) => void, opts: { intervalMs?: number; timeoutMs?: number; signal?: AbortSignal } = {}): Promise<Job<R>> {
  const start = Date.now();
  let last = "";
  for (;;) {
    if (opts.signal?.aborted) throw new ApiError(0, "Cancelled.");
    const j = await api<Job<R>>(`/api/v1/jobs/${id}`);
    if (j.status !== last) { last = j.status; onStatus?.(j); }
    if (TERMINAL.includes(j.status)) return j;
    if (Date.now() - start > (opts.timeoutMs ?? 300_000)) throw new ApiError(0, "This is taking longer than expected. Please check back in a moment.");
    await new Promise((r) => setTimeout(r, opts.intervalMs ?? 400));
  }
}

/** Upload to a /api/v1 endpoint (202 + job) and wait for the real outcome. */
export async function runUploadJob<R>(path: string, form: FormData, handlers: { onUpload?: (fraction: number) => void; onStatus?: (j: Job<R>) => void } = {}): Promise<Job<R>> {
  const created = await uploadWithProgress<Job<R>>(path, form, handlers.onUpload ?? (() => {}));
  handlers.onStatus?.(created);
  return TERMINAL.includes(created.status) ? created : pollJob<R>(created.id, handlers.onStatus);
}

export const cancelJob = (id: string) => api(`/api/v1/jobs/${id}/cancel`, { method: "POST" });

export const STATUS_TEXT: Record<JobStatus, string> = {
  queued: "Waiting in line", processing: "Checking now", completed: "Done", uncertain: "Needs another look", rejected: "Not accepted", failed: "Could not finish", cancelled: "Cancelled",
};
