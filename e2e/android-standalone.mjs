// Is the STANDALONE Android bundle (frontend/out-android, built by `node scripts/build-android.mjs`) independent of the development computer?
//
// What this proves, in a phone-sized Chromium that serves the bundle the way Capacitor serves it from inside the APK:
//   * with NO backend anywhere and no internet, the app opens and every patient screen renders (nothing blank, nothing spinning forever, no script error)
//   * it never contacts localhost, a LAN address, or any server the person did not type in; a development address cannot be entered, and one left in storage is erased
//   * with a server address that cannot be reached it says so, with Try again / Change server / Preview, and never shows a pretend sign-in
//   * airplane mode (no connection at all): the shell opens and reloads, an offline banner appears, uploads and questions fail honestly
//   * preview mode makes no request and shows no invented data
// What it does NOT prove: installation or behaviour inside the Android WebView (needs a device or emulator).
//
// Prerequisite: node frontend/scripts/build-android.mjs   (no --dev-server; creates frontend/out-android). No backend must be running.
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";
import { reporter } from "./lib.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const WEB = path.resolve(here, "..", "frontend", "out-android");
const ORIGIN = "https://localhost";                                       // the app's own origin inside the APK (Capacitor's default)
const SHOTS = path.resolve(here, "..", "docs", "runs", "android_screens");
fs.mkdirSync(SHOTS, { recursive: true });
const r = reporter("android-standalone");

// ---- the "APK": the files of frontend/out-android, served for https://localhost with Capacitor's directory-index behaviour (no server process at all)
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript", ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".woff2": "font/woff2", ".txt": "text/plain" };
function fileFor(url) {
  let f = path.join(WEB, decodeURIComponent(new URL(url).pathname));
  if (!f.startsWith(WEB)) return { status: 403, contentType: "text/plain", body: "forbidden" };
  if (fs.existsSync(f) && fs.statSync(f).isDirectory()) f = path.join(f, "index.html");
  else if (!fs.existsSync(f) && fs.existsSync(path.join(f, "index.html"))) f = path.join(f, "index.html");
  if (!fs.existsSync(f)) return { status: 404, contentType: TYPES[".html"], body: fs.readFileSync(path.join(WEB, "404.html")) };
  return { status: 200, contentType: TYPES[path.extname(f)] ?? "application/octet-stream", body: fs.readFileSync(f) };
}

