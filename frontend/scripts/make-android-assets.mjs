// Draws the launcher icons and the splash images of the Android app from the brand mark that the web app already uses (components/ui.tsx, <Leaf>) and the brand colours
// (app/globals.css). Chromium (Playwright, installed for the e2e checks) renders the SVG; the PNGs are written into the Android project and committed.
//   node scripts/make-android-assets.mjs
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const res = path.resolve(here, "..", "..", "android", "app", "src", "main", "res");
const require = createRequire(path.resolve(here, "..", "..", "e2e", "package.json"));
const { chromium } = require("playwright");

const TEAL = "#064f50", TEAL_DARK = "#083f43", IVORY = "#faf7f1", GOLD = "#b58a45", GOLD_SOFT = "#d3ac66", SAGE = "#9cbab3", SAGE_LIGHT = "#cfe0dc", GOLD_TEXT = "#86622a";

/** The brand leaf (same geometry as <Leaf>), drawn into a 160x160 box; `on` is "dark" (light leaf on teal) or "light" (teal leaf on ivory). */
function leaf(on) {
  const body = on === "dark" ? IVORY : TEAL, low = on === "dark" ? SAGE_LIGHT : SAGE, vein = on === "dark" ? GOLD_SOFT : GOLD;
  return `<path d="M92 8c40 18 56 62 28 104-14 20-40 30-62 24C44 98 52 40 92 8Z" fill="${body}"/>
    <path d="M92 8c-10 34-18 76-34 128" stroke="${vein}" stroke-width="4" stroke-linecap="round" fill="none"/>
    <path d="M76 52c14-2 26-10 34-22M68 84c18 0 32-8 42-22M62 112c16 2 32-6 44-18" stroke="${vein}" stroke-width="3" stroke-linecap="round" fill="none" opacity=".85"/>
    <path d="M26 128c22-26 44-24 62-10-6 22-34 34-62 10Z" fill="${low}"/>`;
}
const mark = (cx, cy, h, on) => { const s = h / 128; return `<g transform="translate(${cx - 73 * s} ${cy - 72 * s}) scale(${s})">${leaf(on)}</g>`; };   // the leaf spans about x 26-120, y 8-136

const svgIcon = (n, shape) => `<svg xmlns="http://www.w3.org/2000/svg" width="${n}" height="${n}" viewBox="0 0 ${n} ${n}">
  <defs><radialGradient id="g" cx="30%" cy="22%" r="95%"><stop offset="0" stop-color="#0b6a6b"/><stop offset="1" stop-color="${TEAL}"/></radialGradient></defs>
  ${shape === "round" ? `<circle cx="${n / 2}" cy="${n / 2}" r="${n / 2}" fill="url(#g)"/>` : `<rect width="${n}" height="${n}" rx="${n * 0.22}" fill="url(#g)"/>`}
  ${mark(n / 2, n / 2, n * 0.56, "dark")}</svg>`;
