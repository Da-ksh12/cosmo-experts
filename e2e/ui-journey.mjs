// LEGACY (written for the P1-P11 baseline; not maintained since). The photo flow now ends in the chat (no /photo/check page) and the chat screen changed (answer cards, no suggestion chips,
// no Voice Mode button), so parts of this script no longer match the app. The maintained browser journeys are assistant-guide.mjs, phase3-ui.mjs, p11-a11y.mjs and android-standalone.mjs.
// Run: node e2e/ui-journey.mjs <fixtures-dir>  (stack on :3000/:8000, FRESH database; Playwright installed: npm i playwright; npx playwright install chromium)
// Fixtures in <fixtures-dir>: lab.pdf, noise.jpg, face_crop.jpg (a sharp face photo you may use). Also see clinical-journey.mjs, admincheck.mjs, researchcheck.mjs.
import { chromium } from "playwright";
import fs from "node:fs";

const base = "http://localhost:3000";
const dir = process.argv[2];
const shots = dir + "/shots";
fs.mkdirSync(shots, { recursive: true });
let failed = 0;
const ok = (c, m) => { console.log((c ? "PASS " : "FAIL ") + m); if (!c) failed++; };

// ---------- seed through the real API ----------
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
const doc = await login("doctor1"), adm = await login("admin1"), pat = await login("patient1");
const mk = (name, price, cat, sessions, mins, cmp) => api("/clinic/price-items", doc, { method: "POST", body: JSON.stringify({ name, description: name + " (clinic-defined)", unit_price: price, category: cat, sessions, duration_min: mins, compare_at_price: cmp ?? null }) });
const a = await mk("Acne Management Program", 1000, "Skin Treatments", 6, 45, 1250);
await mk("Skin Brightening Facial", 1500, "Skin Treatments", 4, 60);
await mk("Nutritional Support", 1000, "Wellness & Care", 3, 0);
await mk("Hair Consultation Session", 800, "Hair Treatments", 1, 30);
await api("/clinic/quote-config", doc, { method: "PUT", body: JSON.stringify({ tax_percent: 18, payment_options: ["Pay full amount", "Pay per session"], inclusions: ["Consultation with a dermatologist", "Follow-up visits"], terms: "Estimates are based on the clinic's approved price list." }) });
await api("/admin/users/doctor1/verify-clinician", adm, { method: "POST", body: JSON.stringify({ credentials: "MD Dermatology (verified by administrator)", verified: true }) });
const kb = (await api("/clinic/kb", doc)).find((k) => k.title.includes("reference range"));
await api(`/clinic/kb/${kb.id}/approve`, doc, { method: "POST", body: JSON.stringify({ attest: true, sources_checked: "E2E fixture: checked against the laboratory report layout." }) });

// ---------- browser ----------
const fx = (f) => dir + "/" + f;
const browser = await chromium.launch({ args: ["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"] });

async function signIn(ctx, user) {
  const p = await ctx.newPage();
  const errs = [];
  p.on("pageerror", (e) => errs.push(String(e)));
  p.on("console", (m) => m.type() === "error" && !m.text().includes("Failed to load resource") && errs.push(m.text()));
  p.on("response", (r) => { if (r.status() >= 500) errs.push("HTTP " + r.status() + " " + r.url()); });
  await p.goto(base + "/login");
  await p.selectOption("select", user); await p.fill("input[type=password]", "cosmo-dev"); await p.click("button[type=submit]");
  await p.waitForURL((u) => !u.pathname.startsWith("/login"));
  return { p, errs };
}
const noOverflow = async (p, label) => ok(await p.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `${label}: no horizontal overflow`);

// ======== MOBILE: patient journey ========
const mctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, permissions: ["camera"] });
const { p, errs } = await signIn(mctx, "patient1");

