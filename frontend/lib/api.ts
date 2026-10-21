"use client";

import { apiUrl, IS_APP, serverLabel } from "@/lib/config";
import { connection } from "@/lib/connection";
import { isPreview, PREVIEW_MESSAGE, previewBody, setPreview } from "@/lib/preview";

// These two are local constants ON PURPOSE. process.env.NEXT_PUBLIC_* is replaced by a literal at build time, so the bundler folds them and, in a release build, removes everything behind them
// (the server dialog, preview mode, demo accounts). A flag imported from another module would not be folded. lib/config.ts defines the same flags for code that does not need to disappear.
const IS_RELEASE = process.env.NEXT_PUBLIC_TARGET === "android" && process.env.NEXT_PUBLIC_RELEASE === "1";
const DEV_TOOLS = process.env.NEXT_PUBLIC_TARGET === "android" && process.env.NEXT_PUBLIC_RELEASE !== "1";

export type User = { id: string; name: string; role: "patient" | "doctor" | "admin" };

const KEY = "cosmo.session";

type Session = { token: string; user: User };
const listeners = new Set<() => void>();
let memory: string | null = null; // fallback when sessionStorage is blocked

export function getRawSession(): string | null {
  try {
    return sessionStorage.getItem(KEY) ?? memory;
  } catch {
    return memory;
  }
}

export function subscribeSession(cb: () => void) {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
}

export function getSession(): Session | null {
  const raw = getRawSession();
  try {
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function setSession(s: Session | null) {
  memory = s ? JSON.stringify(s) : null;
  try {
    if (s) sessionStorage.setItem(KEY, memory!);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* storage blocked: the in-memory copy keeps the app working for this page load */
  }
  listeners.forEach((l) => l());
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public detail?: unknown) {
    super(message);
  }
}

function messageFrom(detail: unknown, fallback: string): string {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    const d = detail as { message: string; reasons?: string[] };
    return [d.message, ...(d.reasons ?? [])].join(" ");
  }
  return fallback;
}

const CSRF = { "X-Requested-With": "cosmo-web" };
const TIMEOUT_MS = 90_000;      // an answer from the assistant can take a while on a small server; beyond this the request is abandoned and the person is told so
const OFFLINE = () => IS_RELEASE ? "We can't reach Cosmo Experts. Please check your internet connection and try again."
  : IS_APP ? `We can't reach the server (${serverLabel()}). Check your internet connection, or the server address in Settings.` : "We can't reach the server right now. Please check your connection and try again.";

/** fetch() with a time limit that also honours a caller's own abort signal. A timeout is reported as a timeout, not as "offline". */
async function fetchTimed(url: string, init: RequestInit, ms = TIMEOUT_MS): Promise<Response> {
  const ctl = new AbortController();
  const outer = init.signal;
  const onAbort = () => ctl.abort(outer?.reason);
  if (outer?.aborted) ctl.abort(outer.reason); else outer?.addEventListener("abort", onAbort);
  const timer = setTimeout(() => ctl.abort(new DOMException("The server took too long to answer.", "TimeoutError")), ms);
  try { return await fetch(url, { ...init, signal: ctl.signal }); }
  finally { clearTimeout(timer); outer?.removeEventListener("abort", onAbort); }
}
let refreshing: Promise<boolean> | null = null;

/** Exchange the httpOnly refresh cookie for a new access token (Supabase mode). One call at a time: concurrent 401s share it. Never exposes the refresh token to script. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const res = await fetchTimed(apiUrl("/api/auth/refresh"), { method: "POST", headers: CSRF, credentials: IS_APP ? "omit" : "same-origin" }, 15_000);
      if (!res.ok) return false;
      const r = (await res.json()) as Session;
      setSession({ token: r.token, user: r.user });
      return true;
    } catch {
      return false;
    }
  })().finally(() => { refreshing = null; });
  return refreshing;
}

export async function signOutEverywhere(): Promise<void> {
  const s = getSession();
  const wasPreview = DEV_TOOLS && isPreview();
  if (DEV_TOOLS) setPreview(false);
  setSession(null);
  if (wasPreview) return;                                  // a preview has no server session to end
  try {
    await fetchTimed(apiUrl("/api/auth/logout"), { method: "POST", headers: { ...CSRF, ...(s ? { Authorization: `Bearer ${s.token}` } : {}) }, credentials: IS_APP ? "omit" : "same-origin" }, 15_000);
  } catch { /* offline: the local session is already gone */ }
}

