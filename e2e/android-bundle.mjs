// Checks the STATIC bundle that goes into the Android app (frontend/out-android) in a phone-sized Chromium, against a real backend. Capacitor serves the same files from
// http://localhost inside the Android WebView, so this exercises the same code, the same cross-origin calls (CORS, bearer token, uploads) and the same screens; it does NOT
// exercise Android itself (no WebView, no permissions dialogs, no APK install): that needs a device or an emulator.
//
// Prerequisites: node scripts/build-android.mjs --no-local   (creates frontend/out-android with no server chosen; run from frontend/)
//                a backend on COSMO_API (default http://127.0.0.1:8000) started with COSMO_CORS=http://localhost:4173 and demo accounts whose password is in
//                COSMO_TEST_PASSWORD_FILE (or COSMO_TEST_PASSWORD)
// Run:           node e2e/android-bundle.mjs
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { reporter } from "./lib.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(here, "..", "frontend", "out-android");
const PORT = Number(process.env.COSMO_BUNDLE_PORT ?? 4173);
const API = process.env.COSMO_API ?? "http://127.0.0.1:8000";
const PASSWORD = process.env.COSMO_TEST_PASSWORD ?? fs.readFileSync(process.env.COSMO_TEST_PASSWORD_FILE ?? "", "utf8").trim();
const SHOTS = path.resolve(here, "..", "docs", "runs", "android_screens");
fs.mkdirSync(SHOTS, { recursive: true });
const r = reporter("android-bundle");

// ---- a static file server for the bundle, with the same directory-index behaviour as Capacitor's local server
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2", ".txt": "text/plain" };
const server = http.createServer((req, res) => {
  let p = decodeURIComponent(new URL(req.url, "http://x").pathname);
  let file = path.join(WEB, p);
  if (!file.startsWith(WEB)) { res.writeHead(403).end(); return; }
  if (fs.existsSync(file) && fs.statSync(file).isDirectory()) file = path.join(file, "index.html");
  else if (!fs.existsSync(file) && fs.existsSync(path.join(file, "index.html"))) file = path.join(file, "index.html");
  if (!fs.existsSync(file)) { res.writeHead(404, { "Content-Type": TYPES[".html"] }).end(fs.readFileSync(path.join(WEB, "404.html"))); return; }
  res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] ?? "application/octet-stream" }).end(fs.readFileSync(file));
});
await new Promise((ok) => server.listen(PORT, "127.0.0.1", ok));
const ORIGIN = `http://localhost:${PORT}`;

const browser = await chromium.launch({ args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });
const ctx = await browser.newContext({
  viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, permissions: ["camera", "microphone"],
  userAgent: "Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
});
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
const shot = (name) => page.screenshot({ path: path.join(SHOTS, `${name}.png`) });
const text = async (sel) => (await page.locator(sel).first().innerText({ timeout: 4000 }).catch(() => ""));

