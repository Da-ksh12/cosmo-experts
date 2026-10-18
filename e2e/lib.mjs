import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const BASE = process.env.COSMO_WEB ?? "http://localhost:3000";
export const API = process.env.COSMO_API ?? "http://127.0.0.1:8000";
const here = path.dirname(fileURLToPath(import.meta.url));

export function reporter(name) {
  const rows = [];
  return {
    ok(cond, msg, detail) {
      rows.push({ pass: !!cond, check: msg, ...(detail !== undefined ? { detail } : {}) });
      console.log((cond ? "PASS " : "FAIL ") + msg + (!cond && detail !== undefined ? "  " + JSON.stringify(detail).slice(0, 600) : ""));
    },
    done() {
      const failed = rows.filter((r) => !r.pass).length;
      const out = path.join(here, "..", "docs", "runs", `p11_${name}.json`);
      fs.writeFileSync(out, JSON.stringify({ suite: name, when: new Date().toISOString(), passed: rows.length - failed, failed, rows }, null, 2));
      console.log(`\n${name}: ${rows.length - failed}/${rows.length} passed`);
      return failed;
    },
  };
}

export async function login(page, id) {
  await page.goto(BASE + "/login");
  await page.selectOption("select", id);
  await page.fill("input[type=password]", "cosmo-dev");
  await page.click("button[type=submit]", { timeout: 90000 });
  await page.waitForURL((u) => !u.pathname.startsWith("/login"), { timeout: 20000 });
  await page.waitForLoadState("networkidle").catch(() => {});
}

export async function apiToken(id) {
  const r = await fetch(API + "/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ user_id: id, password: "cosmo-dev" }) });
  return (await r.json()).token;
}

export async function api(path_, token, init = {}) {
  const headers = { ...(init.headers || {}) };
  if (token) headers.Authorization = "Bearer " + token;
  if (init.body && typeof init.body === "string") headers["Content-Type"] = "application/json";
  const r = await fetch(API + "/api" + path_, { ...init, headers });
  return { status: r.status, body: await r.json().catch(() => ({})) };
}
