// Accessibility: axe-core (WCAG 2.0/2.1/2.2 A and AA rules) on every page for every role, keyboard operation, modal focus management,
// reduced motion, reflow at 320 CSS px and 200% text size. Run: node e2e/p11-a11y.mjs   (stack on :3000/:8000)
import { chromium } from "playwright";
import AxeBuilder from "@axe-core/playwright";
import { BASE, login, reporter } from "./lib.mjs";

const r = reporter("a11y");
const TAGS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"];
const PAGES = {
  anonymous: ["/login"],
  patient1: ["/", "/photo", "/reports", "/ask", "/quote", "/history", "/care"],
  doctor1: ["/clinic"],
  admin1: ["/admin", "/clinic"],
};

async function axe(page, label) {
  const res = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const v = res.violations.map((x) => ({ id: x.id, impact: x.impact, help: x.help, nodes: x.nodes.length, sample: x.nodes.slice(0, 2).map((n) => n.target.join(" ")) }));
  r.ok(v.length === 0, `axe: ${label} has no WCAG A/AA violations (${res.passes.length} rules passed)`, v);
  return v;
}

const b = await chromium.launch();
const ctx = await b.newContext({ viewport: { width: 1280, height: 900 } });
let page = await ctx.newPage();

// ---------------------------------------------------------------- axe on every page, every role
for (const [who, routes] of Object.entries(PAGES)) {
  const c = await b.newContext({ viewport: { width: 1280, height: 900 } });
  const p = await c.newPage();
  if (who !== "anonymous") await login(p, who);
  for (const route of routes) {
    await p.goto(BASE + route);
    await p.waitForLoadState("networkidle").catch(() => {});
    await p.waitForTimeout(600);
    await axe(p, `${who} ${route} (desktop)`);
  }
  // mobile width too: layout changes (bottom nav) bring different elements
  await p.setViewportSize({ width: 390, height: 844 });
  for (const route of routes) {
    await p.goto(BASE + route);
    await p.waitForLoadState("networkidle").catch(() => {});
    await p.waitForTimeout(400);
    await axe(p, `${who} ${route} (390 px)`);
  }
  await c.close();
}

// ---------------------------------------------------------------- page structure
{
  const c = await b.newContext(); const p = await c.newPage();
  await login(p, "patient1");
  for (const route of PAGES.patient1) {
    await p.goto(BASE + route); await p.waitForLoadState("networkidle").catch(() => {});
    const s = await p.evaluate(() => ({ h1: document.querySelectorAll("h1").length, main: document.querySelectorAll("main").length, lang: document.documentElement.lang, title: document.title }));
    r.ok(s.h1 === 1 && s.main === 1 && s.lang && s.title, `structure ${route}: one h1, one main, lang and title`, s);
  }
  await c.close();
}

// ---------------------------------------------------------------- keyboard-only sign-in
{
  const c = await b.newContext(); const p = await c.newPage();
  await p.goto(BASE + "/login");
  await p.keyboard.press("Tab");
  const first = await p.evaluate(() => document.activeElement?.tagName);
  r.ok(["SELECT", "A", "BUTTON", "INPUT"].includes(first), "login: first Tab lands on an interactive control", first);
  await p.focus("select");
  await p.keyboard.type("Doctor");                                   // type-ahead selects the Doctor option without a mouse
  await p.keyboard.press("Tab");
  await p.keyboard.type("cosmo-dev");
  await p.keyboard.press("Enter");
  await p.waitForURL((u) => u.pathname.startsWith("/clinic"), { timeout: 20000 }).catch(() => {});
  r.ok(new URL(p.url()).pathname.startsWith("/clinic"), "login: complete sign-in using only the keyboard", p.url());
  // a visible focus indicator on every focusable element of a page
  await p.waitForLoadState("networkidle").catch(() => {});
  const bad = await p.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll("a[href], button:not([disabled]), input:not([type=hidden]), select, textarea")) {
      if (el.offsetParent === null) continue;
      el.focus();
      const cs = getComputedStyle(el);
      const visible = (cs.outlineStyle !== "none" && parseFloat(cs.outlineWidth) > 0) || cs.boxShadow !== "none";
      if (!visible) out.push((el.getAttribute("aria-label") || el.textContent || el.tagName).trim().slice(0, 40));
    }
    return out;
  });
  r.ok(bad.length === 0, "clinic: every focusable control shows a focus indicator", bad.slice(0, 8));

  // modal focus management on the knowledge-base review dialog
  const reviewBtn = p.getByRole("button", { name: /^Review article/ }).first();
  await reviewBtn.focus();
  await p.keyboard.press("Enter");
  await p.waitForSelector("[role=dialog]");
  r.ok(await p.evaluate(() => document.querySelector("[role=dialog]").contains(document.activeElement)), "dialog: focus moves into the dialog on open");
  let escaped = false;
  for (let i = 0; i < 25; i++) {
    await p.keyboard.press("Tab");
    if (!(await p.evaluate(() => document.querySelector("[role=dialog]").contains(document.activeElement)))) { escaped = true; break; }
  }
  r.ok(!escaped, "dialog: Tab never leaves the dialog (focus is trapped)");
  escaped = false;
  for (let i = 0; i < 25; i++) {
    await p.keyboard.press("Shift+Tab");
    if (!(await p.evaluate(() => document.querySelector("[role=dialog]").contains(document.activeElement)))) { escaped = true; break; }
  }
  r.ok(!escaped, "dialog: Shift+Tab never leaves the dialog");
  r.ok(await p.evaluate(() => document.querySelector("[role=dialog]").getAttribute("aria-modal") === "true" && !!document.querySelector("[role=dialog]").getAttribute("aria-label")), "dialog: aria-modal and an accessible name");
  await axe(p, "clinic knowledge-base review dialog open");
  await p.keyboard.press("Escape");
  await p.waitForSelector("[role=dialog]", { state: "detached" });
  r.ok(await p.evaluate(() => /Review article/.test(document.activeElement?.getAttribute("aria-label") || "")), "dialog: Escape closes it and focus returns to the control that opened it");
  await c.close();
}

