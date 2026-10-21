/** Build target and server address. One source tree, two builds:
 *  - web (default): the pages call the API on their own origin and the Next.js proxy forwards it (next.config.ts). Nothing changes.
 *  - android (NEXT_PUBLIC_TARGET=android): a static bundle inside the Capacitor app. The screens, styles and routing are inside the APK, so the app opens with no server running anywhere.
 *    Data, sign-in and AI answers come from ONE explicit https server address.
 *
 * The address can be set at build time (NEXT_PUBLIC_API_BASE, from COSMO_API_BASE or android.config.json) and changed at run time in Settings (stored on the phone). It is only an address:
 * no secret, credential or key is ever part of the app.
 *
 * A production build NEVER uses a development address. Plain http://, localhost, loopback and private-network addresses are refused at build time (scripts/build-android.mjs), refused when typed
 * in the app, and ignored (and erased) if one is found in the phone's storage. Only a build made with --dev-server (NEXT_PUBLIC_ALLOW_DEV_HTTP=1) accepts them. */

export const IS_APP = process.env.NEXT_PUBLIC_TARGET === "android";
export const APP_VERSION = process.env.NEXT_PUBLIC_APP_VERSION ?? "web";
export const ALLOW_DEV_HTTP = IS_APP && process.env.NEXT_PUBLIC_ALLOW_DEV_HTTP === "1";
const STORE_KEY = "cosmo.apiBase";
const CHANGED = "cosmo:apibase";

/** Hosts that only exist on a developer's own computer or network. */
const DEV_HOST = /^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[?::1\]?|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|169\.254\.\d+\.\d+|[^.]+\.local|[^.]+\.internal|[^.]+\.lan)$/i;
export const isDevHost = (hostname: string) => DEV_HOST.test(hostname);

/** An address is usable only if it is https, or (development builds only) plain http on a development host. */
function usable(base: string): boolean {
  try {
    const u = new URL(base);
    if (u.protocol === "https:") return !isDevHost(u.hostname) || ALLOW_DEV_HTTP;
    return u.protocol === "http:" && ALLOW_DEV_HTTP && isDevHost(u.hostname);
  } catch { return false; }
}

const BUILD_API_BASE = (() => {
  const b = (process.env.NEXT_PUBLIC_API_BASE ?? "").trim().replace(/\/+$/, "");
  return b && usable(b) ? b : "";                  // a development address baked into a production build is dropped here as a second line of defence (the build script already refuses it)
})();

function read(): string {
  try { return localStorage.getItem(STORE_KEY) ?? ""; } catch { return ""; }
}

/** The server the app talks to: "" on the web (same origin), otherwise the saved address, else the build-time address, else nothing: there is no silent fallback to anything else. */
export function getApiBase(): string {
  if (!IS_APP) return "";
  const saved = read();
  if (saved && !usable(saved)) {                    // e.g. an old development address left on the phone by an earlier build
    try { localStorage.removeItem(STORE_KEY); } catch { /* ignore */ }
    return BUILD_API_BASE;
  }
  return saved || BUILD_API_BASE;
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

/** Check an address typed by a person. Production builds accept https:// addresses of public servers only. */
export function parseServerUrl(raw: string): { ok: true; base: string } | { ok: false; error: string } {
  const text = raw.trim();
  if (!text) return { ok: false, error: "Enter the server address, for example https://api.example.com" };
  let u: URL;
  try { u = new URL(/^[a-z][a-z0-9+.-]*:\/\//i.test(text) ? text : `https://${text}`); } catch { return { ok: false, error: "That does not look like a web address." }; }
  if (u.protocol !== "https:" && u.protocol !== "http:") return { ok: false, error: "The address must start with https://" };
  if (u.username || u.password) return { ok: false, error: "Do not put a user name or password in the address." };
  if (u.protocol === "http:" && !ALLOW_DEV_HTTP) return { ok: false, error: "The server must use https://. Plain http:// is not allowed in this app." };
  if (u.protocol === "http:" && !isDevHost(u.hostname)) return { ok: false, error: "Servers on the internet must use https://. Plain http:// is only allowed for a computer on your own network." };
  if (isDevHost(u.hostname) && !ALLOW_DEV_HTTP) return { ok: false, error: "That address belongs to a computer on a local network or to this phone itself. Enter the address of your clinic's online server." };
  return { ok: true, base: `${u.protocol}//${u.host}` };
}

/** Is a server reachable at `base`? Uses the public liveness endpoint and says what happened, never a guess. */
export async function checkServer(base: string, timeoutMs = 8000): Promise<{ ok: boolean; message: string }> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(`${base}/api/v1/health/live`, { signal: ctl.signal, cache: "no-store" });
    if (!res.ok) return { ok: false, message: `The server answered with an error (HTTP ${res.status}).` };
    return { ok: true, message: "Connected." };
  } catch (e) {
    const offline = typeof navigator !== "undefined" && navigator.onLine === false;
    return { ok: false, message: e instanceof DOMException && e.name === "AbortError" ? "The server did not answer in time."
      : offline ? "This phone has no internet connection."
      : "Could not connect. Check the address and your internet connection. If the server is running, it must also list this app as an allowed origin (see docs/ANDROID_APK.md)." };
  } finally { clearTimeout(t); }
}
