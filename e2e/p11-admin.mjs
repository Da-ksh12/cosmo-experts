// Clinician / administrator workflows driven through the browser, with the API used only to set up fixtures and to confirm outcomes.
// KB review + attested approval + edit-returns-to-draft, price list, quotation settings and quotation approval (tax applied), clinician verification,
// role restrictions, and AI Studio training Start / Stop on the real PAD-UFES-20 dataset.   Run: node e2e/p11-admin.mjs   (stack on :3000/:8000)
import { chromium } from "playwright";
import { BASE, api, apiToken, login, reporter } from "./lib.mjs";

const r = reporter("admin");
const stamp = Date.now().toString(36);
const browser = await chromium.launch();
const errs = [];
function newPage(ctx) { const p = ctx.newPage(); return p.then((pg) => { pg.on("pageerror", (e) => errs.push(String(e).slice(0, 200))); pg.on("console", (m) => m.type() === "error" && !m.text().includes("Failed to load resource") && errs.push(m.text().slice(0, 200))); return pg; }); }
const toast = (p, text, t = 15000) => p.getByText(text, { exact: false }).first().waitFor({ timeout: t });

const doc = await apiToken("doctor1"), pat = await apiToken("patient1"), adm = await apiToken("admin1");

// ================================================================ A. knowledge base review and approval (doctor)
{
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } }); const p = await newPage(ctx);
  await login(p, "doctor1"); await p.waitForSelector("text=Clinically reviewed knowledge base");
  const title = "How do I take a clear photo for the clinic?";
  const before = (await api("/clinic/kb", doc)).body.find((a) => a.title === title);
  r.ok(before && before.review_status === "draft" && before.content_origin === "ai_draft", "KB: the shipped article starts as an unreviewed software draft", before && { status: before.review_status, origin: before.content_origin });
  await p.getByText("software-drafted, not yet reviewed").first().waitFor({ timeout: 15000 }).catch(() => {});
  r.ok((await p.getByText("software-drafted, not yet reviewed").count()) > 0, "KB: the page labels unreviewed drafts");
  const ask = async (q) => { const c = (await api("/conversations", pat, { method: "POST" })).body.id; return (await api(`/conversations/${c}/messages`, pat, { method: "POST", body: JSON.stringify({ text: q }) })).body; };
  const cited = (a) => (a.sources ?? []).some((s) => s.kind === "knowledge_base" && s.title === title);
  r.ok(!cited(await ask("How do I take a clear photo for the clinic?")), "KB: while a draft, the assistant does not cite it");

  await p.getByRole("button", { name: `Review article ${title}` }).click();
  const dlg = p.getByRole("dialog", { name: "Review article" });
  await dlg.waitFor();
  r.ok(await dlg.getByText("Statements to verify before approving").isVisible(), "KB: the dialog lists the statements the reviewer must verify");
  const approve = dlg.getByRole("button", { name: "Approve this text" });
  r.ok(await approve.isDisabled(), "KB: Approve is disabled before any attestation");
  await dlg.getByLabel(/Source\(s\) you checked/).fill("too short");
  await dlg.getByRole("checkbox").check();
  r.ok(await approve.isDisabled(), "KB: still disabled with a source note shorter than 10 characters");
  await dlg.getByLabel(/Source\(s\) you checked/).fill("Clinic photography leaflet v2, page 3 (test fixture)");
  await dlg.getByRole("checkbox").uncheck();
  r.ok(await approve.isDisabled(), "KB: disabled when the attestation box is cleared");
  await dlg.getByRole("checkbox").check();
  // changing the text after opening forces a save (return to draft) before approval
  await dlg.locator("textarea").fill((await dlg.locator("textarea").inputValue()) + " ");
  r.ok(await approve.isDisabled(), "KB: editing the text disables approval until it is saved as a new draft");
  r.ok(!(await dlg.getByRole("checkbox").isChecked()), "KB: editing the text clears the attestation (it applies to one exact text)");
  await dlg.locator("textarea").fill(before.body);
  await dlg.getByRole("checkbox").check();
  r.ok(await approve.isEnabled(), "KB: enabled only with an unchanged text, a source note and the attestation");
  await approve.click();
  await toast(p, "Article approved.");
  const after = (await api("/clinic/kb", doc)).body.find((a) => a.title === title);
  r.ok(after.review_status === "approved" && after.reviewed_by === "doctor1" && after.usable_by_assistant && after.source_note.startsWith("Clinic photography leaflet"), "KB: approval recorded with reviewer and source note", after);
  r.ok(cited(await ask("How do I take a clear photo for the clinic?")), "KB: once approved, the assistant cites it");
  await p.getByRole("button", { name: `Review article ${title}` }).click();
  const d2 = p.getByRole("dialog", { name: "Review article" }); await d2.waitFor();
  r.ok(await d2.getByText("Approved by doctor1").isVisible(), "KB: reopening shows who approved it and the source note");
  await d2.locator("textarea").fill(before.body + " Take photos before applying products.");
  await d2.getByRole("button", { name: /Save changes/ }).click();
  await toast(p, "Saved as draft.");
  const edited = (await api("/clinic/kb", doc)).body.find((a) => a.title === title);
  r.ok(edited.review_status === "draft" && !edited.usable_by_assistant && edited.version === before.version + 1, "KB: an edit returns the article to draft and bumps the version", { status: edited.review_status, v: edited.version });
  r.ok(!cited(await ask("How do I take a clear photo for the clinic?")), "KB: the edited draft is no longer used by the assistant");
  await ctx.close();
}

