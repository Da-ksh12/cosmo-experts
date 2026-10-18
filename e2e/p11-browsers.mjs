// Cross-browser (Chromium, Firefox, WebKit) and device-emulation smoke journeys, touch-target sizes, orientation, fake camera (Chromium only).
// Run: node e2e/p11-browsers.mjs   (stack on :3000/:8000).  Real devices are NOT covered: emulation only.
import { chromium, firefox, webkit, devices } from "playwright";
import fs from "node:fs";
import path from "node:path";
import { BASE, login, reporter } from "./lib.mjs";

const r = reporter("browsers");
const SHOTS = path.join(path.dirname(new URL(import.meta.url).pathname.replace(/^\//, "")), "..", "docs", "runs", "p11_screens");
fs.mkdirSync(SHOTS, { recursive: true });

// WebKit reports a request that is cancelled by a navigation as "Fetch API cannot load ... due to access control checks". Those are counted separately
// (and proven harmless by the idle-page check below); every other error still fails the run.
const ABORTED = [];
function watch(page, errs) {
  page.on("pageerror", (e) => { const m = String(e); (/Fetch API cannot load .* due to access control checks/.test(m) ? ABORTED : errs).push("pageerror: " + m.slice(0, 200)); });
  page.on("console", (m) => { if (m.type() === "error" && !m.text().includes("Failed to load resource")) errs.push("console: " + m.text().slice(0, 200)); });
}

async function uploadSyntheticPhoto(page) {
  const b64 = await page.evaluate(async () => {
    const c = document.createElement("canvas"); c.width = 800; c.height = 800;
    const g = c.getContext("2d");
    const grad = g.createRadialGradient(400, 400, 40, 400, 400, 420); grad.addColorStop(0, "#c98f78"); grad.addColorStop(1, "#d9a68f");
    g.fillStyle = grad; g.fillRect(0, 0, 800, 800);
    for (let i = 0; i < 3000; i++) { g.fillStyle = `rgba(${150 + (i % 40)},${100 + (i % 30)},${80 + (i % 25)},0.25)`; g.fillRect((i * 37) % 800, (i * 91) % 800, 3, 3); }
    g.fillStyle = "#6b3f2e"; g.beginPath(); g.arc(400, 400, 70, 0, 7); g.fill();
    return c.toDataURL("image/jpeg", 0.9).split(",")[1];
  });
  await page.setInputFiles('input[type=file][aria-label="Upload a photo"]', { name: "synthetic.jpg", mimeType: "image/jpeg", buffer: Buffer.from(b64, "base64") });
}

async function patientSmoke(page, label, errs) {
  await login(page, "patient1");
  r.ok(await page.locator("h1").first().isVisible(), `${label}: home renders after sign-in`);
  // ask the assistant an urgent question: deterministic safety redirect, no models required
  await page.goto(BASE + "/ask"); await page.waitForSelector("#q");
  await page.fill("#q", "I have chest pain");
  await page.click('button[aria-label="Send question"]');
  await page.waitForSelector("text=This may be urgent", { timeout: 90000 });
  r.ok(true, `${label}: assistant answers an urgent question with the emergency redirect`);
  // question about a topic with no evidence abstains instead of inventing
  await page.fill("#q", "Who won the cricket world cup in nineteen eighty three");
  await page.click('button[aria-label="Send question"]');
  await page.waitForSelector("text=would rather not guess", { timeout: 90000 });
  r.ok(true, `${label}: assistant abstains when there is no evidence`);
  await page.goto(BASE + "/reports"); await page.waitForLoadState("networkidle").catch(() => {});
  r.ok(await page.locator("h1").first().isVisible(), `${label}: reports page renders`);
  await page.goto(BASE + "/photo"); await page.waitForLoadState("networkidle").catch(() => {});
  await uploadSyntheticPhoto(page);
  await page.waitForURL(/\/photo\/(adjust|check)/, { timeout: 30000 }).catch(() => {});
  r.ok(/\/photo\/(adjust|check)/.test(page.url()), `${label}: photo upload proceeds to the next step (${new URL(page.url()).pathname})`);
}

async function staffSmoke(page, label) {
  await login(page, "doctor1");
  await page.waitForSelector("text=Review & care plans", { timeout: 20000 });
  r.ok(true, `${label}: clinician workspace renders`);
  await page.getByRole("button", { name: "Account menu for Doctor" }).or(page.locator('button[aria-label^="Account menu"]')).first().click();
  await page.getByRole("button", { name: /Sign out/ }).click();
  await page.waitForURL(/\/login/, { timeout: 15000 });
  r.ok(true, `${label}: sign-out returns to the login page`);
}

// ---------------------------------------------------------------- three engines, desktop
for (const [name, type] of [["chromium", chromium], ["firefox", firefox], ["webkit", webkit]]) {
  const browser = await type.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage(); const errs = [];
  watch(page, errs);
  try {
    r.ok(true, `${name} ${browser.version()} launched`);
    await patientSmoke(page, name, errs);
    if (name === "chromium") {
      for (const route of ["/", "/photo", "/reports", "/ask", "/quote", "/history", "/care"]) {
        await page.goto(BASE + route); await page.waitForLoadState("networkidle").catch(() => {}); await page.waitForTimeout(400);
        await page.screenshot({ path: path.join(SHOTS, `desktop-${route === "/" ? "home" : route.slice(1)}.png`), fullPage: true });
      }
    }
    await ctx.clearCookies(); await page.evaluate(() => localStorage.clear()).catch(() => {});
    await staffSmoke(page, name);
  } catch (e) {
    r.ok(false, `${name}: journey completed`, String(e).slice(0, 400));
  }
  r.ok(errs.length === 0, `${name}: no uncaught errors or console errors during the journey`, errs.slice(0, 3));
  await browser.close();
}

// ---------------------------------------------------------------- device emulation
const DEVICES = [["iPhone 13", webkit], ["Pixel 7", chromium], ["iPad (gen 7)", webkit], ["Galaxy S9+", chromium], ["iPhone SE", webkit]];
for (const [dname, type] of DEVICES) {
  const browser = await type.launch();
  const ctx = await browser.newContext({ ...devices[dname] });
  const page = await ctx.newPage(); const errs = [];
  watch(page, errs);
  try {
    await login(page, "patient1");
    const s = () => page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    for (const route of ["/", "/photo", "/reports", "/ask", "/quote", "/history", "/care"]) {
      await page.goto(BASE + route); await page.waitForLoadState("networkidle").catch(() => {}); await page.waitForTimeout(300);
      const w = await s();
      r.ok(w.sw <= w.cw + 1, `${dname}: ${route} fits the screen without sideways scrolling`, w);
    }
    // bottom navigation works by tap
    await page.goto(BASE + "/");
    const nav = page.locator('nav[aria-label="Primary"]');
    if (devices[dname].viewport.width < 768) {
      await nav.waitFor({ state: "visible", timeout: 10000 }).catch(() => {});
      r.ok(await nav.isVisible(), `${dname}: bottom navigation is visible`);
      await nav.getByRole("link", { name: "Ask AI" }).tap();
      await page.waitForURL(/\/ask/, { timeout: 10000 });
      r.ok(true, `${dname}: tapping Ask AI in the bottom navigation opens the assistant`);
    }
    // touch targets (WCAG 2.2 SC 2.5.8: at least 24x24 CSS px; 44 px is the recommended size)
    for (const route of ["/", "/ask", "/reports", "/photo"]) {
      await page.goto(BASE + route); await page.waitForLoadState("networkidle").catch(() => {}); await page.waitForTimeout(300);
      const sizes = await page.evaluate(() => [...document.querySelectorAll("a[href], button, input:not([type=hidden]):not(.sr-only), select, textarea, [role=button]")]
        .filter((e) => e.offsetParent !== null && !e.classList.contains("sr-only") && e.getBoundingClientRect().width > 0 && !(e.tagName === "A" && e.closest("p, li, span.text-sm") && e.textContent.trim().length > 0 && getComputedStyle(e).display === "inline"))
        .map((e) => { const b = e.getBoundingClientRect(); return { n: (e.getAttribute("aria-label") || e.textContent || e.tagName).trim().slice(0, 24), w: Math.round(b.width), h: Math.round(b.height) }; }));
      const tiny = sizes.filter((x) => x.w < 24 || x.h < 24);
      const below44 = sizes.filter((x) => x.w < 44 || x.h < 44).length;
      r.ok(tiny.length === 0, `${dname}: ${route} every touch target is at least 24x24 (${sizes.length} targets, ${below44} under the recommended 44)`, tiny.slice(0, 5));
    }
    await page.screenshot({ path: path.join(SHOTS, `${dname.replace(/[^a-z0-9]/gi, "_")}-ask.png`), fullPage: false });
    // landscape
    const vp = page.viewportSize();
    await page.setViewportSize({ width: vp.height, height: vp.width });
    for (const route of ["/", "/ask"]) {
      await page.goto(BASE + route); await page.waitForLoadState("networkidle").catch(() => {});
      const w = await s();
      r.ok(w.sw <= w.cw + 1, `${dname} landscape: ${route} has no sideways scrolling`, w);
    }
  } catch (e) {
    r.ok(false, `${dname}: journey completed`, String(e).slice(0, 400));
  }
  r.ok(errs.length === 0, `${dname}: no uncaught or console errors`, errs.slice(0, 3));
  await browser.close();
}

// ---------------------------------------------------------------- camera and microphone: Chromium with FAKE media devices only
{
  const browser = await chromium.launch({ args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });
  const ctx = await browser.newContext({ permissions: ["camera", "microphone"], viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage(); const errs = [];
  watch(page, errs);
  try {
    await login(page, "patient1");
    await page.goto(BASE + "/photo");
    await page.getByRole("button", { name: "Take Photo" }).click();
    await page.waitForSelector("video[aria-label='Live camera preview']");
    await page.waitForFunction(() => { const v = document.querySelector("video"); return v && v.readyState >= 2 && v.videoWidth > 0; }, null, { timeout: 15000 });
    r.ok(true, "fake camera: live preview receives frames");
    const shutter = page.locator("button[aria-label='Take photo'], button:has-text('Capture')").first();
    await shutter.click();
    await page.waitForURL(/\/photo\/(adjust|check)/, { timeout: 30000 });
    r.ok(true, `fake camera: capture proceeds (${new URL(page.url()).pathname})`);
    await page.goto(BASE + "/ask");
    const mic = page.locator("button[aria-label*='oice' i], button[aria-label*='icrophone' i]").first();
    r.ok(await mic.count() > 0, "microphone control is present on the assistant page");
  } catch (e) {
    r.ok(false, "fake camera/microphone flow", String(e).slice(0, 400));
  }
  r.ok(errs.length === 0, "fake camera: no uncaught or console errors", errs.slice(0, 3));
  await browser.close();
}

// ---------------------------------------------------------------- the WebKit fetch message must not appear on a page left idle (otherwise it is a real CORS/network defect)
{
  const browser = await webkit.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage(); const idle = [];
  await login(page, "patient1");
  await page.waitForLoadState("networkidle").catch(() => {}); await page.waitForTimeout(3000);       // let the sign-in redirect and prefetches finish first
  page.on("pageerror", (e) => idle.push(String(e).slice(0, 160)));
  for (const route of ["/", "/ask", "/history", "/care"]) {
    await page.goto(BASE + route); await page.waitForLoadState("networkidle").catch(() => {}); await page.waitForTimeout(2500);   // nothing navigates while requests settle
  }
  r.ok(idle.length === 0, "webkit: no fetch/CORS errors on pages left idle (the messages seen elsewhere were navigation-cancelled requests)", idle.slice(0, 3));
  await browser.close();
}
r.ok(true, `webkit: ${ABORTED.length} navigation-cancelled fetch messages recorded and excluded from the error check (see the idle-page result above)`);

process.exit(r.done() ? 1 : 0);
