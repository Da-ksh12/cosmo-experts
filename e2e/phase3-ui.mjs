// Phase 3 screens, driven in a real browser: the assistant-actions card on /ask (a proposal changes nothing until confirmed; cancelling changes nothing; urgent and refused requests offer no action),
// the report screen's printed-details / medicines sections, the visible "unreviewed translation" notice, accessibility (axe) and no horizontal overflow at 390 px.
// Run: node e2e/phase3-ui.mjs   (stack on :3000/:8000 with a FRESH, throw-away database; only a tiny synthetic text report is used)
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { BASE, api, apiToken, login, reporter } from "./lib.mjs";

const r = reporter("phase3_ui");
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const norm = (s) => s.replace(/\s+/g, " ").trim();
async function axe(page, label) {
  const res = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  r.ok(res.violations.length === 0, `axe: ${label} has no WCAG A/AA violations (${res.passes.length} rules passed)`,
    res.violations.map((x) => ({ id: x.id, impact: x.impact, nodes: x.nodes.length, sample: x.nodes.slice(0, 2).map((n) => n.target.join(" ")) })));
}
const noOverflow = async (page, label) => r.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${label}: no horizontal overflow`);

// a tiny synthetic report: lab rows, a printed date (day/month order unknowable), an allergy line, a medicine line and a follow-up line
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cosmo-p3-"));
const file = path.join(dir, "e2e_report.txt");
fs.writeFileSync(file, [
  "Cosmo Test Laboratory", "Reported: 12/03/2025", "Referred by: Dr. A Example, MD", "Drug allergy: NKDA", "",
  "Ferritin  8  ng/mL  15 - 150  L", "Hemoglobin  13.5  g/dL  12.0 - 15.5", "",
  "Rx", "Tab Metformin 500 mg BD after food x 5 days", "Follow up after 2 weeks",
].join("\n"));

const pat = await apiToken("patient1"), doc = await apiToken("doctor1");
const reviewRequests = async () => (await api("/notifications", doc)).body.items.filter((n) => n.kind === "review_requested").length;

const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1280, height: 900 } });
const p = await ctx.newPage();
const errs = [];
p.on("pageerror", (e) => errs.push(String(e)));
p.on("response", (x) => { if (x.status() >= 500) errs.push("HTTP " + x.status() + " " + x.url()); });
await login(p, "patient1");

// ---------------------------------------------------------------- upload through the real screen
await p.goto(BASE + "/reports");
await p.setInputFiles('input[aria-label="Choose report files"]', file);
let docId = null;
for (let i = 0; i < 90 && !docId; i++) {
  const list = (await api("/documents", pat)).body;
  docId = Array.isArray(list) ? list.find((d) => d.filename === "e2e_report.txt")?.id : null;
  if (!docId) await p.waitForTimeout(1000);
}
r.ok(!!docId, "the uploaded report is listed for the patient");

if (docId) {
  const detail = (await api(`/documents/${docId}`, pat)).body;
  const ex = (await api(`/documents/${docId}/explanation?lang=en`, pat)).body;
  r.ok(Array.isArray(ex.sections) && ex.sections.map((s) => s.id).slice(0, 3).join() === "found,uncertain,questions", "the explanation keeps its section order: found, uncertain, questions first", ex.sections?.map((s) => s.id));

  // ---------------------------------------------------------------- the report screen
  await p.goto(BASE + `/reports/${docId}`);
  await p.waitForSelector("text=Plain-language explanation", { timeout: 30000 });
  let body = norm(await p.innerText("body"));
  const shown = ex.sections.filter((s) => ["facts", "medicines", "background"].includes(s.id) && (s.items.length || s.summary));
  if (shown.length === 0) r.skip("report screen shows printed details and medicines", "the pipeline extracted no header facts, medicines or background from the fixture, so there is nothing to compare");
  for (const s of shown) {
    r.ok(body.includes(norm(s.title)), `the report screen shows the "${s.title}" section`);
    for (const it of s.items) r.ok(body.includes(norm(it.text).slice(0, 80)), `...and its statement: ${norm(it.text).slice(0, 60)}`);
  }
  r.ok(!body.includes("no Hindi-speaking clinician has reviewed"), "the English view carries no unreviewed-translation notice");
  await axe(p, "patient1 /reports/[id] (English)");
  await p.selectOption('select[aria-label="Language"]', "hi");
  await p.waitForSelector("text=no Hindi-speaking clinician has reviewed", { timeout: 20000 });
  r.ok(true, "the Hindi view says plainly that its wording has not been reviewed");
  await axe(p, "patient1 /reports/[id] (Hindi)");
  await p.setViewportSize({ width: 390, height: 844 });
  await noOverflow(p, "/reports/[id] at 390 px (Hindi)");
  await p.setViewportSize({ width: 1280, height: 900 });

  // ---------------------------------------------------------------- the assistant-actions card
  await p.goto(BASE + "/ask");
  await p.waitForSelector("text=Things I can do for you");
  await p.getByRole("button", { name: "Show my documents" }).click();
  await p.waitForSelector("text=Your account holds");
  r.ok(true, "a read-only request answers from the person's own records");
  r.ok((await p.getByRole("button", { name: "Yes, do this" }).count()) === 0, "a read-only answer offers no action to confirm");

  const before = await reviewRequests();
  await p.getByRole("button", { name: "Ask the clinic to look at my latest report" }).click();
  await p.getByRole("button", { name: "Yes, do this" }).waitFor({ timeout: 30000 });
  r.ok((await reviewRequests()) === before, "a proposal alone notified nobody");
  await axe(p, "patient1 /ask with a pending proposal");
  await p.getByRole("button", { name: "No, don't" }).click();
  await p.waitForSelector("text=Cancelled. Nothing was done.");
  r.ok((await reviewRequests()) === before, "cancelling changed nothing");
  r.ok(((await api("/v1/agent/actions", pat)).body.actions ?? []).length === 0, "no proposal is left pending after cancelling");

  await p.getByRole("button", { name: "Ask the clinic to look at my latest report" }).click();
  await p.getByRole("button", { name: "Yes, do this" }).click();
  await p.waitForSelector("text=Done.", { timeout: 30000 });
  r.ok((await reviewRequests()) === before + 1, "confirming asked the clinic exactly once", { before, after: await reviewRequests() });
  await p.getByRole("button", { name: "Show my documents" }).click();
  await p.waitForSelector("text=Your account holds");

  // urgent and refused requests offer no action at all
  const box = p.getByLabel("Ask the assistant to do something");
  await box.fill("I have severe chest pain and cannot breathe");
  await box.press("Enter");
  await p.waitForSelector("text=Please read", { timeout: 30000 });
  r.ok((await p.getByRole("button", { name: "Yes, do this" }).count()) === 0, "an urgent message offers no action");
  await box.fill("what is my diagnosis and which medicine should I take");
  await box.press("Enter");
  await p.waitForSelector("text=I can't help with that", { timeout: 30000 });
  r.ok((await p.getByRole("button", { name: "Yes, do this" }).count()) === 0, "a request for a diagnosis or a medicine is refused and offers no action");
  await axe(p, "patient1 /ask after a refusal");
  await p.setViewportSize({ width: 390, height: 844 });
  await noOverflow(p, "/ask at 390 px");
  await axe(p, "patient1 /ask (390 px)");
}

// ---------------------------------------------------------------- the voice question, end to end: the browser's fake microphone plays a synthesized sentence (then silence) into the real recording flow
const speechDir = process.env.COSMO_E2E_SPEECH_DIR;
if (!speechDir) r.skip("voice question round trip", "set COSMO_E2E_SPEECH_DIR to a folder with speech.wav and silence.wav (synthesized; no person is recorded)");
else {
  const record = async (file, waitMs) => {
    const vb = await chromium.launch({ args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", `--use-file-for-fake-audio-capture=${path.join(speechDir, file)}%noloop`] });
    const vp = await (await vb.newContext({ viewport: { width: 1280, height: 900 }, permissions: ["microphone"] })).newPage();
    vp.on("pageerror", (e) => errs.push(String(e)));
    await login(vp, "patient1");
    const status = vp.waitForResponse((x) => x.url().includes("/api/speech/status"));          // the microphone button needs to know whether recognition exists before it can be used
    await vp.goto(BASE + "/ask");
    await status;
    await vp.waitForTimeout(400);
    await vp.getByRole("button", { name: "Ask by voice" }).click();
    await vp.getByRole("button", { name: "Stop recording" }).waitFor({ timeout: 15000 });
    await vp.waitForTimeout(waitMs);                                                  // the clip plays once while the recording runs
    await vp.getByRole("button", { name: "Stop recording" }).click();
    return { vb, vp };
  };
  {
    const { vb, vp } = await record("speech.wav", 5000);
    await vp.waitForFunction(() => (document.querySelector("#q")?.value ?? "").length > 0, null, { timeout: 120000 });
    const heard = (await vp.inputValue("#q")).toLowerCase();
    const expected = ["what", "does", "my", "hemoglobin", "result", "say", "about", "report"];
    const hit = expected.filter((w) => heard.includes(w) || (w === "hemoglobin" && heard.includes("haemoglobin"))).length;
    r.ok(hit >= 6, `a spoken question arrives as text in the question box (${hit} of ${expected.length} words)`, heard);
    r.ok((await vp.getByText("Please check the words and correct them if needed before sending.").count()) > 0, "...with the standing request to check the words before sending");
    r.ok(await vp.locator("#q").isEditable(), "...in a box the person can correct before pressing Send (nothing is sent by itself)");
    await vb.close();
  }
  {
    const { vb, vp } = await record("silence.wav", 3000);
    await vp.getByText("We didn't hear any speech in that recording").waitFor({ timeout: 120000 });
    r.ok((await vp.inputValue("#q")) === "", "silence is reported as no speech and no words are invented");
    await vb.close();
  }
}

// ---------------------------------------------------------------- the administrator's model list with an IMPORTED (never evaluated) model
const dataDir = process.env.COSMO_E2E_DATA_DIR;
if (!dataDir) r.skip("administrator screen with an imported model", "set COSMO_E2E_DATA_DIR to the backend's data directory to run this part");
else {
  const adm = await apiToken("admin1");
  const mdir = path.join(dataDir, "models");
  fs.mkdirSync(mdir, { recursive: true });
  const mfile = path.join(mdir, "e2e_imported_model.bin");
  fs.writeFileSync(mfile, "not a real model: a fixture file for the lifecycle screen");
  const imp = await api("/admin/model-imports", adm, { method: "POST", body: JSON.stringify({ task: "acne_features", artifact_path: mfile, architecture: "fixture", source: "e2e fixture, not a real model" }) });
  r.ok(imp.status === 200 && imp.body.state === "imported", "an imported model is registered through the administrator API", imp.body);
  const ap = await (await b.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
  ap.on("pageerror", (e) => errs.push(String(e)));
  ap.on("response", (x) => { if (x.status() >= 500) errs.push("HTTP " + x.status() + " " + x.url()); });
  await login(ap, "admin1");
  await ap.goto(BASE + "/admin");
  // scoped to this model's own card, so the check can be repeated on a stack that already holds other imported models
  const card = ap.locator("div.card", { hasText: imp.body.model_id.slice(0, 8) });
  await card.getByText("Lifecycle: imported").waitFor({ timeout: 30000 });
  r.ok(true, "the administrator screen lists the imported model with its lifecycle state instead of failing");
  r.ok((await card.getByText("No evaluation has been run on this model here").count()) > 0, "...and says plainly that nothing has been evaluated");
  await axe(ap, "admin1 /admin with an imported model");
  // ticking every requirement does not approve a model that fails the gates and names no verified clinician
  ap.on("dialog", async (d) => { if (d.type() === "confirm") await d.accept(); else if (/intended use/i.test(d.message())) await d.accept("e2e fixture"); else await d.accept("doctor1"); });
  await card.getByRole("button", { name: "Record approval review" }).click();
  await card.getByText("Approval refused").waitFor({ timeout: 30000 });
  r.ok(true, "claiming every requirement does not approve a model that fails the gates; the reasons are on screen");
  await card.getByRole("button", { name: "Deploy" }).click();
  await ap.waitForSelector("text=Only a model with a complete approval record can be deployed", { timeout: 20000 });
  r.ok(true, "deploying a model that is not approved is refused, with the reason on screen");
  const rec = (await api("/admin/models", adm)).body.find((m) => m.model_id === imp.body.model_id);
  r.ok(rec && rec.status === "research_only" && rec.approval?.refused_because?.length > 0 && rec.lifecycle?.state !== "active", "the registry still holds it as research-only, not active, with the refusal recorded", rec && { status: rec.status, state: rec.lifecycle?.state });
}

r.ok(errs.length === 0, "no page errors and no 5xx responses in the whole run", errs);
await b.close();
fs.rmSync(dir, { recursive: true, force: true });
process.exit(r.done() ? 1 : 0);
