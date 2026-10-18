// Consent gate and Results-over-time through the browser. Requires the backend started with COSMO_REQUIRE_CONSENT=1 and a TEST database.
// Run: node e2e/p11-consent.mjs
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import { BASE, API, api, apiToken, login, reporter } from "./lib.mjs";

const r = reporter("consent");
const b = await chromium.launch();
const errs = [];
const mk = async (viewport = { width: 1280, height: 900 }) => { const c = await b.newContext({ viewport }); const p = await c.newPage(); p.on("pageerror", (e) => errs.push(String(e))); return [c, p]; };
const axe = async (p) => (await new AxeBuilder({ page: p }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()).violations.map((v) => v.id);

// ---- patient2 meets the gate
{
  const [c, p] = await mk();
  await login(p, "patient2");
  const gate = p.getByRole("region", { name: "Privacy notice" }); await gate.waitFor({ timeout: 20000 });
  r.ok(await gate.getByRole("heading", { name: /Before you continue/ }).isVisible(), "a patient who has not accepted sees the privacy notice instead of the app");
  r.ok(await gate.getByText(/draft notice shown for testing/i).isVisible(), "an unapproved draft is clearly labelled (and is never offered in production)");
  const accept = gate.getByRole("button", { name: "Accept and continue" });
  r.ok(await accept.isDisabled(), "Accept is disabled until the box is ticked");
  r.ok((await axe(p)).length === 0, "consent screen: no WCAG A/AA violations", await axe(p));
  const tok2 = await apiToken("patient2");
  r.ok((await api("/conversations", tok2, { method: "POST" })).status === 403, "while unaccepted, the API refuses data-collecting calls (403 consent_required)");
  await gate.getByRole("checkbox").check();
  await accept.click();
  await p.getByRole("region", { name: "Privacy notice" }).waitFor({ state: "detached", timeout: 15000 });
  r.ok(true, "after accepting, the gate disappears");
  r.ok((await api("/conversations", tok2, { method: "POST" })).status === 200, "after accepting, the API accepts the call");
  await p.goto(BASE + "/ask"); await p.waitForSelector("#q");
  r.ok(true, "the assistant page is usable after acceptance");
  await c.close();
}
// ---- another patient is asked separately; staff are not
{
  const [c, p] = await mk();
  await login(p, "patient1");
  r.ok(await p.getByRole("region", { name: "Privacy notice" }).isVisible({ timeout: 20000 }).catch(() => false), "another patient is asked to accept on their own account");
  await c.close();
  const [c2, p2] = await mk();
  await login(p2, "doctor1"); await p2.waitForSelector("text=Review & care plans");
  r.ok(!(await p2.getByRole("region", { name: "Privacy notice" }).count()), "a clinician is not shown the patient notice");
  await c2.close();
}
// ---- patient1 accepts through the API and then compares results across reports
const p1 = await apiToken("patient1");
const st = (await api("/consent", p1)).body;
await api("/consent/accept", p1, { method: "POST", body: JSON.stringify({ notice_sha256: st.notice.sha256 }) });
const text = (date, v, unit = "ng/mL") => `PATHOLOGY LABORATORY - Laboratory Report\nReport Date: ${date}\nTest            Result   Unit      Reference range\nFerritin     ${v}     ${unit}     15 - 150  L\nHemoglobin   11.2     g/dL      12.0 - 15.5  L\n`;
async function upload(name, body) {
  const fd = new FormData(); fd.append("file", new Blob([body], { type: "text/plain" }), name);
  const rr = await fetch(API + "/api/documents", { method: "POST", headers: { Authorization: "Bearer " + p1 }, body: fd }); return rr.status;
}
r.ok((await upload("jan.txt", text("12/01/2024", "8"))) === 200 && (await upload("jun.txt", text("15/06/2024", "21"))) === 200, "two lab reports with different dates were uploaded");
{
  const [c, p] = await mk();
  await login(p, "patient1");
  await p.goto(BASE + "/reports");
  const sec = p.getByRole("region", { name: "Results over time" }); await sec.waitFor({ timeout: 20000 });
  r.ok(await sec.getByText("Ferritin").first().isVisible(), "Results over time lists a test that appears in more than one report");
  const tbl = sec.getByRole("region", { name: /Ferritin results table/ });
  const rows = await tbl.locator("tbody tr").count();
  r.ok(rows === 2 && /8/.test(await tbl.locator("tbody tr").first().innerText()) && /21/.test(await tbl.locator("tbody tr").last().innerText()), "the table shows both printed values in date order", rows);
  const text_ = await sec.innerText();
  r.ok(/not converted and no meaning is added/i.test(text_), "the screen states that nothing is converted or interpreted");
  r.ok(!/improv|worse|better|normal|abnormal|healthy|concerning/i.test(text_.replace(/Abnormal/g, "")), "no interpretive wording on the comparison");
  r.ok(await sec.getByRole("link", { name: /Page 1/ }).first().isVisible(), "each value links to its source page");
  r.ok((await axe(p)).length === 0, "reports page with the comparison: no WCAG A/AA violations", await axe(p));
  await c.close();
}
r.ok(errs.length === 0, "no uncaught errors", errs);
await b.close();
process.exit(r.done() ? 1 : 0);