// ================================================================ B. price list and quotation settings (doctor), quotation approval with tax
let priceId, quoteId;
{
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } }); const p = await newPage(ctx);
  await login(p, "doctor1"); await p.waitForSelector("text=Approved price list");
  const name = `P11 Laser session ${stamp}`;
  await p.getByLabel("Item name").fill(name); await p.getByLabel("Category").fill("Skin");
  await p.getByLabel("Price per session").fill("1500"); await p.getByLabel("Default sessions").fill("3");
  await p.getByLabel("Minutes per session").fill("30"); await p.getByLabel("List price").fill("2000");
  await p.getByRole("button", { name: "Add item" }).click();
  await toast(p, "Item added.");
  const item = (await api("/price-items", doc)).body.find((i) => i.name === name);
  priceId = item?.id;
  await p.getByText(name).first().waitFor({ timeout: 15000 }).catch(() => {});
  r.ok(item && item.unit_price === 1500 && item.sessions === 3 && item.duration_min === 30 && item.compare_at_price === 2000 && item.category === "Skin", "price list: item added through the form with all fields", item);
  r.ok(await p.getByText(name).first().isVisible() && await p.getByText(/1500 INR \(list 2000\)/).first().isVisible(), "price list: the new item and its list price appear on the page");

  await p.getByLabel("Tax %").fill("12");
  await p.getByLabel(/Payment options/).fill("Card\nUPI");
  await p.getByLabel(/What's included/).fill("Consultation\nAfter-care advice");
  await p.getByLabel("Terms").fill("Valid for 30 days (test fixture).");
  await p.getByRole("button", { name: "Save settings" }).click();
  await toast(p, "Settings saved.");
  await p.reload(); await p.waitForSelector("text=Quotation settings");
  await p.waitForFunction(() => [...document.querySelectorAll("label")].find((l) => l.textContent.startsWith("Tax %"))?.querySelector("input")?.value === "12", null, { timeout: 15000 }).catch(() => {});
  r.ok((await p.getByLabel("Tax %").inputValue()) === "12" && (await p.getByLabel("Terms").inputValue()).startsWith("Valid for 30 days"), "quotation settings: values persist after a reload");
  const cfg = (await api("/quote-config", doc)).body;
  r.ok(cfg.tax_percent === 12 && cfg.payment_options.join() === "Card,UPI" && cfg.inclusions.length === 2, "quotation settings: stored by the server", cfg);

  // a patient requests a quotation for the new item; the doctor approves it in the UI; tax is applied by the server
  const q = await api("/quotes", pat, { method: "POST", body: JSON.stringify({ items: [{ item_id: priceId, qty: 3 }] }) });
  quoteId = q.body.id;
  r.ok(q.status === 200 && q.body.status === "draft", "quotation: a patient request starts as a draft", q.body);
  await p.reload(); await p.waitForSelector(`text=${quoteId.slice(0, 8)}`);
  const row = p.locator("div.card", { hasText: quoteId.slice(0, 8) }).first();
  r.ok(await row.getByText("awaiting approval").isVisible(), "quotation: the clinic sees it awaiting approval");
  await row.getByRole("button", { name: "Approve", exact: true }).click();
  await toast(p, "Quotation approved.");
  const got = (await api(`/quotes`, pat)).body.find((x) => x.id === quoteId);
  r.ok(got.status === "approved" && Math.abs(got.total - 4500 * 1.12) < 0.01, "quotation: approved in the UI; total = 3 x 1500 plus 12% tax computed by the server", { status: got.status, total: got.total });
  const pdf = await fetch("http://127.0.0.1:8000/api/quotes/" + quoteId + "/pdf", { headers: { Authorization: "Bearer " + pat } });
  r.ok(pdf.status === 200 && (await pdf.arrayBuffer()).byteLength > 1000, "quotation: the patient can download the approved PDF");
  await ctx.close();
}