// ---------------------------------------------------------------- reduced motion
{
  const c = await b.newContext({ reducedMotion: "reduce" }); const p = await c.newPage();
  await login(p, "patient1"); await p.goto(BASE + "/"); await p.waitForLoadState("networkidle").catch(() => {});
  const long = await p.evaluate(() => {
    const out = [];
    for (const el of document.querySelectorAll("*")) {
      const cs = getComputedStyle(el);
      const durs = [cs.animationDuration, cs.transitionDuration].flatMap((s) => s.split(",").map((x) => parseFloat(x) * (x.trim().endsWith("ms") ? 1 : 1000)));
      if (durs.some((d) => d > 50)) out.push(el.tagName + "." + String(el.className).slice(0, 30));
    }
    return out;
  });
  r.ok(long.length === 0, "reduced motion: no animation or transition longer than 50 ms remains", long.slice(0, 6));
  await c.close();
  const c2 = await b.newContext({ reducedMotion: "no-preference" }); const p2 = await c2.newPage();
  await login(p2, "patient1");
  const anim = await p2.evaluate(() => [...document.querySelectorAll("*")].some((el) => parseFloat(getComputedStyle(el).animationDuration) > 0.05));
  r.ok(anim, "control: the same page does animate when motion is allowed (so the reduced-motion result is meaningful)");
  await c2.close();
}

// ---------------------------------------------------------------- reflow and 200% text
for (const who of ["patient1", "doctor1", "admin1"]) {
  const c = await b.newContext({ viewport: { width: 320, height: 640 } }); const p = await c.newPage();
  await login(p, who);
  for (const route of who === "patient1" ? PAGES.patient1 : who === "doctor1" ? ["/clinic"] : ["/admin"]) {
    await p.goto(BASE + route); await p.waitForLoadState("networkidle").catch(() => {}); await p.waitForTimeout(500);
    const w = await p.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    r.ok(w.sw <= w.cw + 1, `reflow 320 px: ${who} ${route} has no horizontal scrolling`, w);
  }
  await c.close();
}
// WCAG 1.4.4: text enlarged to 200% (root font size) on a phone-width screen must not clip or overlap content. Contained scrollers (tables) are allowed;
// we require the page itself not to scroll sideways and no text to be cut off by its own container.
for (const who of ["patient1", "doctor1", "admin1"]) {
  const c = await b.newContext({ viewport: { width: 390, height: 844 } }); const p = await c.newPage();
  await login(p, who);
  for (const route of who === "patient1" ? PAGES.patient1 : who === "doctor1" ? ["/clinic"] : ["/admin"]) {
    await p.goto(BASE + route); await p.waitForLoadState("networkidle").catch(() => {});
    await p.evaluate(() => { document.documentElement.style.fontSize = "200%"; });
    await p.waitForTimeout(400);
    const z = await p.evaluate(() => ({
      sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth,
      clipped: [...document.querySelectorAll("button, a, h1, h2, h3, label, p, li, th, td")].filter((e) => {
        if (e.offsetParent === null || e.closest("[role=region], .scroll-x, .overflow-x-auto, .sr-only, .skip-link") || e.matches("a[href=\"#main\"]")) return false;
        const cs = getComputedStyle(e); return cs.overflow === "hidden" && cs.textOverflow !== "ellipsis" && e.scrollWidth > e.clientWidth + 2;
      }).slice(0, 5).map((e) => (e.textContent || "").trim().slice(0, 30)),
    }));
    r.ok(z.sw <= z.cw + 1 && z.clipped.length === 0, `200% text at 390 px: ${who} ${route} neither scrolls sideways nor clips text`, z);
  }
  await c.close();
}
// desktop at 200% browser zoom is a 640 px wide layout
{
  const c = await b.newContext({ viewport: { width: 640, height: 450 } }); const p = await c.newPage();
  await login(p, "patient1");
  for (const route of PAGES.patient1) {
    await p.goto(BASE + route); await p.waitForLoadState("networkidle").catch(() => {});
    const w = await p.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
    r.ok(w.sw <= w.cw + 1, `200% browser zoom (640 px): ${route} has no horizontal scrolling`, w);
  }
  await c.close();
}

await b.close();
process.exit(r.done() ? 1 : 0);