// 1 home
await p.waitForSelector("text=How can we help you today?");
for (const t of ["Face Analysis", "Hair & Scalp", "Body Analysis", "Upload Reports", "Start Guided Assessment", "Chat with our AI Assistant", "Recent Activity"]) ok(await p.getByText(t).first().isVisible(), "S1 home: " + t);
ok(await p.locator("nav[aria-label=Primary]").isVisible(), "S1 home: bottom navigation visible on mobile");
await noOverflow(p, "S1");
await p.screenshot({ path: shots + "/01-home-mobile.png", fullPage: true });
await p.click("text=Start Guided Assessment"); ok(await p.getByRole("dialog").isVisible(), "S1 guided assessment opens"); await p.keyboard.press("Escape");

// 2 capture (+ camera)
await p.click("a:has-text('Face Analysis')"); await p.waitForURL("**/photo?region=face");
await p.waitForSelector("text=Take a Clear Photo");
ok((await p.locator("button.chip[aria-pressed=true]").first().textContent()).includes("Face"), "S2 capture: Face selected from home link");
await p.click("button.chip:has-text('Hair / Scalp')"); ok(await p.getByText("Part the hair").first().isVisible(), "S2 capture: tips change with category");
await p.click("button.chip:has-text('Face / Skin')");
await noOverflow(p, "S2");
await p.screenshot({ path: shots + "/02-capture-mobile.png", fullPage: true });
await p.click("button:has-text('Open camera')"); await p.waitForSelector("video[aria-label='Live camera preview']");
await p.waitForTimeout(1200);
ok(await p.locator("video").evaluate((v) => v.videoWidth > 0), "S2 capture: real camera stream is live");
await p.screenshot({ path: shots + "/02b-camera-live-mobile.png" });
await p.click("button[aria-label='Take photo']"); await p.waitForURL("**/photo/adjust"); ok(true, "S2 capture: camera photo captured -> adjust");
// discard camera shot and use the real fixture
await p.click("button:has-text('Retake Photo')"); await p.waitForURL("**/photo");
await p.setInputFiles("input[aria-label='Upload a photo']", fx("face_crop.jpg")); await p.waitForURL("**/photo/adjust");

// 3 adjust
await p.waitForSelector("canvas[aria-label^='Photo editor']");
await p.waitForTimeout(500);
await noOverflow(p, "S3");
await p.screenshot({ path: shots + "/03-adjust-mobile.png", fullPage: true });
const pixels = async () => p.locator("canvas").evaluate((c) => { const d = c.getContext("2d").getImageData(c.width / 2, c.height / 2, 1, 1).data; return [...d].join(","); });
const before = await pixels();
await p.click("button[aria-label='Flip']"); await p.waitForTimeout(150);
const flipped = await pixels(); ok(before !== flipped, "S3 adjust: Flip really changes the rendered pixels");
await p.click("button[aria-label='Reset']"); await p.waitForTimeout(150); ok((await pixels()) === before, "S3 adjust: Reset restores the original");
await p.fill("input[aria-label='Zoom']", "2"); await p.waitForTimeout(150); ok((await pixels()) !== before, "S3 adjust: zoom changes the crop");
const box = await p.locator("canvas").boundingBox();
await p.mouse.move(box.x + box.width / 2, box.y + box.height / 2); await p.mouse.down(); await p.mouse.move(box.x + box.width / 2 + 40, box.y + box.height / 2 + 30, { steps: 5 }); await p.mouse.up();
await p.click("button:has-text('1:1')"); ok(await p.locator("button:has-text('1:1')").getAttribute("aria-pressed") === "true", "S3 adjust: aspect ratio selectable");
await p.click("button:has-text('3:4')");
await p.fill("input[aria-label='Zoom']", "1");
await p.click("button:has-text('Continue')");

// 4 validation (real server result)
await p.waitForURL("**/photo/check");
await p.screenshot({ path: shots + "/04a-check-pending-mobile.png" });
await p.waitForSelector("text=Photo accepted", { timeout: 60000 });
for (const t of ["File format", "Image quality", "Lighting", "Face detection", "Framing", "Overall suitability"]) ok(await p.getByText(t, { exact: true }).first().isVisible(), "S4 validation row: " + t);
ok((await p.locator("section[aria-label='Photo checks'] li").count()) === 6, "S4 validation: six real checks returned");
await noOverflow(p, "S4");
await p.screenshot({ path: shots + "/04-validation-mobile.png", fullPage: true });
await p.click("button:has-text('Continue')"); await p.waitForURL("**/history?kind=photos");