// ================================================================ C. administrator restrictions and clinician verification
{
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } }); const p = await newPage(ctx);
  await login(p, "admin1"); await p.goto(BASE + "/clinic"); await p.waitForSelector("text=Clinically reviewed knowledge base");
  r.ok((await p.getByRole("button", { name: /^Review article/ }).count()) === 0, "admin: sees the knowledge base but has no Review / Approve controls");
  await p.waitForSelector("text=Approved price list");
  r.ok(await p.getByRole("button", { name: "Add item" }).isDisabled(), "admin: cannot add price-list items (doctor only)");
  const approveBtns = await p.locator("div.card", { hasText: quoteId.slice(0, 8) }).getByRole("button", { name: "Approve", exact: true }).count();
  r.ok(approveBtns === 0, "admin: no quotation Approve control");
  const body = await p.textContent("main");
  r.ok(body.includes("Administrators can view but not author or approve") || !body.includes("Approve plan"), "admin: care-plan authoring is not offered");
  r.ok((await api("/clinic/kb/" + (await api("/clinic/kb", doc)).body[0].id + "/approve", adm, { method: "POST", body: JSON.stringify({ attest: true, sources_checked: "admin attempt via API" }) })).status === 403, "admin: the API also refuses KB approval");
  await p.getByLabel("Credentials").fill("MD Dermatology, reg. TEST-0001 (test fixture)");
  await p.getByRole("button", { name: /Record/ }).click();
  await toast(p, "Verification recorded.");
  const me = (await api("/clinic/patients", doc)).status; // doctor still works
  r.ok(me === 200, "admin: recording clinician verification succeeded and the doctor session still works");
  await ctx.close();
}
{
  // role separation in the browser: a patient cannot reach clinic or admin pages
  const ctx = await browser.newContext(); const p = await newPage(ctx);
  await login(p, "patient1");
  for (const route of ["/clinic", "/admin"]) {
    await p.goto(BASE + route); await p.waitForLoadState("networkidle").catch(() => {}); await p.waitForTimeout(500);
    const txt = (await p.textContent("main").catch(() => "")) ?? "";
    r.ok(!txt.includes("Clinically reviewed knowledge base") && !txt.includes("Train a model"), `patient: ${route} shows no staff content`);
  }
  r.ok((await api("/clinic/kb", pat)).status === 403 && (await api("/admin/train", pat, { method: "POST", body: "{}" })).status === 403, "patient: staff APIs return 403");
  r.ok((await api("/admin/train", doc, { method: "POST", body: "{}" })).status === 403, "doctor: the training API returns 403");
  await ctx.close();
}

// ================================================================ D. AI Studio: Start then Stop a real training job (admin)
{
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 1100 } }); const p = await newPage(ctx);
  await login(p, "admin1"); await p.goto(BASE + "/admin"); await p.waitForSelector("text=Train a model");
  const models0 = (await api("/admin/models", adm)).body;
  const start = p.getByRole("button", { name: /Start training/ });
  r.ok(await start.isDisabled(), "training: Start is disabled until a dataset is chosen");
  const training = p.locator("section[aria-label=Training]");
  const sel = training.locator("select").nth(1);                                  // Task, Dataset, Dataset version, Layers
  const opts = await sel.locator("option").allTextContents();
  const pad = opts.find((o) => /PAD-UFES-20/i.test(o));
  if (!pad) r.skip("training Start/Stop through the UI", "no approved, imported PAD-UFES-20 dataset in this database (run against a copy of the development database)");
  else r.ok(true, "training: only approved datasets are offered, including PAD-UFES-20", opts);
  if (pad) {
    await sel.selectOption({ label: pad });
    await training.locator("select").nth(2).locator("option").first().waitFor({ state: "attached", timeout: 30000 });
    await training.locator("input[type=number]").fill("1");
    await training.locator("select").nth(3).selectOption("head");
    r.ok(await start.isEnabled(), "training: Start becomes enabled with a dataset and version");
    await start.click();
    await p.getByText(/^running/).first().waitFor({ timeout: 30000 });
    r.ok(true, "training: the job reports 'running' in the UI");
    await p.locator("pre").filter({ hasNotText: "Waiting for the first log line" }).first().waitFor({ timeout: 240000 });
    const log1 = await p.locator("pre").last().textContent();
    r.ok(log1.trim().length > 0, "training: real log lines from the running process appear", log1.slice(0, 160));
    r.ok(await start.isDisabled(), "training: Start is disabled while a job runs");
    await p.getByRole("button", { name: "Stop", exact: true }).click();
    await p.getByText(/^stopped/).first().waitFor({ timeout: 900000 });
    r.ok(true, "training: Stop ends the job and the UI shows 'stopped'");
    r.ok(await start.isEnabled(), "training: Start is available again after stopping");
    const models1 = (await api("/admin/models", adm)).body;
    r.ok(models1.length === models0.length, "training: a stopped job does not register a model", { before: models0.length, after: models1.length });
  }
  await ctx.close();
}

r.ok(errs.length === 0, "no uncaught or console errors during the admin workflows", errs.slice(0, 3));
await browser.close();
process.exit(r.done() ? 1 : 0);
