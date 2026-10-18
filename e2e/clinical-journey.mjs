// Run: node clinical-journey.mjs <fixtures-dir>   (stack on :3000/:8000, FRESH database; Playwright installed)
// Fixtures: lab.pdf (Hemoglobin 10.2 g/dL 12.0-15.5 / Ferritin 8 ng/mL 15-150 L / TSH 2.1), face_crop.jpg (a sharp face photo you may use).
// Drives BOTH sides through the real UI: patient submits -> clinician reviews and approves in the browser -> patient sees the results.
import { chromium } from "playwright";
import fs from "node:fs";

const base = "http://localhost:3000";
const dir = process.argv[2];
const shots = dir + "/shots";
fs.mkdirSync(shots, { recursive: true });
let failed = 0;
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) failed++; };

async function api(path, token, init = {}) {
  const headers = { ...(init.headers || {}) };
  if (token) headers.Authorization = "Bearer " + token;
  if (init.body && typeof init.body === "string") headers["Content-Type"] = "application/json";
  const r = await fetch(base + "/api" + path, { ...init, headers });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(path + " -> " + r.status + " " + JSON.stringify(j));
  return j;
}
const login = async (u) => (await api("/auth/login", null, { method: "POST", body: JSON.stringify({ user_id: u, password: "cosmo-dev" }) })).token;

// ---- clinic configuration only (price list + clinician verification); every clinical ACTION below goes through the UI
const doc = await login("doctor1"), adm = await login("admin1");
await api("/clinic/price-items", doc, { method: "POST", body: JSON.stringify({ name: "Acne Management Program", description: "clinic-defined", unit_price: 1000, category: "Skin Treatments", sessions: 6, duration_min: 45 }) });
await api("/clinic/price-items", doc, { method: "POST", body: JSON.stringify({ name: "Nutritional Support", description: "clinic-defined", unit_price: 500, category: "Wellness", sessions: 3, duration_min: 0 }) });
await api("/clinic/quote-config", doc, { method: "PUT", body: JSON.stringify({ tax_percent: 18, payment_options: ["Pay full amount"], inclusions: ["Follow-up visits"] }) });
await api("/admin/users/doctor1/verify-clinician", adm, { method: "POST", body: JSON.stringify({ credentials: "MD Dermatology (verified by administrator)", verified: true }) });

const browser = await chromium.launch();
async function signIn(user, viewport) {
  const ctx = await browser.newContext({ viewport, deviceScaleFactor: 1 });
  const p = await ctx.newPage();
  const errs = [];
  p.on("pageerror", (e) => errs.push(String(e)));
  p.on("console", (m) => m.type() === "error" && !m.text().includes("Failed to load resource") && errs.push(m.text()));
  p.on("response", (r) => { if (r.status() >= 500) errs.push("HTTP " + r.status() + " " + r.url()); });
  await p.goto(base + "/login");
  await p.selectOption("select", user); await p.fill("input[type=password]", "cosmo-dev"); await p.click("button[type=submit]");
  await p.waitForURL((u) => !u.pathname.startsWith("/login"));
  return { p, errs, ctx };
}

// ================= PATIENT (mobile) submits =================
const { p, errs: perrs } = await signIn("patient1", { width: 390, height: 844 });
await p.goto(base + "/photo?region=face");
await p.setInputFiles("input[aria-label='Upload a photo']", dir + "/face_crop.jpg"); await p.waitForURL("**/photo/adjust");
await p.waitForSelector("canvas[aria-label^='Photo editor']"); await p.waitForTimeout(500);
await p.click("button:has-text('Continue')"); await p.waitForURL("**/photo/check");
await p.waitForSelector("text=Photo accepted", { timeout: 60000 }); ok(true, "patient: photo accepted by the real validator");
await p.click("button:has-text('Continue')"); await p.waitForURL("**/history?kind=photos");

await p.goto(base + "/reports"); await p.click("button.chip:has-text('Lab Report')");
await p.setInputFiles("input[aria-label='Choose report files']", dir + "/lab.pdf");
await p.waitForSelector("article:has-text('lab.pdf')", { timeout: 60000 }); ok(true, "patient: lab report uploaded and read");
await p.locator("article:has-text('lab.pdf') a").first().click(); await p.waitForURL(/reports\/[0-9a-f]{32}/);
const reportUrl = p.url();
await p.getByRole("tab", { name: "Key Results" }).click();
await p.getByRole("button", { name: /Ferritin/ }).click();
await p.getByRole("button", { name: /This looks wrong/ }).click(); await p.waitForSelector("text=flagged for your clinician"); ok(true, "patient: flags a doubtful value");