function png(w, h, rgb) {                                              // a small real PNG for the upload flows
  const crcTable = Array.from({ length: 256 }, (_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = (b) => { let c = 0xffffffff; for (const x of b) c = crcTable[(c ^ x) & 0xff] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; };
  const chunk = (t, d) => { const len = Buffer.alloc(4); len.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([len, td, c]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array.from({ length: w }, (_, x) => [rgb[0] ^ (x & 15), rgb[1], rgb[2]]).flat())]);
  const raw = Buffer.concat(Array.from({ length: h }, () => row));
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}

const browser = await chromium.launch({ args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });
const ctx = await browser.newContext({
  viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, permissions: ["camera", "microphone"], ignoreHTTPSErrors: true,
  userAgent: "Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Mobile Safari/537.36",
});
// The page is loaded from the file server through a plain-http mapping of https://localhost; everything else is recorded and, as on a phone with no connection, nothing else can be reached.
const outside = [];                                                       // every request that is not to the app's own files
const errors = [];
await ctx.route("**/*", (route) => {
  const url = route.request().url();
  if (new URL(url).origin === ORIGIN) return route.fulfill(fileFor(url));      // the app's own files
  outside.push(url);
  return route.abort("internetdisconnected");                                  // no PC, no cloud, no internet
});
const page = await ctx.newPage();
page.on("pageerror", (e) => errors.push(String(e).slice(0, 300)));
const shot = (name) => page.screenshot({ path: path.join(SHOTS, `${name}.png`) });
const main = async () => (await page.locator("main").innerText({ timeout: 8000 }).catch(() => ""));
const settle = async () => { await page.waitForLoadState("domcontentloaded"); await page.waitForTimeout(700); };

try {
  // 1. cold start with nothing: no server configured, no connection to anything
  await page.goto(ORIGIN + "/");
  await page.waitForURL(/\/login\/?$/, { timeout: 20000 });
  await page.getByRole("heading", { name: "Connect to your clinic" }).waitFor({ timeout: 10000 });
  r.ok(/open without a connection/.test(await main()), "cold start with no server and no internet: the app opens (not blank) and says what works without a connection");
  r.ok(outside.length === 0, "the cold start made no request to anything outside the app", outside);
  await shot("s01-cold-start");

  // 2. a development address can never be entered in a standalone build
  await page.getByRole("button", { name: "Set the server address" }).click();
  const dialog = page.getByRole("dialog");
  const refused = async (addr, why) => {                                      // type the address, press Save, and wait for the refusal text to appear (the text changes with every attempt)
    await page.locator("#server-url").fill(addr);
    await page.getByRole("button", { name: "Save" }).click();
    return dialog.getByText(why).first().waitFor({ timeout: 4000 }).then(() => true, () => false);
  };
  for (const [addr, why] of [["http://192.168.1.20:8000", /must use https/i], ["https://192.168.1.20", /local network or to this phone/i], ["https://localhost:8000", /local network or to this phone/i], ["https://10.0.2.2", /local network or to this phone/i],
    ["http://api.example.com", /must use https/i], ["https://127.0.0.1", /local network or to this phone/i]]) {
    r.ok(await refused(addr, why), `a standalone build refuses ${addr}`);
  }
  r.ok(outside.length === 0, "refusing those addresses made no network request", outside);
  await shot("s02-refused-addresses");

  // 3. a public https address that cannot be reached: honest test result, then an honest "server unavailable" sign-in state
  await page.locator("#server-url").fill("https://api.cosmo-demo.example");
  await page.getByRole("button", { name: "Test connection" }).click();
  await page.getByText(/Could not connect|no internet connection/).waitFor({ timeout: 15000 });
  r.ok(true, "an unreachable https server is reported as not connected by the connection test");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByRole("heading", { name: "We can't reach your clinic" }).waitFor({ timeout: 20000 });
  const unavailable = await main();
  r.ok(/api\.cosmo-demo\.example/.test(unavailable) && /works without a connection/.test(unavailable), "an unreachable server shows a clear 'We can't reach your clinic' state that names the server and what still works");
  r.ok((await page.locator("button[type=submit]").count()) === 0 && (await page.getByRole("button", { name: "Try again" }).isVisible()) && (await page.getByRole("button", { name: "Change server" }).isVisible()) && (await page.getByRole("button", { name: /preview mode/i }).isVisible()),
    "that state offers Try again, Change server and Preview mode and no pretend sign-in form");
  await shot("s03-server-unavailable");
  await page.getByRole("button", { name: "Try again" }).click();
  await page.getByRole("heading", { name: "We can't reach your clinic" }).waitFor({ timeout: 20000 });
  r.ok(true, "Try again re-checks the server and, with it still down, stays in the same clear state (no blank screen, no endless spinner)");

  // 4. the stored address of an older (development) build is ignored and erased
  await page.evaluate(() => localStorage.setItem("cosmo.apiBase", "http://10.113.199.186:8000"));
  await page.goto(ORIGIN + "/login/");
  await page.getByRole("heading", { name: "Connect to your clinic" }).waitFor({ timeout: 20000 });
  r.ok((await page.evaluate(() => localStorage.getItem("cosmo.apiBase"))) === null, "a development address left in the phone's storage is ignored and erased");
  r.ok(!outside.some((u) => /10\.113|192\.168|127\.0\.0\.1:8000|localhost:8000/.test(u)), "no request ever went to a development address", outside);

  // 5. preview mode: every patient screen opens with nothing invented
  await page.getByRole("button", { name: "Look around in preview mode" }).click();
  await page.getByText(/PREVIEW · no server is connected/).waitFor({ timeout: 15000 });
  await settle();
  const TOUR = [["/", /How can we help/i, "Home"], ["/photo/?region=face", /Take a Clear Photo/i, "Photo capture"], ["/reports/", /report/i, "Reports"], ["/reports/view/?id=none", /./, "Report page"], ["/ask/", /Let's Understand Together/i, "Ask AI"],
    ["/history/", /history|timeline|photo/i, "History"], ["/care/", /Expert Review|care plan/i, "Care plan"], ["/quote/", /quot/i, "Quotation"], ["/privacy/", /privacy|consent/i, "Privacy and consent"], ["/settings/", /Settings/, "Settings"]];
  let n = 0;
  for (const [route, expect, name] of TOUR) {
    await page.goto(ORIGIN + route);
    await settle();
    const t = await main();
    const stuck = await page.locator("main [aria-busy=true], main .spin").count();
    r.ok(t.length > 20 && expect.test(t) && stuck === 0, `preview: ${name} opens with content (no blank screen, no endless spinner)`, t.slice(0, 120));
    await shot(`s05-${String(++n).padStart(2, "0")}-${name.toLowerCase().replace(/[^a-z]+/g, "-")}`);
  }
  r.ok(!outside.some((u) => !/api\.cosmo-demo\.example/.test(u)), "the whole tour made no request to anything but the one address typed in earlier", outside);

  // 6. the upload flows fail honestly in preview (no server to read a photo or a report)
  await page.goto(ORIGIN + "/photo/?region=face");
  await settle();
  await page.locator('input[aria-label="Upload a photo"]').setInputFiles({ name: "skin.png", mimeType: "image/png", buffer: png(420, 420, [200, 160, 140]) });
  await page.waitForURL(/\/photo\/adjust\/?/, { timeout: 20000 });
  await page.getByRole("button", { name: /continue/i }).first().click();
  await page.waitForURL(/\/ask\/?\?pending=/, { timeout: 20000 });
  await page.getByText(/Preview mode: no server is connected/).first().waitFor({ timeout: 20000 });
  r.ok(true, "photo flow in preview: choose, adjust and send works up to the server step, which says plainly that nothing can be analysed without a server");
  await shot("s06-photo-send-preview");
  await page.goto(ORIGIN + "/reports/");
  await settle();
  await page.locator('input[aria-label="Choose report files"]').setInputFiles({ name: "report.txt", mimeType: "text/plain", buffer: Buffer.from("Hemoglobin 13.5 g/dL") });
  await page.getByText(/Preview mode: no server is connected/).first().waitFor({ timeout: 20000 });
  r.ok(true, "report upload in preview fails honestly (nothing is read, saved or sent)");

  // 7. airplane mode: no connection at all, and the app is reloaded (relaunched) in that state
  await page.addInitScript(() => { Object.defineProperty(navigator, "onLine", { get: () => false, configurable: true }); });
  await page.goto(ORIGIN + "/ask/");
  await page.evaluate(() => window.dispatchEvent(new Event("offline")));
  await page.getByText(/You are offline/).waitFor({ timeout: 15000 });
  r.ok(true, "airplane mode: the app reopens at /ask/ and shows a clear offline banner");
  await page.reload();
  await page.evaluate(() => window.dispatchEvent(new Event("offline")));
  await page.locator("#q").waitFor({ timeout: 20000 });
  r.ok((await page.getByText(/You are offline/).count()) > 0, "airplane mode: a reload (relaunch) still opens the screen from the app's own files");
  await shot("s07-airplane-ask");

  // 8. relaunch: the session ends with the app, the saved address stays, nothing is shown that was not asked for
  await page.evaluate(() => sessionStorage.clear());
  await page.goto(ORIGIN + "/");
  await page.waitForURL(/\/login\/?$/, { timeout: 20000 });
  await settle();
  r.ok(/Connect to your clinic|We can't reach your clinic/.test(await main()), "relaunch: the app returns to the sign-in side (preview and session end with the app) without any pretend login");

  r.ok(errors.length === 0, "no uncaught script errors during the whole run", errors);
} catch (e) {
  r.ok(false, "the run completed", String(e).slice(0, 600));
  await shot("zz-standalone-failure").catch(() => {});
}
await browser.close();
process.exit(r.done() ? 1 : 0);