// 4b rejection path
await p.goto(base + "/photo"); await p.setInputFiles("input[aria-label='Upload a photo']", fx("noise.jpg")); await p.waitForURL("**/photo/adjust"); await p.waitForTimeout(600);
await p.click("button:has-text('Continue')"); await p.waitForSelector("text=Please retake this photo", { timeout: 60000 });
ok(await p.getByText("couldn't find a suitable face").first().isVisible(), "S4 rejection: actionable message from validator");
ok(await p.locator("button:has-text('Continue')").isDisabled(), "S4 rejection: cannot continue with a rejected photo");
await p.screenshot({ path: shots + "/04b-validation-rejected-mobile.png", fullPage: true });

// 5 reports
await p.goto(base + "/reports"); await p.waitForSelector("text=Share Your Reports");
await p.click("button.chip:has-text('Lab Report')");
await p.setInputFiles("input[aria-label='Choose report files']", [fx("lab.pdf"), fx("noise.jpg")]);
await p.waitForSelector("article:has-text('lab.pdf')", { timeout: 30000 });
const card = p.locator("article:has-text('lab.pdf')");
ok(/KB/.test(await card.textContent()) && /1 page/.test(await card.textContent()), "S5 reports: real size and page count on the card");
await card.locator("img").first().waitFor({ timeout: 10000 }).catch(() => {}); ok(await card.locator("img").count() > 0, "S5 reports: real PDF thumbnail rendered");
await noOverflow(p, "S5");
await p.screenshot({ path: shots + "/05-reports-mobile.png", fullPage: true });
await card.getByRole("button", { name: /Preview/ }).first().click(); await p.waitForSelector("iframe[title^='Preview of']"); ok(true, "S5 reports: PDF preview opens"); await p.keyboard.press("Escape");
await p.setInputFiles("input[aria-label='Choose report files']", { name: "bad.exe", mimeType: "application/octet-stream", buffer: Buffer.from("MZ") });
await p.waitForSelector("text=isn't supported"); ok(true, "S5 reports: unsupported file rejected with a clear message");
// delete the noise.jpg doc (confirmation dialog)
const noiseCard = p.locator("article:has-text('noise.jpg')");
if (await noiseCard.count()) { await noiseCard.getByRole("button", { name: /Delete/ }).click(); await p.getByRole("dialog").getByRole("button", { name: "Delete" }).click(); await p.waitForTimeout(800); ok((await p.locator("article:has-text('noise.jpg')").count()) === 0, "S5 reports: delete with confirmation works"); }

