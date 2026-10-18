// Notification bell through the browser: a clinician action creates a notification, the patient sees the count, opens it, follows the link, and it is marked read.
// Run: node e2e/p11-notifications.mjs   (stack on :3000/:8000)
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import { BASE, API, api, apiToken, login, reporter } from "./lib.mjs";

const r = reporter("notifications");
const doc = await apiToken("doctor1"), pat = await apiToken("patient1");
// own fixture: a small text report, so the test does not depend on other suites having run
{
  const fd = new FormData(); fd.append("file", new Blob([["PATHOLOGY LABORATORY - Laboratory Report", "Report Date: 12/01/2024", "Ferritin     8     ng/mL     15 - 150  L", ""].join(String.fromCharCode(10))], { type: "text/plain" }), "notify-fixture.txt");
  const up = await fetch(API + "/api/documents", { method: "POST", headers: { Authorization: "Bearer " + pat }, body: fd });
  r.ok(up.status === 200, "fixture: patient1 uploaded a report");
}
await api("/notifications/read-all", pat, { method: "POST" });

const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1280, height: 900 } }); const p = await ctx.newPage();
const errs = []; p.on("pageerror", (e) => errs.push(String(e)));
await login(p, "patient1");
const bell = p.getByRole("button", { name: /^Notifications/ });
r.ok(/none unread/.test(await bell.getAttribute("aria-label")), "bell: no unread notifications to start");

// a clinician reviews the patient's report (a fresh dedupe key each time: use a new quotation approval instead of re-reviewing)
const item = (await api("/clinic/price-items", doc, { method: "POST", body: JSON.stringify({ name: "Bell test " + Date.now().toString(36), unit_price: 5, currency: "INR" }) })).body.id;
const q = (await api("/quotes", pat, { method: "POST", body: JSON.stringify({ items: [{ item_id: item, qty: 1 }] }) })).body.id;
await api(`/clinic/quotes/${q}/approve`, doc, { method: "POST" });

await p.waitForFunction(() => /1 unread/.test(document.querySelector("button[aria-label^='Notifications']")?.getAttribute("aria-label") ?? ""), null, { timeout: 45000 }).catch(() => {});
await p.reload(); await p.waitForLoadState("networkidle");
r.ok(/1 unread/.test(await bell.getAttribute("aria-label")), "bell: shows the new notification count", await bell.getAttribute("aria-label"));
await bell.click();
const dlg = p.getByRole("dialog", { name: "Notifications" }); await dlg.waitFor();
r.ok(await dlg.getByText("Your quotation is approved").first().isVisible(), "dialog: lists the notification with a generic title");
const axe = await new AxeBuilder({ page: p }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
r.ok(axe.violations.length === 0, "dialog: no WCAG A/AA violations", axe.violations.map((v) => v.id));
await dlg.getByRole("link", { name: /Your quotation is approved \(unread\)/ }).click();
await p.waitForURL(/\/quote/);
r.ok(true, "following the notification opens the quotation page");
await p.waitForTimeout(500);
r.ok((await api("/notifications?unread=true", pat)).body.unread === 0, "the notification is marked read after it is opened");
r.ok(/none unread/.test(await p.getByRole("button", { name: /^Notifications/ }).getAttribute("aria-label")), "bell: count returns to zero");
r.ok(errs.length === 0, "no uncaught errors", errs);
await b.close();
process.exit(r.done() ? 1 : 0);
