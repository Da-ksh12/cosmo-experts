"use client";

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
let refreshing: Promise<boolean> | null = null;

/** Exchange the httpOnly refresh cookie for a new access token (Supabase mode). One call at a time: concurrent 401s share it. Never exposes the refresh token to script. */
export function refreshSession(): Promise<boolean> {
  refreshing ??= (async () => {
    try {
      const res = await fetch("/api/auth/refresh", { method: "POST", headers: CSRF, credentials: "same-origin" });
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
  setSession(null);
  try {
    await fetch("/api/auth/logout", { method: "POST", headers: { ...CSRF, ...(s ? { Authorization: `Bearer ${s.token}` } : {}) }, credentials: "same-origin" });
  } catch { /* offline: the local session is already gone */ }
}

async function request(path: string, init: RequestInit = {}, retried = false): Promise<Response> {
  const s = getSession();
  const headers = new Headers(init.headers);
  if (s) headers.set("Authorization", `Bearer ${s.token}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  let res: Response;
  try {
    res = await fetch(path, { ...init, headers });
  } catch {
    throw new ApiError(0, "We can't reach the server right now. Please check your connection and try again.");
  }
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

/** Multipart upload with real progress (fetch cannot report upload progress). */
export function uploadWithProgress<T = unknown>(path: string, form: FormData, onProgress: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const s = getSession();
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    if (s) xhr.setRequestHeader("Authorization", `Bearer ${s.token}`);
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onerror = () => reject(new ApiError(0, "We can't reach the server right now. Please check your connection and try again."));
    xhr.onload = () => {
      let body: { detail?: unknown } & Record<string, unknown> = {};
      try { body = JSON.parse(xhr.responseText); } catch { /* non-JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) { onProgress(1); resolve(body as T); }
      else reject(new ApiError(xhr.status, messageFrom(body.detail, "Something went wrong. Please try again."), body.detail));
    };
    xhr.send(form);
  });
}