await p.goto(base + "/quote"); await p.waitForSelector("text=Acne Management Program");
await p.getByRole("tab", { name: "All Treatments" }).click();
await p.getByLabel("Add Acne Management Program to quotation").check();
await p.getByRole("button", { name: /Send for Clinic Approval/ }).click(); await p.waitForSelector("text=Awaiting clinic approval"); ok(true, "patient: quotation request sent");

// ================= DOCTOR (desktop) works ONLY through the UI =================
const { p: d, errs: derrs, ctx: dctx } = await signIn("doctor1", { width: 1280, height: 900 });
await d.waitForSelector("text=Review & care plans");
await d.waitForSelector("table tbody tr:has-text('Demo Patient One')");
ok(await d.locator("tr:has-text('lab.pdf')").count() === 1 && await d.locator("tr:has-text('Demo Patient One')").count() === 1, "doctor: sees exactly this patient's submission in the table");
// photo: view, inspect, review
await d.getByRole("button", { name: /Open face photo from Demo Patient One/ }).click();
const dlg = d.getByRole("dialog");
await dlg.locator("img").first().waitFor();
await d.waitForFunction(() => { const i = document.querySelector("[role=dialog] img"); return i && i.naturalWidth > 100; });
ok(true, "doctor: can open and SEE the submitted photo (image decoded in the browser)");
ok(await dlg.getByText("Automated photo check").isVisible() && await dlg.getByText("Face detection").isVisible(), "doctor: sees the automated check beside the photo, labelled as usability only");
await d.screenshot({ path: shots + "/clinical-01-photo-dialog.png" });
await dlg.getByRole("button", { name: /Mark photo reviewed/ }).click(); await d.waitForSelector("text=Photo marked reviewed"); ok(true, "doctor: marks photo reviewed");
// document: correct a value, see the patient's flag, mark reviewed
await d.locator("tr:has-text('lab.pdf')").getByRole("button", { name: "Review" }).click();
await d.waitForSelector("section[aria-label='Document review']");
const hb = d.locator("section[aria-label='Document review'] div.rounded-2xl:has(strong:text-is('Hemoglobin'))");
await hb.getByLabel("Value").fill("10.4");
await hb.getByRole("button", { name: "Save correction" }).click(); await d.waitForSelector("text=Saved.");
ok(await d.locator("section[aria-label='Document review']").getByText("Patient flagged").count() === 1, "doctor: sees the patient's flag on exactly the flagged value");
await d.screenshot({ path: shots + "/clinical-02-document-review.png", fullPage: true });
await d.getByRole("button", { name: /Mark document reviewed/ }).click(); await d.waitForSelector("text=Marked reviewed.");
// care plan: author, save draft, approve, record progress
await d.locator("select[aria-label=Patient]").selectOption("patient1");
await d.waitForSelector("[aria-label='Case overview']"); ok(true, "doctor: case overview loads photos and documents for the chosen patient");
await d.getByLabel("Clinical assessment").fill("Report shows low ferritin and a slightly low haemoglobin. Skin photo reviewed in clinic. Plan agreed with the patient.");
await d.getByLabel("Findings (comma separated)").fill("Low ferritin on report, Photo quality adequate");
await d.getByRole("button", { name: "Add stage" }).click(); await d.getByRole("button", { name: "Add stage" }).click();
await d.getByLabel("Stage 1 title").fill("Review results"); await d.getByLabel("Stage 1 detail").fill("Discuss the reports");
await d.getByLabel("Stage 2 title").fill("Targeted treatment"); await d.getByLabel("Stage 2 detail").fill("Clinic-selected sessions");
await d.getByLabel("Doctor's notes for the patient").fill("Please return in 6-8 weeks for review.");
await d.getByRole("checkbox", { name: /Acne Management Program/ }).check();
ok(await d.getByRole("button", { name: "Approve plan" }).isDisabled(), "doctor: cannot approve before saving a draft");
await d.getByRole("button", { name: "Save draft" }).click(); await d.waitForSelector("text=Draft saved.");
await d.getByRole("button", { name: "Approve plan" }).click(); await d.waitForSelector("text=Plan approved");
await d.getByText("Record progress on the approved plan").waitFor();
await d.locator("div:has(> span:has-text('Review results'))").getByRole("button", { name: "done" }).first().click(); await d.waitForSelector("text=Progress recorded.");
ok(true, "doctor: authored, approved and recorded progress on a care plan through the UI");
await d.screenshot({ path: shots + "/clinical-03-care-plan.png", fullPage: true });
// quotation approval
await d.locator("div.card:has-text('patient1')").filter({ hasText: "awaiting approval" }).getByRole("button", { name: "Approve", exact: true }).click();
await d.waitForSelector("text=Quotation approved."); ok(true, "doctor: approved the patient's quotation");
ok(derrs.length === 0, "doctor session: no console/page/5xx errors " + JSON.stringify(derrs.slice(0, 2)));

