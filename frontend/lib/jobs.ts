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

/** Upload to a /api/v1 endpoint (202 + job) and wait for the real outcome. `signal` cancels: an upload in flight is aborted, a job already queued is cancelled on the server.
 *  `idempotencyKey` makes a retry of the SAME upload safe: the server returns the job it already has instead of processing (and saving) the file twice. */
export async function runUploadJob<R>(path: string, form: FormData,
  handlers: { onUpload?: (fraction: number) => void; onStatus?: (j: Job<R>) => void; signal?: AbortSignal; uploadTimeoutMs?: number; idempotencyKey?: string } = {}): Promise<Job<R>> {
  const created = await uploadWithProgress<Job<R>>(path, form, handlers.onUpload ?? (() => {}),
    { signal: handlers.signal, timeoutMs: handlers.uploadTimeoutMs, headers: handlers.idempotencyKey ? { "Idempotency-Key": handlers.idempotencyKey } : undefined });
  handlers.onStatus?.(created);
  if (TERMINAL.includes(created.status)) return created;
  try { return await pollJob<R>(created.id, handlers.onStatus, { signal: handlers.signal }); }
  catch (e) {
    if (handlers.signal?.aborted) void cancelJob(created.id).catch(() => { /* the job finishes on its own if it cannot be cancelled */ });
    throw e;
  }
}

export const cancelJob = (id: string) => api(`/api/v1/jobs/${id}/cancel`, { method: "POST" });

export const STATUS_TEXT: Record<JobStatus, string> = {
  queued: "Waiting in line", processing: "Checking now", completed: "Done", uncertain: "Needs another look", rejected: "Not accepted", failed: "Could not finish", cancelled: "Cancelled",
};
