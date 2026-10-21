// Builds the Android app from the real UI:  static export  ->  Capacitor sync  ->  APK.
//
//   node scripts/build-android.mjs --release [--api-base https://api.example.com] [--web-origin https://app.example.com] [--allow-unconfigured]
//        PRODUCTION. A SIGNED release APK (dedicated release keystore, never debug-signed) with ONE fixed https server compiled in, no server-address screen, no preview mode and
//        no developer override. Refuses to build without the production API base URL. Verified afterwards by scripts/verify-apk.mjs.
//   node scripts/build-android.mjs [--apk] [--api-base https://api.example.com] [--no-local]
//        INTERNAL / DEVELOPER debug build of the same bundle: debug-signed, keeps the server dialog and the labelled offline preview. Not for clients.
//   node scripts/build-android.mjs --dev-server 10.0.2.2 [--apk]
//        DEVELOPMENT build only (see below).
//   add --export-only to any of them to stop after the bundle has been built, checked and synced (no Gradle, no signing key needed).
//
// The server address (the production API base URL) is  --api-base https://...   or   COSMO_API_BASE=https://...   or   "apiBase" in android.config.json  (in that order of precedence).
// A release build reads android.config.json only: your personal, git-ignored android.config.local.json can never leak into it.
// The optional web origin (--web-origin / COSMO_WEB_ORIGIN / "webOrigin") is the public https page that hosts the password-reset form; without it the app tells people to ask the clinic.
//
// Every build REFUSES to bake in a development address: plain http://, localhost, loopback, 10.0.2.2, private-network or .local addresses fail the build, and the Android network
// configuration allows https only. --dev-server <host[,host...]> is the explicit, loudly labelled opt-in for developers testing against a backend on their own computer: it allows plain
// http to exactly those hosts in this build, and the result must never be given to clients.
//
// Needs JAVA_HOME (JDK 21) and ANDROID_HOME for the APK. A release build also needs the release keystore: node scripts/make-release-keystore.mjs (once). See docs/PRODUCTION_RELEASE.md.
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const androidDir = path.resolve(root, "..", "android");
const win = process.platform === "win32";
const args = process.argv.slice(2);
const flag = (name) => args.includes(name);
const value = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const read = (f) => (fs.existsSync(path.join(root, f)) ? JSON.parse(fs.readFileSync(path.join(root, f), "utf8")) : {});
const fail = (msg, code = 2) => { console.error(`\nERROR: ${msg}`); process.exit(code); };

const release = flag("--release");
const exportOnly = flag("--export-only");          // build and check the bundle (and sync it into the Android project) but do not run Gradle: no signing key is needed
const devHosts = (value("--dev-server") ?? "").split(",").map((h) => h.trim()).filter(Boolean);
const dev = devHosts.length > 0;
if (release && (dev || flag("--apk") || flag("--no-local"))) fail("--release cannot be combined with --dev-server, --apk or --no-local (a release build is always standalone, signed, and ignores local configuration).");

const cfg = { appId: "ai.cosmoexperts.app", appName: "Cosmo Experts AI", versionName: "1.0.0", versionCode: 3, apiBase: "", webOrigin: "", androidScheme: "https", devCleartextHosts: [],
  ...read("android.config.json"), ...(release || flag("--no-local") ? {} : read("android.config.local.json")) };

const DEV_HOST = /^(localhost|127\.\d+\.\d+\.\d+|0\.0\.0\.0|\[?::1\]?|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|169\.254\.\d+\.\d+|[^.]+\.local|[^.]+\.internal|[^.]+\.lan)$/i;

function run(cmd, cmdArgs, opts = {}) {
  const env = { ...process.env, ...(opts.env ?? {}) };
  const r = win ? spawnSync([/\s/.test(cmd) ? `"${cmd}"` : cmd, ...cmdArgs].join(" "), { cwd: opts.cwd ?? root, stdio: "inherit", shell: true, env })       // Windows needs a shell for .cmd files; every argument is a constant of this script
                : spawnSync(cmd, cmdArgs, { cwd: opts.cwd ?? root, stdio: "inherit", env });
  if (r.status !== 0) fail(`${cmd} ${cmdArgs.join(" ")} failed (exit ${r.status})`, r.status || 1);
}

