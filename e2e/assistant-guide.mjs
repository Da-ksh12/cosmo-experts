// The skin-and-skincare guide, driven in a real browser: answers as cards (education without any document, warning-sign banner, diagnosis/medicine requests that still help, a report explained
// from its printed values with a confirm-before-write proposal), the photo -> "what are you noticing?" -> library guidance -> summary -> booking journey, the appointment request that is NOT a
// booking until the clinic records a time, accessibility (axe) and no horizontal overflow at 390 px.
// Run: node e2e/assistant-guide.mjs   (stack on :3000/:8000 with a FRESH, throw-away database; the library may be unreviewed: it is then shown with its label)
// Optional: COSMO_E2E_FACE=<path to a real face photo you hold the rights to> enables the accepted-photo journey (nothing with a face is committed to the repository).
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import zlib from "node:zlib";
import { BASE, api, apiToken, login, reporter } from "./lib.mjs";

const r = reporter("assistant_guide");
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const norm = (s) => s.replace(/\s+/g, " ").trim();
async function axe(page, label) {
  const res = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  r.ok(res.violations.length === 0, `axe: ${label} has no WCAG A/AA violations (${res.passes.length} rules passed)`,
    res.violations.map((x) => ({ id: x.id, impact: x.impact, nodes: x.nodes.length, sample: x.nodes.slice(0, 2).map((n) => n.target.join(" ")) })));
}
const noOverflow = async (page, label) => r.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${label}: no horizontal overflow`);

// ---- fixtures: a tiny synthetic report (text), and a plain picture without a face
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cosmo-guide-"));
const report = path.join(dir, "guide_report.txt");
fs.writeFileSync(report, ["Cosmo Test Laboratory", "Reported: 12/03/2025", "Ferritin  8  ng/mL  15 - 150  L", "Hemoglobin  13.5  g/dL  12.0 - 15.5", "TSH  2.1  mIU/L"].join("\n"));
function png(w, h, rgb) {                                 // a flat colour PNG, written by hand so no image library is needed
  const raw = Buffer.alloc((w * 3 + 1) * h);
  for (let y = 0; y < h; y++) { raw[y * (w * 3 + 1)] = 0; for (let x = 0; x < w; x++) { const o = y * (w * 3 + 1) + 1 + x * 3; raw[o] = rgb[0]; raw[o + 1] = rgb[1]; raw[o + 2] = rgb[2]; } }
  const crc = (buf) => { let c, t = []; for (let n = 0; n < 256; n++) { c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } let v = 0xffffffff; for (const b of buf) v = t[(v ^ b) & 255] ^ (v >>> 8); return (v ^ 0xffffffff) >>> 0; };
  const chunk = (type, data) => { const len = Buffer.alloc(4); len.writeUInt32BE(data.length); const td = Buffer.concat([Buffer.from(type), data]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([len, td, c]); };
  const ihdr = Buffer.alloc(13); ihdr.writeUInt32BE(w, 0); ihdr.writeUInt32BE(h, 4); ihdr[8] = 8; ihdr[9] = 2;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", ihdr), chunk("IDAT", zlib.deflateSync(raw)), chunk("IEND", Buffer.alloc(0))]);
}
const FACE = process.env.COSMO_E2E_FACE && fs.existsSync(process.env.COSMO_E2E_FACE) ? process.env.COSMO_E2E_FACE : null;

const pat = await apiToken("patient1"), doc = await apiToken("doctor1"), pat2 = await apiToken("patient2");
const reviewRequests = async () => (await api("/notifications", doc)).body.items.filter((n) => n.kind === "review_requested").length;
const errs = [];

const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1280, height: 900 } });
const p = await ctx.newPage();
p.on("pageerror", (e) => errs.push(String(e)));
p.on("response", (x) => { if (x.status() >= 500) errs.push("HTTP " + x.status() + " " + x.url()); });
await login(p, "patient1");

const answers = () => p.locator("[data-answer]");
/** Types a question like a person and waits for the next answer card; returns it. */
async function say(text) {
  const n = await answers().count();
  await p.locator("textarea#q").fill(text);
  await p.locator("textarea#q").press("Enter");
  await p.waitForFunction((k) => document.querySelectorAll("[data-answer]").length > k, n, { timeout: 60000 });
  return answers().last();
}
const bodyText = async () => norm(await p.innerText("body"));

// ---------------------------------------------------------------- 1. the empty screen: a welcome and real suggestions, none of the old noise
await p.goto(BASE + "/ask");
await p.waitForSelector("[data-welcome]", { timeout: 30000 });
let t = await bodyText();
r.ok(/How can I help with your skin today\?/.test(t) && /cannot diagnose or choose medicines/.test(t), "the empty chat welcomes the person and says what it can and cannot do, once");
r.ok(!/I answer from your uploaded reports/.test(t) && !/Things I can do for you/.test(t) && !/Doctor Review/.test(t) && !/Suggested questions/.test(t), "the old banner, the duplicate action card and the generic chips are gone");
const starterCount = await p.locator("[data-starters] a, [data-starters] button").count();
r.ok(starterCount >= 3, "the welcome offers real suggestions", { starterCount });
r.ok((await p.locator('[data-starters] a[href="/book"], [data-starters] a[href="/photo"]').count()) >= 2, "some suggestions open real screens (book, photo)");
await axe(p, "patient1 /ask (empty)");
await p.setViewportSize({ width: 390, height: 844 }); await noOverflow(p, "/ask empty at 390 px"); await p.setViewportSize({ width: 1280, height: 900 });

// ---------------------------------------------------------------- 2. education needs no document
let c = await say("Suggest a gentle skincare routine");
t = norm(await c.innerText());
r.ok((await c.getAttribute("data-answer")) === "education", "a skincare question is answered as an education card");
r.ok(!/couldn't find anything in your documents/i.test(t) && !/Clinician review required/i.test(t), "no 'nothing found' and no 'clinician review required' on a normal skincare question");
r.ok((await c.locator("[data-section]").count()) >= 2, "the answer has structured sections");
r.ok((await c.locator("[data-review-tier]").count()) === 1, "the answer says whether the clinic has reviewed the wording (one honest label)");
r.ok((await c.locator("summary:has-text('Sources')").count()) === 1, "the answer lists its sources");
r.ok((await c.getByRole("link", { name: /Book a dermatologist appointment/ }).count()) === 1, "the answer offers a real next step (book)");
await axe(p, "patient1 /ask (education card)");
await p.setViewportSize({ width: 390, height: 844 }); await noOverflow(p, "/ask with an answer at 390 px"); await p.setViewportSize({ width: 1280, height: 900 });

// ---------------------------------------------------------------- 3. a concern: follow-ups are real choices, and the answer keeps the context
c = await say("I have pimples on my cheeks");
t = norm(await c.innerText());
r.ok(/acne|pimples/i.test(t) && (await c.locator("[data-follow-ups]").count()) === 1, "a concern gets guidance and one or two follow-up questions");
const askBefore = await answers().count();
await c.locator("[data-follow-up='duration']").filter({ hasText: "About a month" }).click();
await p.waitForFunction((k) => document.querySelectorAll("[data-answer]").length > k + 0, askBefore, { timeout: 60000 });
c = answers().last(); t = norm(await c.innerText());
r.ok(/noted that/i.test(t) && /week|month/i.test(t), "answering a follow-up is acknowledged and remembered", { t: t.slice(0, 200) });
c = await say("What should I avoid?");
t = norm(await c.innerText());
r.ok(/avoid/i.test(t) && /acne|pimple/i.test(t), "a follow-up question keeps the topic (no need to repeat 'acne')", { t: t.slice(0, 200) });

// ---------------------------------------------------------------- 4. a diagnosis or a medicine is not given, and the answer still helps
c = await say("Do I have acne? Diagnose me");
t = norm(await c.innerText());
r.ok(/can't diagnose/i.test(t) && (await c.getByRole("link", { name: /Book a dermatologist appointment/ }).count()) === 1, "a request to diagnose is declined with a reason and a way forward, not a bare refusal");
r.ok(!/Clinician review required/i.test(t), "the decline does not use the generic 'clinician review required'");

// ---------------------------------------------------------------- 5. warning signs: proportionate, explained, and the clinic's own emergency words
const policy = (await api(`/conversations/${(await api("/conversations", pat2, { method: "POST" })).body.id}/messages`, pat2, { method: "POST", body: JSON.stringify({ text: "my lips and tongue are swollen after a new cream" }) })).body;
c = await say("my lips and tongue are swollen after a new cream");
const banner = c.locator("[data-escalation='emergency']");
r.ok((await banner.count()) === 1, "a possible emergency raises a clear banner");
t = norm(await banner.innerText());
r.ok(t.includes(norm(policy.answer).slice(0, 80)), "the banner carries the clinic's own urgent-symptom message", { want: norm(policy.answer).slice(0, 80) });
r.ok((await banner.locator("[data-escalation-reason]").count()) === 1, "...and says why it was raised");
c = await say("a red area on my leg is spreading fast and is hot and painful");
r.ok((await c.locator("[data-escalation='urgent']").count()) === 1 && /today/i.test(norm(await c.locator("[data-escalation='urgent']").innerText())), "a same-day sign says so, with the reason");
c = await say("I have dry skin on my hands");
r.ok((await c.locator("[data-escalation]").count()) === 0, "an ordinary dryness question raises no banner (escalation is not the default)");

// ---------------------------------------------------------------- 6. a report: explained from what it prints; a change is proposed and waits for the person
const form = new FormData();
form.append("file", new Blob([fs.readFileSync(report)], { type: "text/plain" }), "guide_report.txt");
const up = await fetch((process.env.COSMO_API ?? "http://127.0.0.1:8000") + "/api/documents", { method: "POST", headers: { Authorization: "Bearer " + pat }, body: form });
r.ok(up.status < 300, "a synthetic report is uploaded", { status: up.status });
await p.waitForTimeout(2500);
c = await say("Explain my latest report");
r.ok((await c.getAttribute("data-answer")) === "report" && (await c.locator("[data-report-items] li").count()) >= 2, "a report is explained as a list of its printed values");
t = norm(await c.locator("[data-report-items]").innerText());
r.ok(/Ferritin/.test(t) && /Printed range: 15 - 150/.test(t) && /Marked low by the report/i.test(t), "a value is called low only because the report marked it, with its printed range", { t: t.slice(0, 300) });
r.ok(/TSH/.test(t) && /No range printed/i.test(t), "a value without a printed range says so instead of being judged");
r.ok((await c.getByRole("link", { name: /Open the full explanation/ }).count()) === 1, "the full explanation is one tap away");
const before = await reviewRequests();
await c.getByRole("button", { name: /Ask the clinic to review this report/ }).click();
await p.getByRole("button", { name: "Yes, do this" }).last().waitFor({ timeout: 60000 });
r.ok((await reviewRequests()) === before, "a proposal alone notified nobody");
await p.getByRole("button", { name: "Yes, do this" }).last().click();
await p.waitForSelector("text=Done.", { timeout: 30000 });
r.ok((await reviewRequests()) === before + 1, "confirming asked the clinic exactly once", { before, after: await reviewRequests() });
await axe(p, "patient1 /ask (report card)");

// ---------------------------------------------------------------- 7. booking: honest about what it is, then the clinic answers
c = await say("I want to book an appointment");
t = norm(await c.innerText());
r.ok(/not connected|can't show or book a slot|request/i.test(t) && !/your appointment is booked|appointment confirmed/i.test(t), "the chat does not pretend to book: it explains the request step", { t: t.slice(0, 220) });
await c.getByRole("link", { name: /Send an appointment request/ }).click();
await p.waitForURL(/\/book/, { timeout: 20000 });
await p.waitForSelector("[data-clinic-profile]");
t = await bodyText();
r.ok(/Live availability is not connected/i.test(t) && /does not book a time/i.test(t), "the booking screen says availability is not connected and a request is not a booking");
r.ok(/has not added its contact details/i.test(t), "an unconfigured clinic is shown honestly (no invented phone number or hours)");
await axe(p, "patient1 /book");
await p.setViewportSize({ width: 390, height: 844 }); await noOverflow(p, "/book at 390 px"); await p.setViewportSize({ width: 1280, height: 900 });
await p.locator("#reason").fill("Pimples that keep coming back on my cheeks");
await p.locator("#preferred").fill("Weekday evenings");
await p.getByRole("button", { name: "Send request" }).click();
await p.waitForSelector("[data-request-sent]", { timeout: 30000 });
t = norm(await p.locator("[data-request-sent]").innerText());
r.ok(/not a confirmed appointment yet/i.test(t), "after sending, the screen says it is not a confirmed appointment");
r.ok((await p.locator("[data-request-status='requested']").count()) === 1, "the request is listed as waiting for the clinic");

const dctx = await b.newContext({ viewport: { width: 1280, height: 1000 } });
const d = await dctx.newPage();
d.on("pageerror", (e) => errs.push(String(e)));
await login(d, "doctor1");
await d.goto(BASE + "/clinic");
await d.waitForSelector("[data-appointment-queue] [data-appointment]", { timeout: 30000 });
r.ok((await d.locator("[data-appointment-queue] [data-appointment]").count()) >= 1, "the clinic sees the request in its queue");
await d.locator("[data-appointment-queue] [data-appointment]").first().getByRole("button", { name: "Confirm a time" }).click();
const dlg = d.getByRole("dialog");
r.ok(await dlg.getByRole("button", { name: "Save", exact: true }).isDisabled(), "confirming needs a typed day and time");
await dlg.getByPlaceholder(/Tuesday 28 October/).fill("Tuesday 28 October, 4:30 pm");
await dlg.getByRole("button", { name: "Save", exact: true }).click();
await d.waitForSelector("text=Saved. The patient has been notified.", { timeout: 20000 });
await d.locator("[data-appointment-queue]").getByRole("button", { name: "All", exact: true }).click();
await d.waitForSelector("[data-appointment-queue] [data-status='confirmed']", { timeout: 20000 });
await axe(d, "doctor1 /clinic (appointment queue)");
// the clinic fills in its own details; they appear to patients, never before
await d.locator("[data-clinic-profile-form] input[type=tel]").first().fill("+91 98765 43210");
await d.getByRole("button", { name: "Save clinic details" }).click();
await d.waitForSelector("text=Clinic details saved.", { timeout: 20000 });
await p.reload(); await p.waitForSelector("[data-clinic-profile]");
t = await bodyText();
r.ok(/Confirmed for Tuesday 28 October, 4:30 pm/.test(t), "the patient sees the time the clinic recorded");
r.ok(/98765 43210/.test(t) && !/has not added its contact details/i.test(t), "the clinic's own contact details appear once the clinic has entered them");
await p.goto(BASE + "/ask");
c = await say("What is the clinic phone number?");
r.ok(/98765 43210/.test(norm(await c.innerText())) || (await c.locator("a[href^='tel:']").count()) >= 1, "the assistant quotes the clinic's own number, from its configuration");

// ---------------------------------------------------------------- 8. a photo: quality first, then what the person is noticing, then guidance, a summary and a booking
async function sendPhoto(buffer, name, mime) {
  await p.goto(BASE + "/photo?region=face");
  await p.locator('input[aria-label="Upload a photo"]').setInputFiles(typeof buffer === "string" ? buffer : { name, mimeType: mime, buffer });
  await p.waitForURL(/\/photo\/adjust/, { timeout: 30000 });
  await p.getByRole("button", { name: /Continue/ }).click();
  await p.waitForURL(/\/ask/, { timeout: 60000 });
}
if (FACE) {
  await sendPhoto(FACE);
  await p.waitForSelector("[data-photo-assessment] [data-analysis-state='completed'], [data-photo-assessment] [data-analysis-state='uncertain'], [data-photo-assessment] [data-analysis-state='rejected']", { timeout: 120000 });
  const state = await p.locator("[data-photo-assessment] [data-analysis-state]").first().getAttribute("data-analysis-state");
  r.ok(state === "completed", "a clear face photo completes the assessment", { state });
  if (state === "completed") {
    await p.waitForSelector("[data-guidance]");
    t = norm(await p.locator("[data-guidance]").innerText());
    r.ok(/cannot tell whether anything on your skin needs attention/i.test(t), "the guidance never says the skin is fine: it says what the check cannot tell");
    r.ok(!/\b(acne|rosacea|eczema|melasma|psoriasis)\b.{0,40}\b(detected|found|present)\b/i.test(t), "no condition is claimed from the photo");
    r.ok((await p.locator("[data-concern-prompt]").count()) === 1, "the assistant asks what the person is noticing, as real choices");
    const aid = await p.locator("[data-photo-assessment] [data-analysis-id]").first().getAttribute("data-analysis-id");
    const full = (await api(`/v1/photo-analyses/${aid}`, pat)).body;
    r.ok(Array.isArray(full.observations) && full.observations.every((o) => o.kind !== "skin_feature"), "no skin finding, spot or mark is claimed (no approved skin-analysis model exists)", { kinds: (full.observations ?? []).map((o) => o.kind) });
    r.ok(full.guidance && full.guidance.may_explain.length === 0 && full.guidance.basis === "photo_quality_only", "the guidance block says it rests on photo quality only");
    await axe(p, "patient1 /ask (photo assessment)");
    await p.locator("[data-concern='Pimples or bumps']").click();
    await p.waitForFunction(() => document.querySelectorAll("[data-answer]").length > 0, null, { timeout: 60000 });
    c = answers().last(); t = norm(await c.innerText());
    r.ok(/not a finding from your photo/i.test(t) && /acne|pimples/i.test(t), "after choosing a concern, the guidance is the library's and says it is not a finding from the photo", { t: t.slice(0, 220) });
    c = await say("Prepare a summary for my dermatologist");
    t = norm(await c.innerText());
    r.ok(/Summary for your dermatologist/i.test(t) && /not a diagnosis/i.test(t), "the summary is a factual hand-over, not a diagnosis");
    await c.getByRole("link", { name: /Book a dermatologist appointment/ }).click();
    await p.waitForURL(/\/book\?.*analysis=/, { timeout: 20000 });
    r.ok(true, "booking from a conversation with a photo carries the assessment");
    await p.locator("#reason").fill("Please look at the area I photographed");
    r.ok((await p.locator("[data-share-summary]").isChecked()) === false, "sharing the photo summary is an explicit choice, off by default");
    await p.locator("[data-share-summary]").check();
    await p.getByRole("button", { name: "Send request" }).click();
    await p.waitForSelector("[data-request-sent]", { timeout: 30000 });
    await d.goto(BASE + "/clinic"); await d.waitForSelector("[data-appointment-queue] [data-appointment]");
    await d.getByRole("button", { name: /Photo summary shared by the patient/ }).first().click();
    await d.waitForSelector("text=chose to share this with their request", { timeout: 20000 });
    r.ok(true, "the clinic can open the summary the patient chose to share (and nothing else of the photos)");
  }
} else r.skip("accepted-photo journey", "set COSMO_E2E_FACE to a real face photo you may use; none is stored in the repository");

await sendPhoto(png(420, 420, [200, 160, 140]), "no_face.png", "image/png");
await p.waitForSelector("[data-photo-assessment] [data-analysis-state='rejected'], [data-photo-assessment] [data-analysis-state='uncertain']", { timeout: 120000 });
r.ok(true, "a picture without a face is not accepted (retake, with the reason)");
t = await bodyText();
r.ok(!/couldn't find anything in your documents/i.test(t), "a photo question never ends in 'nothing found in your documents'");
if (await p.locator("[data-concern-prompt]").count()) {
  await p.locator("[data-concern-prompt] button").first().click();
  await p.waitForFunction(() => document.querySelectorAll("[data-answer]").length > 0, null, { timeout: 60000 });
  t = norm(await answers().last().innerText());
  r.ok(/retake|new photo|better photo/i.test(t) || /pimples|acne/i.test(t), "choosing a concern after an unusable photo still gets help (retake advice and the library), not a dead end", { t: t.slice(0, 200) });
}

// ---------------------------------------------------------------- done
r.ok(errs.length === 0, "no page errors and no 5xx responses during the journey", errs.slice(0, 5));
await b.close();
const failed = r.done();
process.exit(failed ? 1 : 0);
