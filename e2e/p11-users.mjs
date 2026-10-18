// Administrator user management through the browser. Run: node e2e/p11-users.mjs   (stack on :3000/:8000; creates one uniquely named user, so run it against a test database)
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import { BASE, api, apiToken, login, reporter } from "./lib.mjs";

const r = reporter("users");
const id = "e2e-" + Date.now().toString(36);
const PW1 = "first-long-password-1", PW2 = "second-long-password-2";
const adm = await apiToken("admin1");
const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1280, height: 1100 } }); const p = await ctx.newPage();
const errs = []; p.on("pageerror", (e) => errs.push(String(e)));
const toast = (t) => p.getByText(t, { exact: false }).first().waitFor({ timeout: 15000 });

await login(p, "admin1"); await p.goto(BASE + "/admin"); await p.waitForSelector("text=Users and permissions");
await p.getByRole("button", { name: "Add user" }).click();
const dlg = p.getByRole("dialog", { name: "Add user" }); await dlg.waitFor();
const create = dlg.getByRole("button", { name: "Create user" });
r.ok(await create.isDisabled(), "add user: Create is disabled while the form is empty");
await dlg.getByLabel("User id").fill(id); await dlg.getByLabel("Name").fill("E2E Clinician");
await dlg.getByLabel("Role").selectOption("doctor"); await dlg.getByLabel(/Temporary password/).fill("short");
r.ok(await create.isDisabled(), "add user: a password under 12 characters is not accepted");
await dlg.getByLabel(/Temporary password/).fill(PW1);
r.ok((await new AxeBuilder({ page: p }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()).violations.length === 0, "add-user dialog: no WCAG A/AA violations");
await create.click(); await toast("User created.");
const row = p.locator("tr", { hasText: id });
r.ok(await row.getByText("not verified").isVisible(), "the new clinician is listed as not verified");
r.ok((await api("/admin/users", adm)).body.some((u) => u.id === id && u.role === "doctor"), "the server holds the new account");

// the new user can sign in and reach clinical pages
const tokLogin = await fetch("http://127.0.0.1:8000/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: id, password: PW1 }) });
r.ok(tokLogin.status === 200, "the new account signs in with its temporary password");
const userTok = (await tokLogin.json()).token;

// role change drops verification and access at once
await row.getByLabel(`Role for ${id}`).selectOption("patient"); await toast("Role changed.");
r.ok((await api("/clinic/kb", userTok, {})).status === 403, "after the role change the existing session has no clinical access");
await row.getByLabel(`Role for ${id}`).selectOption("doctor"); await toast("Role changed.");

// disable / enable
await row.getByRole("button", { name: "Disable" }).click(); await toast("Account disabled.");
r.ok(await row.getByText("disabled").first().isVisible(), "the list shows the account as disabled");
r.ok((await api("/me", userTok)).status === 403, "a disabled account's token stops working");
r.ok((await fetch("http://127.0.0.1:8000/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: id, password: PW1 }) })).status === 403, "a disabled account cannot sign in");
await row.getByRole("button", { name: "Enable" }).click(); await toast("Account enabled.");
r.ok((await api("/me", userTok)).status === 200, "enabling restores access");

// password reset
await row.getByRole("button", { name: "Reset password" }).click();
const rd = p.getByRole("dialog", { name: new RegExp("Reset password for " + id) }); await rd.waitFor();
await rd.getByLabel(/New password/).fill(PW2); await rd.getByRole("button", { name: "Reset password" }).click(); await toast("Password reset.");
const old = await fetch("http://127.0.0.1:8000/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: id, password: PW1 }) });
const neu = await fetch("http://127.0.0.1:8000/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: id, password: PW2 }) });
r.ok(old.status === 401 && neu.status === 200, "the old password stops working and the new one works");

// self-protection in the UI
const mine = p.locator("tr", { hasText: "admin1" }).first();
r.ok(await mine.getByLabel("Role for admin1").isDisabled() && await mine.getByRole("button", { name: "Disable" }).isDisabled(), "an administrator cannot change or disable their own account in the UI");
r.ok((await new AxeBuilder({ page: p }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze()).violations.length === 0, "admin page with the user table: no WCAG A/AA violations");
r.ok(errs.length === 0, "no uncaught errors", errs);
await b.close();
process.exit(r.done() ? 1 : 0);