/** An https origin of a public server (no path, query, credentials, or development host). */
function publicOrigin(raw, what) {
  let u;
  try { u = new URL(raw); } catch { fail(`The ${what} "${raw}" is not a valid address.`); }
  if (u.username || u.password) fail(`The ${what} must not contain a user name or password.`);
  if (u.pathname !== "/" || u.search || u.hash) fail(`The ${what} must be an origin only (https://host[:port]), without a path or query.`);
  if (u.protocol !== "https:") fail(`The ${what} must be https:// (got ${u.protocol}//). An app never talks to a server over plain http. For a backend on your own computer use --dev-server.`);
  if (DEV_HOST.test(u.hostname) || u.hostname === "10.0.2.2") fail(`The ${what} points at ${u.hostname}, a development address (localhost, loopback, 10.0.2.2, a private network or a .local name). Refusing to bake it into an app. Deploy the backend and use its public https address, or build with --dev-server for development only.`);
  return u.origin;
}

// ---- 1. mode and server address
if (!dev && cfg.devCleartextHosts.length) console.warn(`NOTE: devCleartextHosts (${cfg.devCleartextHosts.join(", ")}) in the config is ignored: a normal build allows https only. Use --dev-server for a development build.`);
const rawBase = (value("--api-base") ?? process.env.COSMO_API_BASE ?? cfg.apiBase ?? "").trim();
const rawWeb = (value("--web-origin") ?? process.env.COSMO_WEB_ORIGIN ?? cfg.webOrigin ?? "").trim();
let apiBase = "";
if (rawBase) {
  let u;
  try { u = new URL(rawBase); } catch { fail(`The API base URL "${rawBase}" is not a valid address.`); }
  if (dev) {
    if (u.username || u.password) fail("The API base URL must not contain a user name or password.");
    if (u.pathname !== "/" || u.search || u.hash) fail("The API base URL must be an origin only (https://host[:port]), without a path or query.");
    if (!(u.protocol === "http:" || u.protocol === "https:") || !devHosts.includes(u.hostname) && DEV_HOST.test(u.hostname)) fail(`With --dev-server the API host must be one of the listed hosts (${devHosts.join(", ")}) or a public https host.`);
    apiBase = u.origin;
  } else {
    apiBase = publicOrigin(rawBase, "API base URL");
  }
}
const webOrigin = rawWeb && !dev ? publicOrigin(rawWeb, "web origin") : "";
const unconfiguredOk = flag("--allow-unconfigured");
if (release && !apiBase && !unconfiguredOk) {
  fail("A release build needs the production API base URL, and none is configured.\n"
    + "  Deploy the backend over https first (docs/PRODUCTION_RELEASE.md), then build with\n"
    + "    $env:COSMO_API_BASE = \"https://<your production API host>\"; node scripts\\build-android.mjs --release\n"
    + "  (or set \"apiBase\" in frontend/android.config.json). No address is ever invented. To exercise the signing pipeline WITHOUT a server, add --allow-unconfigured: the result is named\n"
    + "  ...-UNCONFIGURED.apk, shows \"This app isn't connected yet\", and must not be given to anyone.");
}
if (release && !apiBase) console.warn("\nWARNING: --allow-unconfigured: this release has NO server address. It exists only to verify signing and packaging. Do not distribute it.");

const versionName = release ? String(cfg.versionName) : `${cfg.versionName}-${dev ? "dev" : "debug"}`;
console.log("\n=== Cosmo Experts AI Android build ===");
console.log(`mode              : ${release ? "RELEASE (production, signed)" : dev ? "DEVELOPMENT (not for clients)" : "internal debug build (developer tools, debug-signed, not for clients)"}`);
console.log(`version           : ${versionName} (code ${cfg.versionCode})`);
console.log(`API base URL      : ${apiBase || (release ? "NONE (--allow-unconfigured)" : "none configured: the app opens, shows its screens and a preview mode, and asks for a server address")}`);
console.log(`web origin (reset): ${webOrigin || "none: password reset is by the clinic"}`);
console.log(`plain http allowed: ${dev ? devHosts.join(", ") : "no (https only)"}`);
if (dev) console.warn("WARNING: development build. It can talk to the listed hosts over plain http. Do not distribute it.");
if (!apiBase && !dev && !release) console.warn("NOTE: no production API base URL is configured. Deploy the backend over https and rebuild with COSMO_API_BASE=https://<your server> to bake its address in.");