try {
  // 1. first start: no server configured -> the app asks for one (it never pretends to be signed in or connected)
  await page.goto(ORIGIN + "/");
  await page.waitForURL(/\/login\/?$/, { timeout: 20000 });
  await page.getByRole("heading", { name: "Connect to your clinic" }).waitFor({ timeout: 10000 });
  r.ok(true, "first start with no server: shows 'Connect to your clinic' instead of a login form");
  r.ok((await page.locator("select").count()) === 0, "no demo account list is offered before a server is chosen");
  await shot("01-connect");

  // 2. server settings: an internet http address is refused, a private-network one is accepted and tested for real
  await page.getByRole("button", { name: "Set the server address" }).click();
  await page.locator("#server-url").fill("http://example.com");
  await page.getByRole("button", { name: "Save" }).click();
  r.ok(/https/i.test(await text('[role="status"]')), "http://example.com (a server on the internet) is refused: https is required");
  await page.locator("#server-url").fill("not a url ::");
  await page.getByRole("button", { name: "Test connection" }).click();
  r.ok(/web address|https/i.test(await text('[role="status"]')), "a malformed address is refused with a plain message");
  await page.locator("#server-url").fill(API);
  await page.getByRole("button", { name: "Test connection" }).click();
  await page.getByText("Connected.").waitFor({ timeout: 15000 });
  r.ok(true, "the server address is tested against the real backend (health endpoint, CORS allowed)");
  await shot("02-server-dialog");
  await page.getByRole("button", { name: "Save" }).click();

  // 3. sign in with a demo account (the administrator console is not offered in the app)
  await page.locator("select").waitFor({ timeout: 15000 });
  const offered = await page.locator("select option").allTextContents();
  r.ok(offered.length >= 2 && !offered.some((t) => /admin/i.test(t)), "the app offers patient and doctor sign-in, not the administrator console", offered);
  await page.selectOption("select", "patient1");
  await page.fill("input[type=password]", "wrong-password");
  await page.click("button[type=submit]");
  await page.getByText(/incorrect|invalid|could not sign in|wrong/i).first().waitFor({ timeout: 15000 });
  r.ok(true, "a wrong password is rejected by the real backend with a visible error");
  await shot("03-login-error");
  await page.fill("input[type=password]", PASSWORD);
  await page.click("button[type=submit]");
  await page.waitForURL((u) => u.pathname === "/" || u.pathname === "", { timeout: 30000 });
  await page.locator('nav[aria-label="Primary"]').waitFor({ timeout: 20000 });
  r.ok(true, "signed in through the bundle against the real backend; the patient bottom navigation is shown");
  await page.waitForTimeout(1500);
  await shot("04-home");

  // 4. the AI conversation: suggested starters, a real answer, an honest failure, a retry
  await page.getByRole("link", { name: "Ask AI" }).click();
  await page.waitForURL(/\/ask\/?/, { timeout: 15000 });
  await page.locator("#q").waitFor({ timeout: 15000 });
  r.ok(await page.getByRole("button", { name: "Explain my latest report in simple words" }).isVisible().catch(() => false) || (await page.locator("#suggested button").count()) > 0, "suggested questions are shown");
  await shot("05-ask-empty");
  const asked = page.waitForResponse((x) => /\/api\/conversations\/[^/]+\/messages$/.test(x.url()) && x.request().method() === "POST", { timeout: 180000 });
  await page.locator("#q").fill("What does it mean when a lab value is outside the printed range?");
  await page.getByRole("button", { name: "Send question" }).click();
  const resp = await asked;
  r.ok(resp.status() === 200, "the question went to the real backend (POST /api/conversations/{id}/messages -> 200)", resp.status());
  await page.locator('section[aria-label="Conversation"] .card').first().waitFor({ timeout: 60000 });
  const answer = (await page.locator('section[aria-label="Conversation"] .card p').first().innerText()).trim();
  r.ok(answer.length > 20, "an assistant answer (or an honest abstention) from the backend is displayed", answer.slice(0, 160));
  await page.waitForTimeout(500);
  await shot("06-ask-answer");

  await page.route("**/api/conversations/*/messages", (route) => route.abort("failed"));
  await page.locator("#q").fill("Can you summarise my reports?");
  await page.getByRole("button", { name: "Send question" }).click();
  await page.getByText(/Not sent\./).waitFor({ timeout: 20000 });
  const bubblesAfterFail = await page.locator('section[aria-label="Conversation"] .card').count();
  r.ok(bubblesAfterFail === 1, "when the backend call fails the question is marked 'Not sent' and no answer is invented", bubblesAfterFail);
  await shot("07-ask-failed");
  await page.unroute("**/api/conversations/*/messages");
  const retried = page.waitForResponse((x) => /\/messages$/.test(x.url()) && x.request().method() === "POST", { timeout: 180000 });
  await page.getByRole("button", { name: "Try again" }).click();
  r.ok((await retried).status() === 200, "'Try again' resends the question and the backend answers");
  await page.waitForFunction(() => document.querySelectorAll('section[aria-label="Conversation"] .card').length >= 2, null, { timeout: 60000 });

  // 5. other patient screens render from real data and offer their empty states
  for (const [name, link, expect] of [["reports", "Reports", /report/i], ["history", "History", /history|timeline|photo/i]]) {
    await page.getByRole("link", { name: link }).first().click();
    await page.waitForURL(new RegExp(`/${name}/?$`), { timeout: 15000 });
    await page.waitForLoadState("networkidle").catch(() => {});
    r.ok(expect.test(await page.locator("main").innerText()), `${name} screen loads from the backend`);
    await shot(`08-${name}`);
  }
  await page.goto(ORIGIN + "/reports/view/?id=does-not-exist");
  await page.waitForLoadState("networkidle").catch(() => {});
  r.ok(!errors.length && (await page.locator("main").innerText()).length > 0, "the single report page (/reports/view/?id=...) renders and reports an unknown id without crashing");
  await shot("09-report-unknown");

  // 6. settings: profile, server connection, consent, sign out
  await page.goto(ORIGIN + "/settings/");
  await page.getByRole("heading", { name: "Settings" }).waitFor({ timeout: 15000 });
  await page.getByText("Connected", { exact: true }).waitFor({ timeout: 15000 });
  r.ok((await page.locator("main").innerText()).includes("127.0.0.1:8000"), "settings shows the server host and a live 'Connected' status");
  await shot("10-settings");

  // 7. no connection: the banner appears and a send fails visibly
  await page.goto(ORIGIN + "/ask/");
  await page.locator("#q").waitFor({ timeout: 20000 });
  await ctx.setOffline(true);
  await page.getByText(/You are offline/).waitFor({ timeout: 10000 });
  await page.locator("#q").fill("Is anyone there?");
  await page.getByRole("button", { name: "Send question" }).click();
  await page.getByText(/Not sent\./).waitFor({ timeout: 20000 });
  r.ok(/can't reach the server \(127\.0\.0\.1:8000\)/i.test(await page.locator("main").innerText()), "offline: a banner says so and the failed send names the server it could not reach");
  await shot("11-offline");
  await ctx.setOffline(false);

  // 8. a wrong server address: the sign-in screen says it cannot reach it, and cannot be submitted
  await page.evaluate(() => { sessionStorage.clear(); localStorage.setItem("cosmo.apiBase", "http://127.0.0.1:9"); });
  await page.goto(ORIGIN + "/login/");
  await page.getByText(/can't reach the server/i).first().waitFor({ timeout: 30000 });
  r.ok(await page.locator("button[type=submit]").isDisabled(), "an unreachable server is reported on the sign-in screen and sign-in is disabled");
  await shot("12-unreachable");

  // 9. preview mode (no server at all): the real screens with nothing invented, clearly labelled, and not a single API request
  const ctx2 = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const p2 = await ctx2.newPage();
  const apiCalls = [];
  const errors2 = [];
  p2.on("request", (q) => { if (/\/api\//.test(q.url())) apiCalls.push(q.url()); });
  p2.on("pageerror", (e) => errors2.push(String(e).slice(0, 300)));
  await p2.goto(ORIGIN + "/");
  await p2.getByRole("button", { name: "Look around in preview mode" }).click();
  await p2.getByText(/PREVIEW · no server is connected/).waitFor({ timeout: 15000 });
  await p2.locator('nav[aria-label="Primary"]').waitFor({ timeout: 15000 });
  r.ok(true, "preview mode: the real home screen opens with a visible PREVIEW banner and no server");
  await p2.screenshot({ path: path.join(SHOTS, "13-preview-home.png") });
  await p2.getByRole("link", { name: "Ask AI" }).click();
  await p2.locator("#q").waitFor({ timeout: 15000 });
  await p2.locator("#q").fill("Is this mole dangerous?");
  await p2.getByRole("button", { name: "Send question" }).click();
  await p2.getByText(/Not sent\./).waitFor({ timeout: 15000 });
  const previewText = await p2.locator("main").innerText();
  r.ok(/Preview mode/.test(previewText) && (await p2.locator('section[aria-label="Conversation"] .card').count()) === 0, "preview mode: a question is refused honestly ('Not sent', preview message) and no answer is produced");
  await p2.screenshot({ path: path.join(SHOTS, "14-preview-ask.png") });
  await p2.getByRole("link", { name: "Reports" }).first().click();
  await p2.waitForURL(/\/reports\/?$/, { timeout: 15000 });
  await p2.waitForLoadState("networkidle").catch(() => {});
  r.ok(/report/i.test(await p2.locator("main").innerText()), "preview mode: the reports screen shows its empty state, not example data");
  await p2.goto(ORIGIN + "/settings/");
  await p2.getByText("Preview mode: no server connected").waitFor({ timeout: 15000 });
  r.ok(true, "preview mode: Settings says there is no server connection");
  r.ok(apiCalls.length === 0, "preview mode made no API request at all", apiCalls);
  await p2.getByRole("button", { name: "Connect to a server" }).click();
  await p2.waitForURL(/\/login\/?$/, { timeout: 15000 });
  r.ok((await p2.getByText(/PREVIEW · no server is connected/).count()) === 0, "leaving preview returns to the sign-in screen and removes the banner");
  r.ok(errors2.length === 0, "no uncaught script errors in preview mode", errors2);
  await ctx2.close();

  r.ok(errors.length === 0, "no uncaught script errors during the whole run", errors);
} catch (e) {
  r.ok(false, "the run completed", String(e).slice(0, 500));
  await shot("zz-failure").catch(() => {});
}
await browser.close();
server.close();
process.exit(r.done() ? 1 : 0);
