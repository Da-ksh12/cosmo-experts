// Builds the Android app from the real UI:  static export  ->  Capacitor sync  ->  (optional) debug APK.
//
//   node scripts/build-android.mjs [--apk] [--api-base https://api.example.com] [--no-local]
//   node scripts/build-android.mjs --dev-server 10.0.2.2   DEVELOPMENT build only (see below)
//
// A normal build is a STANDALONE app: the screens, styles and routing are inside the APK, so it opens with no server running anywhere. Data, sign-in and AI answers come from ONE
// explicit https server address (the production API base URL):
//     --api-base https://...   or   COSMO_API_BASE=https://...   or   "apiBase" in android.config.json / android.config.local.json (in that order of precedence)
// If none is given the app still opens (screens, preview mode) and asks for the address; sign-in needs a deployed backend.
//
// A normal build REFUSES to bake in a development address: plain http://, localhost, loopback, 10.0.2.2, private-network or .local addresses fail the build, and the Android network
// configuration allows https only. --dev-server <host[,host...]> is the explicit, loudly labelled opt-in for developers testing against a backend on their own computer: it allows plain
// http to exactly those hosts in this build, and the result must never be given to clients.
//
// Needs JAVA_HOME (JDK 21) and ANDROID_HOME for --apk. android.config.local.json (yours, ignored by Git) may hold "apiBase"; --no-local ignores it.
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const androidDir = path.resolve(root, "..", "android");
const win = process.platform === "win32";
const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const value = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const read = (f) => (fs.existsSync(path.join(root, f)) ? JSON.parse(fs.readFileSync(path.join(root, f), "utf8")) : {});
const cfg = { appId: "ai.cosmoexperts.app", appName: "Cosmo Experts AI", versionName: "0.2.0-standalone", versionCode: 2, apiBase: "", androidScheme: "https", devCleartextHosts: [],
  ...read("android.config.json"), ...(flag("--no-local") ? {} : read("android.config.local.json")) };

const DEV_HOST = /^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[?::1\]?|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|169\.254\.\d+\.\d+|[^.]+\.local|[^.]+\.internal|[^.]+\.lan)$/i;
const fail = (msg, code = 2) => { console.error(`\nERROR: ${msg}`); process.exit(code); };

function run(cmd, cmdArgs, opts = {}) {
  const env = { ...process.env, ...(opts.env ?? {}) };
  const r = win ? spawnSync([/\s/.test(cmd) ? `"${cmd}"` : cmd, ...cmdArgs].join(" "), { cwd: opts.cwd ?? root, stdio: "inherit", shell: true, env })       // Windows needs a shell for .cmd files; every argument is a constant of this script
                : spawnSync(cmd, cmdArgs, { cwd: opts.cwd ?? root, stdio: "inherit", env });
  if (r.status !== 0) fail(`${cmd} ${cmdArgs.join(" ")} failed (exit ${r.status})`, r.status || 1);
}

// ---- 1. mode and server address
const devHosts = (value("--dev-server") ?? "").split(",").map((h) => h.trim()).filter(Boolean);
const dev = devHosts.length > 0;
if (!dev && cfg.devCleartextHosts.length) console.warn(`NOTE: devCleartextHosts (${cfg.devCleartextHosts.join(", ")}) in the config is ignored: a normal build allows https only. Use --dev-server for a development build.`);
const rawBase = (value("--api-base") ?? process.env.COSMO_API_BASE ?? cfg.apiBase ?? "").trim();
let apiBase = "";
if (rawBase) {
  let u;
  try { u = new URL(rawBase); } catch { fail(`The API base URL "${rawBase}" is not a valid address.`); }
  if (u.username || u.password) fail("The API base URL must not contain a user name or password.");
  if (u.pathname !== "/" || u.search || u.hash) fail("The API base URL must be an origin only (https://host[:port]), without a path or query.");
  if (dev) {
    if (!(u.protocol === "http:" || u.protocol === "https:") || !devHosts.includes(u.hostname) && DEV_HOST.test(u.hostname)) fail(`With --dev-server the API host must be one of the listed hosts (${devHosts.join(", ")}) or a public https host.`);
  } else {
    if (u.protocol !== "https:") fail(`The API base URL must be https:// (got ${u.protocol}//). A standalone build never talks to a server over plain http. For a backend on your own computer use --dev-server.`);
    if (DEV_HOST.test(u.hostname) || u.hostname === "10.0.2.2") fail(`The API base URL points at ${u.hostname}, a development address (localhost, loopback, 10.0.2.2, a private network or a .local name). Refusing to bake it into a standalone app. Deploy the backend and use its public https address, or build with --dev-server for development only.`);
  }
  apiBase = u.origin;
}

console.log("\n=== Cosmo Experts AI Android build ===");
console.log(`mode              : ${dev ? "DEVELOPMENT (not for clients)" : "standalone (production configuration)"}`);
console.log(`API base URL      : ${apiBase || "none configured: the app opens, shows its screens and a preview mode, and asks for a server address"}`);
console.log(`plain http allowed: ${dev ? devHosts.join(", ") : "no (https only)"}`);
if (dev) console.warn("WARNING: development build. It can talk to the listed hosts over plain http. Do not distribute it.");
if (!apiBase && !dev) console.warn("NOTE: no production API base URL is configured. Deploy the backend over https and rebuild with COSMO_API_BASE=https://<your server> to bake its address in.");