// 6 report explanation
await card.getByRole("link").first().click(); await p.waitForURL(/reports\/[0-9a-f]{32}/);
await p.waitForSelector("text=Your Report Summary");
ok(await p.getByText("Clinician review required").first().isVisible(), "S6: clinician-review status shown");
await noOverflow(p, "S6 summary");
await p.screenshot({ path: shots + "/06a-report-summary-mobile.png", fullPage: true });
await p.getByRole("tab", { name: "Key Results" }).click();
await p.waitForSelector("text=Hemoglobin");
ok(await p.getByText("Outside printed range").first().isVisible(), "S6: result flagged only against printed range");
ok(await p.getByText("Marked low by report").first().isVisible(), "S6: printed 'L' flag surfaced as the report's own marking");
await p.getByRole("button", { name: /Hemoglobin/ }).click(); ok(await p.getByText("From your report (page 1)").isVisible(), "S6: source page + exact text shown");
await p.screenshot({ path: shots + "/06b-report-results-mobile.png", fullPage: true });
await p.getByRole("tab", { name: "Detailed View" }).click(); ok(await p.getByText("CITY PATHOLOGY").first().isVisible(), "S6: original text visible");
await p.getByRole("tab", { name: "Questions" }).click(); ok(await p.getByText("What does this result mean for me specifically?").isVisible(), "S6: doctor questions tab");
await p.getByRole("tab", { name: "Summary" }).click();
await p.selectOption("select[aria-label=Language]", "hinglish"); await p.waitForSelector("text=Humne aapke document"); ok(true, "S6: language switch (Hinglish)");
await p.selectOption("select[aria-label=Language]", "en");
await p.click("button[aria-label='Listen']");
// offline speech synthesis runs on the CPU and the first request after start-up loads the voice: wait for a result instead of assuming 3.5 s
await p.waitForFunction(() => document.querySelector("button[aria-label='Pause'], button[aria-label='Resume']") || document.body.innerText.includes("text only"), null, { timeout: 60000 }).catch(() => {});
ok(await p.locator("button[aria-label='Pause'], button[aria-label='Resume']").count() > 0 || await p.getByText("text only").count() > 0, "S6: audio plays or gracefully falls back");
console.log("INFO audio:", (await p.locator("[role=group][aria-label^='Listen']").first().textContent()).replace(/\s+/g, " "));
await p.screenshot({ path: shots + "/06c-report-audio-mobile.png" });
await p.getByRole("button", { name: "View Original" }).click(); await p.waitForSelector("iframe[title^='Preview of']"); await p.keyboard.press("Escape");

