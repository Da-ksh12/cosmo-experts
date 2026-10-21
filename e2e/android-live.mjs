// Does the exact bundle that is inside the Android APK (frontend/out-android) work end to end against a REAL backend reached over the INTERNET with https?
//
// The bundle is served the way Capacitor serves it from inside the APK (origin https://localhost, no server process), in a phone-sized Chromium. The ONLY other address that can be
// reached is the https address of the backend under test (typed into the app's own server dialog, as a person would); everything else is refused and reported.
// This proves: the server dialog, CORS from the app's origin, sign-in with a non-default password, the AI answer, the photo check and the report reading on the real backend (queued jobs
// included), the history / care plan / quotation / privacy / settings screens, sign-out, and the clinician sign-in. It does NOT prove anything about Android itself (WebView, install, permissions).
//
// Prerequisites: node frontend/scripts/build-android.mjs   (the standalone bundle in frontend/out-android)
//                the backend + tunnel from  ops/windows/start-cosmo-demo.ps1  (its https address and the demo password)
// Run:           COSMO_LIVE_URL=https://<address> COSMO_TEST_PASSWORD_FILE=<file> [COSMO_FIXTURES=<dir with face_crop.jpg and lab.pdf>] [COSMO_SHOTS_DIR=<dir>] node e2e/android-live.mjs
// The address and the password are scrubbed from the written results (docs/runs/p11_android-live.json) and screenshots go to COSMO_SHOTS_DIR (default: the temp folder), not into the repository.
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { reporter } from "./lib.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(here, "..", "frontend", "out-android");
const ORIGIN = "https://localhost";                                       // the app's own origin inside the APK
const fromEnv = (name, fileName) => (process.env[name] ?? (process.env[fileName] ? fs.readFileSync(process.env[fileName], "utf8") : "")).trim();
const LIVE = fromEnv("COSMO_LIVE_URL", "COSMO_LIVE_URL_FILE").replace(/\/+$/, "");
const PASSWORD = fromEnv("COSMO_TEST_PASSWORD", "COSMO_TEST_PASSWORD_FILE");
if (!/^https:\/\/[a-z0-9.-]+$/i.test(LIVE) || !PASSWORD) {
  console.error("Set COSMO_LIVE_URL (or COSMO_LIVE_URL_FILE) to the https address of the backend and COSMO_TEST_PASSWORD (or COSMO_TEST_PASSWORD_FILE) to the demo password.");
  process.exit(2);
}
const LIVE_HOST = new URL(LIVE).host;
const FIX = process.env.COSMO_FIXTURES ?? "";
const SHOTS = process.env.COSMO_SHOTS_DIR ?? path.join(os.tmpdir(), "cosmo-live-shots");
fs.mkdirSync(SHOTS, { recursive: true });

const rep = reporter("android-live");
const scrub = (v) => (v === undefined ? v : JSON.parse(JSON.stringify(v, (_k, x) => (typeof x === "string" ? x.replaceAll(LIVE, "<server>").replaceAll(LIVE_HOST, "<server>").replaceAll(PASSWORD, "<password>") : x))));
const r = { ok: (c, m, d) => rep.ok(c, scrub(m), scrub(d)), skip: (m, why) => rep.skip(scrub(m), why), done: () => rep.done() };

// ---- the "APK": the files of frontend/out-android, with Capacitor's directory-index behaviour
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2", ".txt": "text/plain" };
function fileFor(url) {
  let f = path.join(WEB, decodeURIComponent(new URL(url).pathname));
  if (!f.startsWith(WEB)) return { status: 403, contentType: "text/plain", body: "forbidden" };
  if (fs.existsSync(f) && fs.statSync(f).isDirectory()) f = path.join(f, "index.html");
  else if (!fs.existsSync(f) && fs.existsSync(path.join(f, "index.html"))) f = path.join(f, "index.html");
  if (!fs.existsSync(f)) return { status: 404, contentType: TYPES[".html"], body: fs.readFileSync(path.join(WEB, "404.html")) };
  return { status: 200, contentType: TYPES[path.extname(f)] ?? "application/octet-stream", body: fs.readFileSync(f) };
}