// ================= ADMIN boundary =================
const { p: a, errs: aerrs } = await signIn("admin1", { width: 1280, height: 900 });
await a.waitForSelector("text=Review & care plans");
await a.locator("select[aria-label=Patient]").selectOption("patient1");
await a.waitForSelector("text=Administrators can view but not author or approve clinical plans");
ok(await a.getByRole("button", { name: "Approve plan" }).count() === 0 && await a.getByLabel("Clinical assessment").isDisabled(), "admin: sees the plan but cannot author or approve it");
ok(await a.getByRole("button", { name: /Mark photo reviewed/ }).count() === 0, "admin: no clinical photo-review action");
await a.close();

// ================= PATIENT sees the outcome =================
await p.goto(base + "/care"); await p.waitForSelector("text=Doctor's Assessment");
ok(await p.getByText("low ferritin and a slightly low haemoglobin", { exact: false }).count() > 0, "patient: sees the clinician's assessment text");
ok(await p.getByText("Verified clinician").isVisible(), "patient: sees the clinician as verified (admin recorded it)");
ok(await p.getByText("Review results").first().isVisible() && await p.getByText("Done").first().isVisible(), "patient: sees stage 1 recorded as done");
await p.screenshot({ path: shots + "/clinical-04-patient-care.png", fullPage: true });
await p.goto(base + "/quote"); await p.waitForSelector("text=Approved by the clinic");
const [dl] = await Promise.all([p.waitForEvent("download"), p.getByRole("button", { name: /PDF/ }).click()]); ok((await dl.suggestedFilename()).endsWith(".pdf"), "patient: downloads the approved quotation PDF");
await p.goto(base + "/history"); await p.waitForSelector("text=Your Care Plan Progress");
ok(await p.getByLabel("50% of care-plan stages completed").count() > 0, "patient: history progress is 50% (1 of 2 recorded stages)");
await p.goto(base + "/history?kind=photos"); await p.waitForSelector("text=reviewed by a clinician"); ok(true, "patient: photo shows 'reviewed by a clinician'");
await p.goto(reportUrl); await p.getByRole("tab", { name: "Key Results" }).click();
await p.waitForSelector("text=10.4"); ok(true, "patient: sees the clinician-corrected value (10.4) in the report");
await p.getByRole("button", { name: /Hemoglobin/ }).click(); ok(await p.getByText("Review status: corrected").count() > 0, "patient: sees the review status 'corrected'");
await p.screenshot({ path: shots + "/clinical-05-patient-report.png", fullPage: true });
ok(perrs.length === 0, "patient session: no console/page/5xx errors " + JSON.stringify(perrs.slice(0, 2)));

// ================= audit trail =================
const audit = await api("/admin/audit?limit=500", adm);
const has = (action, actor) => audit.some((r) => r.action === action && (!actor || r.actor === actor));
for (const [act, who] of [["image.staff_access", "doctor1"], ["image.reviewed", "doctor1"], ["field.review", "doctor1"], ["field.patient_flag", "patient1"], ["document.reviewed", "doctor1"],
  ["case.opened", "doctor1"], ["careplan.save_draft", "doctor1"], ["careplan.approve", "doctor1"], ["careplan.stage", "doctor1"], ["quote.approve", "doctor1"]]) ok(has(act, who), `audit: ${act} by ${who}`);
ok(!has("careplan.approve", "admin1"), "audit: admin never approved a clinical plan");

await browser.close();
console.log(failed ? `\n${failed} CHECK(S) FAILED` : "\nALL CHECKS PASSED");
process.exitCode = failed ? 1 : 0;