async function request(path: string, init: RequestInit = {}, retried = false): Promise<Response> {
  if (DEV_TOOLS && isPreview()) {                          // developer-build preview mode: no network at all; empty neutral bodies or an honest refusal (lib/preview.ts)
    const body = previewBody(path, (init.method ?? "GET").toUpperCase());
    if (body === undefined) throw new ApiError(0, PREVIEW_MESSAGE);
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  }
  const s = getSession();
  const headers = new Headers(init.headers);
  if (s) headers.set("Authorization", `Bearer ${s.token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let res: Response;
  try {
    res = await fetchTimed(apiUrl(path), { ...init, headers });
  } catch (e) {
    if (e instanceof DOMException && e.name === "TimeoutError") { connection.markDown("timeout"); throw new ApiError(0, "The server took too long to answer. Please try again in a moment."); }
    if (e instanceof DOMException && e.name === "AbortError") throw new ApiError(0, "Cancelled.");
    connection.markDown("unreachable");
    throw new ApiError(0, OFFLINE());
  }
  connection.markUp();                                     // the server answered (even if with an error): it is reachable
  if (res.status === 401 && s) {
    if (!retried && !path.startsWith("/api/auth/") && (await refreshSession())) return request(path, init, true);
    setSession(null); // Shell observes the session store and redirects to /login
  }
  if (!res.ok) {
    let detail: unknown;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* non-JSON error */
    }
    throw new ApiError(res.status, messageFrom(detail, "Something went wrong. Please try again."), detail);
  }
  return res;
}

export async function api<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  return (await request(path, init)).json();
}

export const post = <T = unknown>(path: string, body?: unknown) =>
  api<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const patch = <T = unknown>(path: string, body: unknown) => api<T>(path, { method: "PATCH", body: JSON.stringify(body) });
export const put = <T = unknown>(path: string, body: unknown) => api<T>(path, { method: "PUT", body: JSON.stringify(body) });
export const del = <T = unknown>(path: string) => api<T>(path, { method: "DELETE" });

export async function blob(path: string, init?: RequestInit): Promise<Blob> {
  return (await request(path, init)).blob();
}

export function upload<T = unknown>(path: string, form: FormData) {
  return api<T>(path, { method: "POST", body: form });
}

export const STATUS_LABEL: Record<string, string> = {
  available: "Available",
  research_only: "Research only",
  clinician_review_required: "Clinician review required",
  unavailable: "Unavailable",
  approved_for_defined_use: "Approved for defined use",
};

export const CATEGORY_LABEL: Record<string, string> = {
  laboratory_report: "Lab Report", prescription: "Prescription", consultation_note: "Consultation Note", other: "Other",
};

export const DOC_TYPE_LABEL: Record<string, string> = {
  laboratory_report: "Laboratory report",
  prescription: "Prescription",
  histopathology_report: "Histopathology report",
  consultation_note: "Consultation note",
  treatment_history: "Treatment history",
  other_uncertain: "Other / uncertain",
};

/** Multipart upload with real progress (fetch cannot report upload progress). `signal` cancels the upload; `timeoutMs` bounds it (default 5 minutes). */
export function uploadWithProgress<T = unknown>(path: string, form: FormData, onProgress: (fraction: number) => void,
  opts: { signal?: AbortSignal; timeoutMs?: number; headers?: Record<string, string> } = {}): Promise<T> {
  return new Promise((resolve, reject) => {
    if (DEV_TOOLS && isPreview()) { reject(new ApiError(0, PREVIEW_MESSAGE)); return; }
    if (opts.signal?.aborted) { reject(new ApiError(0, "Cancelled.")); return; }
    const s = getSession();
    const xhr = new XMLHttpRequest();
    xhr.open("POST", apiUrl(path));
    xhr.timeout = opts.timeoutMs ?? 300_000;
    if (s) xhr.setRequestHeader("Authorization", `Bearer ${s.token}`);
    for (const [k, v] of Object.entries(opts.headers ?? {})) xhr.setRequestHeader(k, v);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onerror = () => { connection.markDown("unreachable"); reject(new ApiError(0, OFFLINE())); };
    xhr.ontimeout = () => { connection.markDown("timeout"); reject(new ApiError(0, "The upload took too long and was stopped. Please try again on a stronger connection.")); };
    xhr.onabort = () => reject(new ApiError(0, "Cancelled."));
    opts.signal?.addEventListener("abort", () => xhr.abort(), { once: true });
    xhr.onload = () => {
      connection.markUp();
      let body: { detail?: unknown } & Record<string, unknown> = {};
      try { body = JSON.parse(xhr.responseText); } catch { /* non-JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) { onProgress(1); resolve(body as T); }
      else reject(new ApiError(xhr.status, messageFrom(body.detail, "Something went wrong. Please try again."), body.detail));
    };
    xhr.send(form);
  });
}