const browser = await chromium.launch({ args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });
const ctx = await browser.newContext({
  viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, permissions: ["camera", "microphone"], ignoreHTTPSErrors: true,
  userAgent: "Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
});
const outside = [];                                                       // anything that is neither the app's own files nor the backend under test
const live = [];                                                          // requests that reached the backend: "METHOD /path"
await ctx.route("**/*", (route) => {
  const req = route.request();
  const u = new URL(req.url());
  if (u.origin === ORIGIN) return route.fulfill(fileFor(req.url()));
  if (u.origin === LIVE) { live.push(`${req.method()} ${u.pathname}`); return route.continue(); }
  outside.push(req.url());
  return route.abort("internetdisconnected");
});
const page = await ctx.newPage();
const errors = [];
page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
const shot = (name) => page.screenshot({ path: path.join(SHOTS, `${name}.png`) });
const main = async () => (await page.locator("main").innerText({ timeout: 10000 }).catch(() => ""));
const settle = async () => { await page.waitForLoadState("domcontentloaded"); await page.waitForTimeout(900); };
const BAD = /can't connect|can.t reach|something went wrong|could not load|preview mode/i;     // none of these may be on a screen that is supposed to show real data
const hit = (re) => live.some((x) => re.test(x));

try {
  // 1. first start: nothing configured -> asks for the server
  await page.goto(ORIGIN + "/");
  await page.waitForURL(/\/login\/?$/, { timeout: 30000 });
  await page.getByRole("heading", { name: "Connect to your clinic" }).waitFor({ timeout: 15000 });
  r.ok(outside.length === 0 && live.length === 0, "cold start: the app opens from its own files and contacts nothing");

  // 2. the server address is pasted the way a person would: no https://, a trailing slash. It is tested against the real server (CORS from the app's origin) and saved
  await page.getByRole("button", { name: "Set the server address" }).click();
  await page.locator("#server-url").fill(LIVE_HOST + "/");
  await page.getByRole("button", { name: "Test connection" }).click();
  await page.getByText("Connected.").waitFor({ timeout: 45000 });
  r.ok(hit(/^GET \/api\/v1\/health\/live$/), "Test connection reaches the real server over https and reports Connected (the browser allowed the call from the app's origin)");
  await page.getByRole("button", { name: "Save" }).click();

  // 3. sign-in: demo accounts of the real backend, the wrong password refused, the right one accepted
  await page.locator("select").waitFor({ timeout: 45000 });
  const offered = await page.locator("select option").allTextContents();
  r.ok(offered.length >= 2 && !offered.some((t) => /admin/i.test(t)), "the app offers patient and doctor sign-in only (no administrator console)", offered);
  await page.selectOption("select", "patient1");
  await page.fill("input[type=password]", "cosmo-dev");
  await page.click("button[type=submit]");
  await page.getByText(/incorrect|invalid|could not sign in|wrong/i).first().waitFor({ timeout: 45000 });
  r.ok(true, "the documented development password is rejected by the real server");
  await page.fill("input[type=password]", PASSWORD);
  await page.click("button[type=submit]");
  await page.waitForURL((u) => u.pathname === "/" || u.pathname === "", { timeout: 60000 });
  await page.locator('nav[aria-label="Primary"]').waitFor({ timeout: 30000 });
  r.ok(hit(/^POST \/api\/auth\/login$/), "signed in with the demo password; the patient home and bottom navigation are shown");
  await page.waitForTimeout(2000);
  const home = await main();
  r.ok(home.length > 40 && !BAD.test(home) && /Demo Patient One|How can we help|Good (morning|afternoon|evening)/i.test(home), "the home screen is filled from the real server (no error or preview state)", home.slice(0, 160));
  await shot("01-home");

  // 4. the AI conversation: a real answer from the backend
  await page.getByRole("link", { name: "Ask AI" }).click();
  await page.waitForURL(/\/ask\/?/, { timeout: 20000 });
  await page.locator("#q").waitFor({ timeout: 20000 });
  const asked = page.waitForResponse((x) => /\/api\/conversations\/[^/]+\/messages$/.test(x.url()) && x.request().method() === "POST", { timeout: 240000 });
  await page.locator("#q").fill("What does it mean when a lab value is outside the printed range?");
  await page.getByRole("button", { name: "Send question" }).click();
  r.ok((await asked).status() === 200, "the question went to the real backend and was answered (POST .../messages -> 200)");
  await page.locator('section[aria-label="Conversation"] .card').first().waitFor({ timeout: 90000 });
  const answer = (await page.locator('section[aria-label="Conversation"] .card p').first().innerText()).trim();
  r.ok(answer.length > 20, "an assistant answer is displayed (on an empty demo database with no report and no approved content the honest answer is that it has nothing to go on)", answer.slice(0, 140));
  await shot("02-ask-answer");

  // 5. the photo flow on the real backend: choose, adjust, queued check, verdict from the real validator, saved to history
  if (FIX && fs.existsSync(path.join(FIX, "face_crop.jpg"))) {
    await page.goto(ORIGIN + "/photo/?region=face");
    await settle();
    await page.locator('input[aria-label="Upload a photo"]').setInputFiles(path.join(FIX, "face_crop.jpg"));
    await page.waitForURL(/\/photo\/adjust\/?/, { timeout: 30000 });
    await page.getByRole("button", { name: /continue/i }).first().click();
    await page.waitForURL(/\/photo\/check\/?/, { timeout: 30000 });
    await page.getByText("Photo accepted").waitFor({ timeout: 300000 });
    r.ok(hit(/^POST \/api\/v1\/images\/check$/) && hit(/^GET \/api\/v1\/jobs\/[^/]+$/), "photo: uploaded over https, checked by the real validator as a queued job, verdict 'Photo accepted'");
    await shot("03-photo-accepted");
    await page.getByRole("button", { name: /^Continue/ }).click();
    await page.waitForURL(/\/history\/?/, { timeout: 30000 });
    await settle();
    const hist = await main();
    r.ok(hist.length > 40 && !BAD.test(hist) && /photo/i.test(hist), "the accepted photo leads to the history screen, loaded from the server", hist.slice(0, 140));
  } else {
    r.skip("photo flow", "COSMO_FIXTURES with face_crop.jpg not provided");
  }

  // 6. reports: a PDF is read by the real backend, listed, and opened on the per-report page of the bundle (/reports/view/?id=...)
  if (FIX && fs.existsSync(path.join(FIX, "lab.pdf"))) {
    await page.goto(ORIGIN + "/reports/");
    await settle();
    await page.locator('input[aria-label="Choose report files"]').setInputFiles(path.join(FIX, "lab.pdf"));
    await page.getByText(/was read successfully/).first().waitFor({ timeout: 300000 });
    r.ok(hit(/^POST \/api\/v1\/documents$/), "report: the PDF was uploaded over https and read by the real backend ('was read successfully')");
    const link = page.getByRole("link", { name: /Open explanation of lab\.pdf/ }).first();
    await link.waitFor({ timeout: 30000 });
    await link.click();
    await page.waitForURL(/\/reports\/view\/\?id=/, { timeout: 30000 });
    await settle();
    await page.waitForFunction(() => /Hemoglobin/i.test(document.querySelector("main")?.innerText ?? ""), null, { timeout: 60000 });
    const rep1 = await main();
    r.ok(/Hemoglobin/i.test(rep1) && /Ferritin/i.test(rep1) && !BAD.test(rep1), "the report page shows the values read from the PDF (Hemoglobin, Ferritin)", rep1.slice(0, 200));
    await shot("04-report");

    // 6b. with a document of its own the assistant offers questions based on it (the generic starters are replaced), and the answer quotes THIS report (Ferritin 8 ng/mL, Hemoglobin 10.2 g/dL)
    await page.goto(ORIGIN + "/ask/");
    const suggestion = page.locator("#suggested button", { hasText: /What does my Ferritin result say\?/ });
    await suggestion.waitFor({ timeout: 60000 });
    r.ok(true, "after a report is read, the suggested questions are about that report (e.g. 'What does my Ferritin result say?')");
    const asked2 = page.waitForResponse((x) => /\/api\/conversations\/[^/]+\/messages$/.test(x.url()) && x.request().method() === "POST", { timeout: 240000 });
    await suggestion.click();
    r.ok((await asked2).status() === 200, "the suggested question reached the real backend and was answered (200)");
    const convo = page.locator('section[aria-label="Conversation"]');
    await page.waitForFunction(() => /ferritin/i.test(document.querySelector('section[aria-label="Conversation"]')?.innerText ?? "") && /says:/i.test(document.querySelector('section[aria-label="Conversation"]')?.innerText ?? ""), null, { timeout: 120000 }).catch(() => {});
    const grounded = await convo.innerText().catch(() => "");
    r.ok(/lab\.pdf/.test(grounded) && /Ferritin\s+8/i.test(grounded), "the answer quotes the uploaded report with its source (lab.pdf, Ferritin 8 ng/mL)", grounded.slice(-500));
    await shot("04b-ask-grounded");
  } else {
    r.skip("report flow", "COSMO_FIXTURES with lab.pdf not provided");
  }

  // 7. the remaining patient screens load from the real server
  for (const [route, expect, name] of [["/reports/", /Share Your Reports|Uploaded/i, "Reports"], ["/history/", /history|timeline|photo/i, "History"], ["/care/", /Expert Review|care plan/i, "Care plan"],
    ["/quote/", /quot/i, "Quotation"], ["/privacy/", /privacy|consent/i, "Privacy and consent"]]) {
    await page.goto(ORIGIN + route);
    await settle();
    await page.waitForLoadState("networkidle").catch(() => {});
    const t = await main();
    const busy = await page.locator("main [aria-busy=true], main .spin").count();
    r.ok(t.length > 40 && expect.test(t) && !BAD.test(t) && busy === 0, `${name}: loads from the real server (content, no error state, no endless spinner)`, t.slice(0, 120));
    await shot(`05-${name.toLowerCase().replace(/[^a-z]+/g, "-")}`);
  }

  // 8. settings: live connection status, then sign out
  await page.goto(ORIGIN + "/settings/");
  await page.getByRole("heading", { name: "Settings" }).waitFor({ timeout: 20000 });
  await page.getByText("Connected", { exact: true }).waitFor({ timeout: 45000 });
  r.ok((await main()).includes(LIVE_HOST), "settings shows the server host with a live 'Connected' status");
  await shot("06-settings");
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.waitForURL(/\/login\/?$/, { timeout: 30000 });
  await page.locator("select").waitFor({ timeout: 45000 });
  r.ok((await page.evaluate(() => sessionStorage.length)) === 0, "signing out ends the session (nothing left in session storage) and returns to sign-in with the server remembered");

  // 9. the clinician side of the same app and server
  await page.selectOption("select", "doctor1");
  await page.fill("input[type=password]", PASSWORD);
  await page.click("button[type=submit]");
  await page.waitForURL(/\/clinic\/?$/, { timeout: 60000 });
  await page.getByRole("heading", { name: /Review & care plans/ }).waitFor({ timeout: 45000 });
  await settle();
  const clinic = await main();
  r.ok(!BAD.test(clinic), "the doctor signs in and the clinic workspace loads from the real server", clinic.slice(0, 120));
  if (FIX && fs.existsSync(path.join(FIX, "face_crop.jpg"))) {              // the patient's photo travelled to the doctor: the thumbnails are fetched with the bearer token and shown
    await page.waitForFunction(() => { const imgs = [...document.querySelectorAll('[aria-label="Photo submissions"] img')]; return imgs.length > 0 && imgs.every((i) => i.complete && i.naturalWidth > 0); }, null, { timeout: 120000 });
    r.ok(true, "the doctor sees the patient's photo submissions: the images are downloaded over https with the sign-in token and displayed");
  }
  await shot("07-clinic");

  // 10. the whole run touched nothing but the app's files and the one server that was typed in
  r.ok(outside.length === 0, "no request ever went anywhere except the app's own files and the one server address", outside);
  r.ok(live.every((x) => !/localhost|127\.0\.0\.1/.test(x)), "no request used a development address");
  r.ok(errors.length === 0, "no uncaught script errors during the whole run", errors);
} catch (e) {
  r.ok(false, "the run completed", String(e).slice(0, 700));
  await shot("zz-live-failure").catch(() => {});
}
await browser.close();
process.exit(rep.done() ? 1 : 0);