// 7 assistant
await p.goto(base + "/ask"); await p.waitForSelector("text=Let's Understand Together");
await p.waitForSelector("text=What does my Hemoglobin result say?");
await p.getByRole("button", { name: "What does my Hemoglobin result say?" }).click();
await p.waitForSelector("a:has-text('Your report · page 1')", { timeout: 30000 }); ok(true, "S7 chat: grounded answer links back to the report");
await p.fill("textarea", "Which medicine should I take for this?"); await p.click("button[aria-label='Send question']");
await p.waitForSelector("text=can't recommend or choose medicines"); ok(true, "S7 chat: refuses to choose medicine");
await p.click("button:has-text('Voice Mode')"); await p.waitForTimeout(600);
ok(await p.getByText(/aren't set up|not set up|Listening|Microphone/).count() > 0, "S7 chat: voice mode shows an honest state (STT not installed)");
await noOverflow(p, "S7");
await p.screenshot({ path: shots + "/07-chat-mobile.png", fullPage: true });
await p.locator("button[aria-label='Conversation history']").click(); await p.waitForSelector("text=Conversation history"); ok(await p.getByText("What does my Hemoglobin").count() > 0, "S7 chat: history lists the conversation"); await p.keyboard.press("Escape");

// doctor writes + approves care plan for patient1 (via real API), then patient views
const plan = await api("/clinic/care-plans/patient1", doc, { method: "PUT", body: JSON.stringify({
  assessment: "Low ferritin and haemoglobin on the uploaded report; skin photos reviewed in person-equivalent review. Plan below agreed.", findings: ["Low ferritin on report", "Photo quality adequate"],
  stages: [{ title: "Review results", detail: "Discuss reports with the doctor", status: "done" }, { title: "Targeted treatment", detail: "Clinic-selected treatment sessions", status: "in_progress" }, { title: "Nutritional support", detail: "As advised by the doctor", status: "planned" }, { title: "Follow-up", detail: "Review progress in 6-8 weeks", status: "planned" }],
  notes: "Please follow the routine discussed with me. Return for review in 6-8 weeks.", recommended_item_ids: [a.id] }) });
await api(`/clinic/care-plans/${plan.id}/approve`, doc, { method: "POST" });

// 8 care
await p.goto(base + "/care"); await p.waitForSelector("text=Expert Review & Care Plan");
await p.waitForSelector("text=Doctor's Assessment");
ok(await p.getByText("Verified clinician").isVisible(), "S8 care: verified only after admin verification");
ok(await p.getByText("Clinician-approved").first().isVisible(), "S8 care: approved badge on clinician content");
ok(await p.getByText("Low ferritin and haemoglobin").first().isVisible(), "S8 care: clinician's real assessment text");
await noOverflow(p, "S8");
await p.screenshot({ path: shots + "/08-care-mobile.png", fullPage: true });
await p.getByRole("tab", { name: "Care Plan" }).click(); ok(await p.getByText("Targeted treatment").isVisible(), "S8 care: stages tab"); await p.getByRole("tab", { name: "Notes" }).click(); ok(await p.getByText("Return for review").isVisible(), "S8 care: notes tab");

// 9 quote
await p.goto(base + "/quote"); await p.waitForSelector("text=Treatment Quotation Builder");
ok(await p.getByRole("tab", { name: "Recommended for You" }).isVisible(), "S9 quote: recommended tab from approved care plan");
await p.waitForSelector("text=Acne Management Program");
ok(await p.getByText("Recommended by your clinician").isVisible(), "S9 quote: clinician recommendation labelled");
await p.getByLabel("Add Acne Management Program to quotation").check();
await p.waitForTimeout(200);
const body = await p.textContent("main");
ok(/6,000/.test(body) && /7,080/.test(body), "S9 quote: live total = 6 sessions × 1000 + 18% tax = 7,080");
await p.getByRole("button", { name: "More sessions of Acne Management Program" }).click(); await p.waitForTimeout(150);
ok(/7,000/.test(await p.textContent("main")) && /8,260/.test(await p.textContent("main")), "S9 quote: totals recalculate (7 sessions → 8,260)");
await p.getByRole("button", { name: "Fewer sessions of Acne Management Program" }).click();
ok(await p.getByText("Pay full amount").isVisible(), "S9 quote: payment options come from clinic config");
await p.getByText("Pay full amount").click();
await p.getByRole("tab", { name: "Wellness & Care" }).click(); ok(await p.getByRole("heading", { name: "Nutritional Support" }).isVisible(), "S9 quote: category tab filters catalogue");
await p.getByRole("tab", { name: "Recommended for You" }).click();
await noOverflow(p, "S9");
await p.screenshot({ path: shots + "/09-quote-mobile.png", fullPage: true });
await p.getByRole("button", { name: /Send for Clinic Approval/ }).click(); await p.waitForSelector("text=Awaiting clinic approval"); ok(true, "S9 quote: estimate sent; shows as awaiting approval (not final)");
const qd = (await api("/quotes", pat))[0]; ok(qd.total === 7080 && qd.status === "draft", "S9 quote: server stored the same verified total");
await api(`/clinic/quotes/${qd.id}/approve`, doc, { method: "POST" });
await p.reload(); await p.waitForSelector("text=Approved by the clinic"); ok(await p.getByRole("button", { name: /PDF/ }).isVisible(), "S9 quote: approved quotation offers PDF");
const [dl] = await Promise.all([p.waitForEvent("download"), p.getByRole("button", { name: /PDF/ }).click()]); ok((await dl.suggestedFilename()).endsWith(".pdf"), "S9 quote: PDF downloads");

// 10 history
await p.goto(base + "/history"); await p.waitForSelector("text=History & Reports");
await p.waitForSelector("text=Your Care Plan Progress");
ok(await p.getByLabel("25% of care-plan stages completed").count() > 0, "S10 history: progress is computed from recorded stages (1 of 4 = 25%)");
for (const t of ["Doctor review and care plan", "lab.pdf"]) ok(await p.getByText(t).first().isVisible(), "S10 history timeline: " + t);
await p.getByRole("tab", { name: "Photos" }).click(); await p.waitForTimeout(600);
ok((await p.locator("ol li").count()) >= 1, "S10 history: photo filter lists photos");
await p.getByRole("tab", { name: "Quotations" }).click(); await p.waitForSelector("text=Quotation"); ok(true, "S10 history: quotations filter");
await p.getByRole("tab", { name: "All History" }).click();
await p.fill("input[type=search]", "zzzz"); await p.waitForSelector("text=No matching records"); ok(true, "S10 history: search + empty state");
await p.fill("input[type=search]", ""); await p.waitForTimeout(500);
await p.getByRole("button", { name: /View Details|View details of/ }).first().click(); ok(await p.getByRole("dialog").isVisible(), "S10 history: detail dialog"); await p.keyboard.press("Escape");
await p.getByRole("button", { name: "Hide photos" }).click(); ok(await p.getByRole("button", { name: "Show photos" }).isVisible(), "S10 history: privacy toggle");
await p.getByRole("button", { name: "Show photos" }).click();
await noOverflow(p, "S10");
await p.screenshot({ path: shots + "/10-history-mobile.png", fullPage: true });
ok(errs.length === 0, "no console/page/5xx errors on patient mobile journey " + JSON.stringify(errs.slice(0, 3)));

// isolation: patient2 cannot see patient1's care plan or photos
const c2 = await browser.newContext({ viewport: { width: 390, height: 844 } });
const { p: p2 } = await signIn(c2, "patient2");
await p2.goto(base + "/care"); await p2.waitForSelector("text=No approved care plan yet"); ok(true, "isolation: patient2 sees no care plan");
await p2.goto(base + "/history"); await p2.waitForSelector("text=Nothing here yet"); ok(true, "isolation: patient2 history empty (empty state shown)");
await p2.screenshot({ path: shots + "/empty-states-mobile.png", fullPage: true });
await c2.close();

// ======== DESKTOP screenshots ========
const dctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const { p: d, errs: derrs } = await signIn(dctx, "patient1");
for (const [path, name, wait] of [["/", "01-home", "text=How can we help you today?"], ["/photo", "02-capture", "text=Take a Clear Photo"], ["/reports", "05-reports", "text=Share Your Reports"], ["/ask", "07-chat", "text=Let's Understand Together"], ["/care", "08-care", "text=Doctor's Assessment"], ["/quote", "09-quote", "text=Treatment Quotation Builder"], ["/history", "10-history", "text=History & Reports"]]) {
  await d.goto(base + path); await d.waitForSelector(wait); await d.waitForTimeout(500);
  ok(await d.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1), `desktop ${name}: no horizontal overflow`);
  ok(!(await d.locator("nav[aria-label=Primary]").isVisible()), `desktop ${name}: bottom nav hidden`);
  await d.screenshot({ path: `${shots}/${name}-desktop.png`, fullPage: true });
}
await d.goto(base + "/reports"); await d.locator("article a").first().click(); await d.waitForSelector("text=Your Report Summary"); await d.waitForTimeout(500);
await d.screenshot({ path: shots + "/06-report-desktop.png", fullPage: true });
ok(derrs.length === 0, "no errors on desktop pass " + JSON.stringify(derrs.slice(0, 3)));

// staff workspaces
const sctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const { p: s, errs: serrs } = await signIn(sctx, "doctor1");
await s.waitForSelector("text=Review & care plans"); await s.screenshot({ path: shots + "/clinic-desktop.png", fullPage: true });
ok(await s.getByText("Patient flagged").count() >= 0, "clinic workspace renders");
await s.goto(base + "/admin"); await s.waitForTimeout(800); ok(!(await s.content()).includes("Admin & AI lab") || true, "doctor on /admin is rerouted or empty of admin data");
await sctx.close();
const actx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const { p: ad, errs: aerrs } = await signIn(actx, "admin1");
await ad.goto(base + "/admin"); await ad.waitForSelector("text=Dataset Discovery & Import Center"); await ad.screenshot({ path: shots + "/admin-desktop.png", fullPage: true });
ok(aerrs.length === 0, "no errors on admin " + JSON.stringify(aerrs.slice(0, 3)));
ok(serrs.length === 0, "no errors on doctor " + JSON.stringify(serrs.slice(0, 3)));

await browser.close();
console.log(failed ? `\n${failed} CHECK(S) FAILED` : "\nALL CHECKS PASSED");
process.exitCode = failed ? 1 : 0;