// ---- 2. release signing (read before the long build so a missing key fails immediately; passwords reach Gradle only through the environment)
let signingEnv = {};
if (release && !exportOnly) {
  const propsFile = process.env.COSMO_SIGNING_PROPERTIES || path.join(os.homedir(), ".cosmo-signing", "release-signing.properties");
  if (!fs.existsSync(propsFile)) fail(`The release signing configuration was not found at ${propsFile}.\n  Create the release key once with:  node scripts/make-release-keystore.mjs\n  (or point COSMO_SIGNING_PROPERTIES at your properties file). The key is never created by this script and never stored in Git.`);
  const props = Object.fromEntries(fs.readFileSync(propsFile, "utf8").split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith("#") && l.includes("=")).map((l) => [l.slice(0, l.indexOf("=")).trim(), l.slice(l.indexOf("=") + 1).trim()]));
  for (const k of ["storeFile", "storePassword", "keyAlias", "keyPassword"]) if (!props[k]) fail(`${propsFile} has no "${k}".`);
  if (!fs.existsSync(props.storeFile)) fail(`The release keystore ${props.storeFile} named in ${propsFile} does not exist.`);
  if (/debug/i.test(props.storeFile) || /androiddebugkey/i.test(props.keyAlias)) fail("The configured key looks like a debug key. A release build is never debug-signed.");
  signingEnv = { COSMO_KEYSTORE_FILE: props.storeFile, COSMO_KEYSTORE_PASSWORD: props.storePassword, COSMO_KEY_ALIAS: props.keyAlias, COSMO_KEY_PASSWORD: props.keyPassword };
  console.log(`signing key       : ${path.basename(props.storeFile)} (alias ${props.keyAlias}); passwords stay in the environment`);
}

// ---- 3. static export of the real UI
console.log("\n== 1/5 static export");
fs.rmSync(path.join(root, "out-android"), { recursive: true, force: true });
run("npx", ["next", "build"], { env: {
  NEXT_PUBLIC_TARGET: "android", NEXT_PUBLIC_RELEASE: release ? "1" : "", NEXT_PUBLIC_API_BASE: apiBase, NEXT_PUBLIC_WEB_ORIGIN: webOrigin,
  NEXT_PUBLIC_ALLOW_DEV_HTTP: dev ? "1" : "", NEXT_PUBLIC_APP_VERSION: versionName, NEXT_TELEMETRY_DISABLED: "1" } });
const outDir = path.join(root, "out-android");
if (!fs.existsSync(path.join(outDir, "index.html"))) fail("The export did not produce out-android/index.html", 3);

// ---- 4. nothing from a developer's computer (and, in a release, no developer tool) may be inside the bundle
const bundleFiles = [];
const walk = (d) => { for (const e of fs.readdirSync(d, { withFileTypes: true })) { const p = path.join(d, e.name); if (e.isDirectory()) walk(p); else if (/\.(js|html|txt|json|css)$/.test(e.name)) bundleFiles.push(p); } };
walk(outDir);
if (!dev) {
  const bad = [];
  for (const p of bundleFiles) {
    const text = fs.readFileSync(p, "utf8");
    for (const re of [/\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b/g, /\b127\.0\.0\.1\b/g, /\b10\.0\.2\.2\b/g, /https?:\/\/localhost[:\d]*/g]) {
      for (const m of text.matchAll(re)) bad.push(`${path.relative(root, p)}: ${text.slice(Math.max(0, m.index - 30), m.index + m[0].length + 20).replace(/\s+/g, " ")}`);
    }
  }
  if (bad.length) { console.error("\nA bundle must not contain development addresses, but the export contains:\n  " + bad.slice(0, 12).join("\n  ")); fail("Remove the development address (or build with --dev-server).", 5); }
  console.log("guard: no localhost, loopback, 10.0.2.2 or private-network address in the exported app");
}
if (release) {
  const text = bundleFiles.map((p) => fs.readFileSync(p, "utf8")).join("\n");
  const present = ["Set the server address", "Look around in preview mode", "Change server", "Test connection", "Patient (Demo One)", "Local development mode", "PREVIEW · no server", "Developer build", "for example https://api.example.com", "the server address in Settings", "Preview mode: no server"].filter((s) => text.includes(s));
  if (present.length) fail(`The release bundle still contains developer-only features (${present.join("; ")}). They must be compiled out of a production app.`, 5);
  if (apiBase && !text.includes(apiBase)) fail(`The production API base URL ${apiBase} was not found in the exported app: it is not compiled in.`, 5);
  console.log(`guard: no server dialog, preview mode or demo account in the release bundle; ${apiBase ? `API base ${apiBase} is compiled in` : "no API base (unconfigured build)"}`);
}

