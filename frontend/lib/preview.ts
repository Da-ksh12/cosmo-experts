import { IS_APP } from "@/lib/config";

/** PREVIEW MODE (Android app only): lets a person look at the real screens when no server is available.
 *  It is explicitly a preview, never a simulation of care: every screen receives EMPTY lists and neutral status objects, never an example record, report, answer, result or prediction,
 *  and anything that would create, send, upload or analyse something fails with PREVIEW_MESSAGE. No network request is made. The flag lives in session storage, so it ends with the session. */
const KEY = "cosmo.preview";
let memory = false;

export function isPreview(): boolean {
  if (!IS_APP) return false;
  try { return sessionStorage.getItem(KEY) === "1" || memory; } catch { return memory; }
}

export function setPreview(on: boolean) {
  memory = on;
  try { if (on) sessionStorage.setItem(KEY, "1"); else sessionStorage.removeItem(KEY); } catch { /* storage blocked: the in-memory flag keeps this page load consistent */ }
}

export const PREVIEW_MESSAGE = "Preview mode: no server is connected, so this can't be done. Connect to your clinic's server in Settings to use it.";

const EMPTY_LISTS = [/^\/api\/history(\?.*)?$/, /^\/api\/documents(\/compare)?$/, /^\/api\/assistant\/suggestions$/, /^\/api\/conversations$/];

/** The body a screen gets for a request in preview mode, or undefined when the request cannot be served (the caller then fails it with PREVIEW_MESSAGE). */
export function previewBody(path: string, method: string): unknown | undefined {
  if (method === "POST" && path === "/api/conversations") return { id: "preview" };          // a conversation id only; sending a message still fails
  if (method !== "GET") return undefined;
  if (EMPTY_LISTS.some((re) => re.test(path))) return [];
  if (path === "/api/consent") return { enforced: false, required: false, accepted: true, available: false, notice: null };
  if (path === "/api/consent/history") return { events: [] };
  if (path === "/api/care-plan") return { plan: null, doctor: null, photos: [], documents: [], awaiting_review: false };
  if (path === "/api/notifications") return { unread: 0, items: [] };
  if (path === "/api/speech/status") return { stt: { available: false } };
  return undefined;
}