const svgForeground = (n) => `<svg xmlns="http://www.w3.org/2000/svg" width="${n}" height="${n}" viewBox="0 0 ${n} ${n}">${mark(n / 2, n / 2, n * 0.40, "dark")}</svg>`;     // inside the 66/108 safe zone
const svgSplashMark = (n) => `<svg xmlns="http://www.w3.org/2000/svg" width="${n}" height="${n}" viewBox="0 0 ${n} ${n}">${mark(n / 2, n / 2, n * 0.50, "light")}</svg>`;
const svgSplash = (w, h) => {
  const u = Math.min(w, h), cx = w / 2, cy = h * 0.43;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}"><rect width="${w}" height="${h}" fill="${IVORY}"/>
  ${mark(cx, cy, u * 0.2, "light")}
  <text x="${cx}" y="${cy + u * 0.19}" text-anchor="middle" font-family="Georgia, 'Times New Roman', serif" font-weight="700" font-size="${u * 0.075}" fill="${TEAL_DARK}">Cosmo Experts</text>
  <text x="${cx}" y="${cy + u * 0.245}" text-anchor="middle" font-family="Arial, Helvetica, sans-serif" font-weight="600" font-size="${u * 0.021}" letter-spacing="${u * 0.0042}" fill="${GOLD_TEXT}">SKIN | HAIR | BODY | AESTHETICS</text></svg>`;
};

const browser = await chromium.launch();
const page = await browser.newPage();
async function png(svg, w, h, file, transparent = false) {
  await page.setViewportSize({ width: w, height: h });
  await page.setContent(`<html><body style="margin:0;background:transparent">${svg}</body></html>`);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  await page.screenshot({ path: file, omitBackground: transparent, clip: { x: 0, y: 0, width: w, height: h } });
}

const DENS = { mdpi: 1, hdpi: 1.5, xhdpi: 2, xxhdpi: 3, xxxhdpi: 4 };
for (const [d, k] of Object.entries(DENS)) {
  const dir = path.join(res, `mipmap-${d}`);
  await png(svgIcon(Math.round(48 * k), "square"), Math.round(48 * k), Math.round(48 * k), path.join(dir, "ic_launcher.png"), true);
  await png(svgIcon(Math.round(48 * k), "round"), Math.round(48 * k), Math.round(48 * k), path.join(dir, "ic_launcher_round.png"), true);
  await png(svgForeground(Math.round(108 * k)), Math.round(108 * k), Math.round(108 * k), path.join(dir, "ic_launcher_foreground.png"), true);
}
// splash images keep the sizes of the generated template
for (const dir of fs.readdirSync(res).filter((d) => d.startsWith("drawable") && fs.existsSync(path.join(res, d, "splash.png")))) {
  const buf = fs.readFileSync(path.join(res, dir, "splash.png"));
  const w = buf.readUInt32BE(16), h = buf.readUInt32BE(20);           // PNG header: width and height
  await png(svgSplash(w, h), w, h, path.join(res, dir, "splash.png"));
}
await png(svgSplashMark(960), 960, 960, path.join(res, "drawable-nodpi", "ic_splash_mark.png"), true);
await browser.close();

// colours and theme (Android 12+ system splash: ivory background with the mark; earlier versions show the splash image as the window background)
fs.writeFileSync(path.join(res, "values", "colors.xml"), `<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="colorPrimary">${TEAL}</color>
    <color name="colorPrimaryDark">${TEAL_DARK}</color>
    <color name="colorAccent">${GOLD}</color>
    <color name="cosmo_ivory">${IVORY}</color>
</resources>
`);
fs.writeFileSync(path.join(res, "values", "ic_launcher_background.xml"), `<?xml version="1.0" encoding="utf-8"?>
<resources>
    <color name="ic_launcher_background">${TEAL}</color>
</resources>
`);
fs.writeFileSync(path.join(res, "values", "styles.xml"), `<?xml version="1.0" encoding="utf-8"?>
<resources>

    <!-- Base application theme: light only, ivory window, no action bar. -->
    <style name="AppTheme" parent="Theme.AppCompat.Light.NoActionBar">
        <item name="colorPrimary">@color/colorPrimary</item>
        <item name="colorPrimaryDark">@color/colorPrimaryDark</item>
        <item name="colorAccent">@color/colorAccent</item>
        <item name="android:windowBackground">@color/cosmo_ivory</item>
    </style>

    <style name="AppTheme.NoActionBar" parent="Theme.AppCompat.Light.NoActionBar">
        <item name="windowActionBar">false</item>
        <item name="windowNoTitle">true</item>
        <item name="colorPrimary">@color/colorPrimary</item>
        <item name="colorPrimaryDark">@color/colorPrimaryDark</item>
        <item name="colorAccent">@color/colorAccent</item>
        <item name="android:windowBackground">@color/cosmo_ivory</item>
    </style>

    <style name="AppTheme.NoActionBarLaunch" parent="Theme.SplashScreen">
        <item name="windowSplashScreenBackground">@color/cosmo_ivory</item>
        <item name="windowSplashScreenAnimatedIcon">@drawable/ic_splash_mark</item>
        <item name="postSplashScreenTheme">@style/AppTheme.NoActionBar</item>
        <item name="android:background">@drawable/splash</item>
    </style>
</resources>
`);
console.log("Android icons, splash images, colours and theme written to", res);
