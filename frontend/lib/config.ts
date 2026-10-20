/** Build target and server address. One source tree, two builds:
 *  - web (default): the pages call the API on their own origin and the Next.js proxy forwards it (next.config.ts). Nothing changes.
 *  - android (NEXT_PUBLIC_TARGET=android): a static bundle inside the Capacitor app. There is no proxy, so the app calls an explicit server address.
 *
 * The address can be baked in at build time (NEXT_PUBLIC_API_BASE in frontend/.env.android) and changed at run time in Settings (stored on the phone). It is only an address: no secret,
 * credential or key is ever part of the app. Android itself refuses plain http:// except for the few development hosts listed in the debug build (see docs/ANDROID_APK.md). */

export const IS_APP = process.env.NEXT_PUBLIC_TARGET === "android";
export const APP_VERSION = process.env.NEXT_PUBLIC_APP_VERSION ?? "web";
const BUILD_API_BASE = (process.env.NEXT_PUBLIC_API_BASE ?? "").trim().replace(/\/+$/, "");
const STORE_KEY = "cosmo.apiBase";
const CHANGED = "cosmo:apibase";

function read(): string {
  try { return localStorage.getItem(STORE_KEY) ?? ""; } catch { return ""; }
}

/** The server the app talks to: "" on the web (same origin), otherwise the saved address, else the build-time default. */
export function getApiBase(): string {
  if (!IS_APP) return "";
  return read() || BUILD_API_BASE;
}

export function defaultApiBase(): string {
  return BUILD_API_BASE;
}

export function setApiBase(base: string | null) {
  try {
    if (base) localStorage.setItem(STORE_KEY, base);
    else localStorage.removeItem(STORE_KEY);
  } catch { /* storage blocked: the build-time address stays in use */ }
  if (typeof window !== "undefined") window.dispatchEvent(new Event(CHANGED));
}

export function onApiBaseChange(cb: () => void): () => void {
  window.addEventListener(CHANGED, cb);
  return () => window.removeEventListener(CHANGED, cb);
}

/** Absolute URL for an API path ("/api/..."). On the web, and for anything that is not an API path, the path is returned unchanged. */
export function apiUrl(path: string): string {
  return IS_APP && path.startsWith("/api/") ? getApiBase() + path : path;
}

export function serverLabel(): string {
  const b = getApiBase();
  if (!b) return IS_APP ? "no server set" : "this site";
  try { return new URL(b).host; } catch { return b; }
}

const PRIVATE_HOST = /^(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|[^.]+\.local)$/i;

/** Check an address typed by a person. Public servers must use https; plain http is accepted here only for a computer on the person's own network (Android still decides whether it may connect). */
export function parseServerUrl(raw: string): { ok: true; base: string } | { ok: false; error: string } {
  const text = raw.trim();
  if (!text) return { ok: false, error: "Enter the server address, for example https://api.example.com" };
  let u: URL;
  try { u = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `https://${text}`); } catch { return { ok: false, error: "That does not look like a web address." }; }
  if (u.protocol !== "https:" && u.protocol !== "http:") return { ok: false, error: "The address must start with https:// (or http:// for a computer on your own network)." };
  if (u.username || u.password) return { ok: false, error: "Do not put a user name or password in the address." };
  if (u.protocol === "http:" && !PRIVATE_HOST.test(u.hostname)) return { ok: false, error: "Servers on the internet must use https://. Plain http:// is only allowed for a computer on your own network." };
  return { ok: true, base: `${u.protocol}//${u.host}` };
}

/** Is a server reachable at `base` (or on this origin when base is empty)? Uses the public liveness endpoint and says what happened, never a guess. */
export async function checkServer(base: string, timeoutMs = 8000): Promise<{ ok: boolean; message: string }> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(`${base}/api/v1/health/live`, { signal: ctl.signal, cache: "no-store" });
    if (!res.ok) return { ok: false, message: `The server answered with an error (HTTP ${res.status}).` };
    return { ok: true, message: "Connected." };
  } catch (e) {
    const cleartext = base.startsWith("http://");
    return { ok: false, message: e instanceof DOMException && e.name === "AbortError" ? "The server did not answer in time."
      : `Could not connect. Check the address and your connection.${cleartext ? " Android only allows http:// to the development hosts listed when this app was built; an https:// address always works." : ""} If the server is running, it must also list this app as an allowed origin (see docs/ANDROID_APK.md).` };
  } finally { clearTimeout(t); }
}