// ---- 5. the native project (created once, then kept in Git)
console.log("\n== 2/5 Android project");
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

console.log("\n== 3/5 Capacitor sync");
run("npx", ["cap", "sync", "android"], { env: { COSMO_ANDROID_SCHEME: dev ? "http" : String(cfg.androidScheme || "https"), COSMO_RELEASE: release ? "1" : "" } });
if (release) {
  const cc = JSON.parse(fs.readFileSync(path.join(androidDir, "app", "src", "main", "assets", "capacitor.config.json"), "utf8"));
  if (cc.server?.url || cc.server?.cleartext) fail("capacitor.config.json in the release app points at a server URL or allows cleartext.", 5);
  if (cc.android?.webContentsDebuggingEnabled !== false || cc.android?.loggingBehavior !== "none") fail("capacitor.config.json in the release app does not disable web-view debugging and logging.", 5);
  console.log("guard: the release app has no server.url, no cleartext, no web-view debugging and no web-view logging");
}

// ---- 6. the APK
const wantApk = (release || flag("--apk")) && !exportOnly;
if (wantApk) {
  const task = release ? "assembleRelease" : "assembleDebug";
  console.log(`\n== 4/5 Gradle ${task}`);
  run(path.join(androidDir, win ? "gradlew.bat" : "gradlew"), [task, "--console=plain", `-PcosmoVersionName=${versionName}`, `-PcosmoVersionCode=${cfg.versionCode}`], { cwd: androidDir, env: signingEnv });
  const dir = path.join(androidDir, "app", "build", "outputs", "apk", release ? "release" : "debug");
  const apks = fs.existsSync(dir) ? fs.readdirSync(dir).filter((f) => f.endsWith(".apk")) : [];
  if (!apks.length) fail(`No APK found in ${dir}`, 4);
  if (release && apks.some((a) => /unsigned/i.test(a)) && !apks.some((a) => /release\.apk$/i.test(a))) fail(`Only an unsigned APK was produced in ${dir}.`, 4);
  for (const a of apks) {
    let f = path.join(dir, a);
    if (release && !apiBase) { const renamed = f.replace(/\.apk$/, "-UNCONFIGURED.apk"); fs.renameSync(f, renamed); f = renamed; }
    const sha = crypto.createHash("sha256").update(fs.readFileSync(f)).digest("hex");
    console.log(`\nAPK: ${f}\n     ${fs.statSync(f).size} bytes (${(fs.statSync(f).size / 1048576).toFixed(1)} MB), sha256 ${sha}\n     ${release ? "RELEASE build, signed with the release key" : dev ? "DEVELOPMENT build (plain http to " + devHosts.join(", ") + ")" : "internal debug build, debug-signed"}; API base: ${apiBase || "none configured"}`);
    if (release) {
      console.log("\n== 5/5 verification of the APK");
      run(process.execPath, [path.join(root, "scripts", "verify-apk.mjs"), f, "--release", ...(apiBase ? ["--api-base", apiBase] : ["--unconfigured"]), "--version-name", versionName, "--version-code", String(cfg.versionCode)]);
    }
  }
} else {
  console.log(exportOnly ? "\n== 4/5 skipped (--export-only): the bundle is built, checked and synced; no APK was made." : "\n== 4/5 skipped (no --apk). Build the APK with:  node scripts/build-android.mjs --apk   (internal)  or  --release  (production)");
}