// ---- 2. static export of the real UI
console.log("\n== 1/4 static export");
fs.rmSync(path.join(root, "out-android"), { recursive: true, force: true });
run("npx", ["next", "build"], { env: { NEXT_PUBLIC_TARGET: "android", NEXT_PUBLIC_API_BASE: apiBase, NEXT_PUBLIC_ALLOW_DEV_HTTP: dev ? "1" : "", NEXT_PUBLIC_APP_VERSION: String(cfg.versionName), NEXT_TELEMETRY_DISABLED: "1" } });
if (!fs.existsSync(path.join(root, "out-android", "index.html"))) fail("The export did not produce out-android/index.html", 3);

// ---- 3. nothing from a developer's computer may be inside a standalone bundle
if (!dev) {
  const bad = [];
  const walk = (d) => { for (const e of fs.readdirSync(d, { withFileTypes: true })) { const p = path.join(d, e.name); if (e.isDirectory()) walk(p); else if (/\.(js|html|txt|json|css)$/.test(e.name)) {
    const text = fs.readFileSync(p, "utf8");
    for (const re of [/\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b/g, /\b127\.0\.0\.1\b/g, /\b10\.0\.2\.2\b/g, /https?:\/\/localhost[:\d]*/g]) {
      for (const m of text.matchAll(re)) bad.push(`${path.relative(root, p)}: ${text.slice(Math.max(0, m.index - 30), m.index + m[0].length + 20).replace(/\s+/g, " ")}`);
    } } } };
  walk(path.join(root, "out-android"));
  if (bad.length) { console.error("\nA standalone bundle must not contain development addresses, but the export contains:\n  " + bad.slice(0, 12).join("\n  ")); fail("Remove the development address (or build with --dev-server).", 5); }
  console.log("guard: no localhost, loopback, 10.0.2.2 or private-network address in the exported app");
}

// ---- 4. the native project (created once, then kept in Git)
console.log("\n== 2/4 Android project");
if (!fs.existsSync(path.join(androidDir, "app", "build.gradle"))) run("npx", ["cap", "add", "android"]);

// The debug network configuration is either the strict default (no file: https only) or, for a development build only, a generated list of the hosts allowed over plain http.
const dbgXml = path.join(androidDir, "app", "src", "debug", "res", "xml");
const dbgFile = path.join(dbgXml, "network_security_config.xml");
if (dev) {
  fs.mkdirSync(dbgXml, { recursive: true });
  fs.writeFileSync(dbgFile, `<?xml version="1.0" encoding="utf-8"?>
<!-- GENERATED by frontend/scripts/build-android.mjs --dev-server. DEVELOPMENT BUILD ONLY: never distribute an app built with this file. -->
<network-security-config>
    <base-config cleartextTrafficPermitted="false">
        <trust-anchors><certificates src="system" /></trust-anchors>
    </base-config>
    <domain-config cleartextTrafficPermitted="true">
${devHosts.map((h) => `        <domain includeSubdomains="false">${h}</domain>`).join("\n")}
    </domain-config>
</network-security-config>
`);
} else if (fs.existsSync(dbgFile)) {
  fs.rmSync(dbgFile);                                     // a standalone build uses the strict main configuration: https only
}

console.log("\n== 3/4 Capacitor sync");
run("npx", ["cap", "sync", "android"], { env: { COSMO_ANDROID_SCHEME: dev ? "http" : String(cfg.androidScheme || "https") } });

if (flag("--apk")) {
  console.log("\n== 4/4 Gradle assembleDebug");
  run(path.join(androidDir, win ? "gradlew.bat" : "gradlew"), ["assembleDebug", "--console=plain", `-PcosmoVersionName=${cfg.versionName}`, `-PcosmoVersionCode=${cfg.versionCode}`], { cwd: androidDir });
  const dir = path.join(androidDir, "app", "build", "outputs", "apk", "debug");
  const apks = fs.existsSync(dir) ? fs.readdirSync(dir).filter((f) => f.endsWith(".apk")) : [];
  if (!apks.length) fail(`No APK found in ${dir}`, 4);
  for (const a of apks) {
    const f = path.join(dir, a);
    const sha = crypto.createHash("sha256").update(fs.readFileSync(f)).digest("hex");
    console.log(`\nAPK: ${f}\n     ${fs.statSync(f).size} bytes (${(fs.statSync(f).size / 1048576).toFixed(1)} MB), sha256 ${sha}\n     ${dev ? "DEVELOPMENT build (plain http to " + devHosts.join(", ") + ")" : "standalone build, https only"}; API base: ${apiBase || "none configured"}`);
  }
} else {
  console.log("\n== 4/4 skipped (no --apk). Build the APK with:  node scripts/build-android.mjs --apk");
}
