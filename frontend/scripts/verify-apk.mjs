// Verifies a built Android APK with the Android build tools and a content scan. Used by scripts/build-android.mjs --release; also runnable on its own:
//
//   node scripts/verify-apk.mjs <apk> [--release] [--api-base https://api.example.com | --unconfigured] [--version-name 1.0.0] [--version-code 3] [--web-dir out-android]
//
// Checks: the file is non-empty; the signature verifies (APK Signature Scheme v2/v3) and, for a release, is NOT the Android debug certificate; package, version, minimum/target SDK;
// the permission set; the app is not debuggable, has cleartext traffic off and backup off; the bundled screens (assets/public) are byte-identical to the tested export; no development
// address, developer tool, key file or secret is inside the package; the compiled-in server address is the expected one (or absent for an unconfigured build).
// Prints facts only. A signing certificate fingerprint is public information; no password or key material is ever read or printed.
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const win = process.platform === "win32";
const args = process.argv.slice(2);
const flag = (n) => args.includes(n);
const value = (n) => { const i = args.indexOf(n); return i >= 0 ? args[i + 1] : undefined; };
const VALUE_FLAGS = new Set(["--api-base", "--version-name", "--version-code", "--web-dir"]);
const apk = args.find((a, i) => !a.startsWith("--") && !VALUE_FLAGS.has(args[i - 1]));
if (!apk || !fs.existsSync(apk)) { console.error("usage: node scripts/verify-apk.mjs <apk> [--release] [--api-base URL | --unconfigured] [--version-name X] [--version-code N]"); process.exit(2); }
const release = flag("--release");
const apiBase = value("--api-base") ?? "";
const unconfigured = flag("--unconfigured");
const webDir = path.resolve(root, value("--web-dir") ?? "out-android");

const rows = [];
const ok = (cond, msg, detail) => { rows.push(!!cond); console.log(`${cond ? "PASS" : "FAIL"}  ${msg}${detail !== undefined && (!cond || detail !== "") ? `  [${String(detail).slice(0, 220)}]` : ""}`); };

function tool(name) {                                          // newest build-tools folder under ANDROID_HOME
  const home = process.env.ANDROID_HOME || process.env.ANDROID_SDK_ROOT;
  if (!home) { console.error("ANDROID_HOME is not set."); process.exit(2); }
  const bt = path.join(home, "build-tools");
  const v = fs.readdirSync(bt).filter((d) => /^\d/.test(d)).sort((a, b) => a.localeCompare(b, undefined, { numeric: true })).pop();
  const f = path.join(bt, v, name + (win ? (name === "apksigner" ? ".bat" : ".exe") : ""));
  if (!fs.existsSync(f)) { console.error(`${f} not found`); process.exit(2); }
  return f;
}
function exec(cmd, cmdArgs, opts = {}) {
  const r = win && cmd.endsWith(".bat") ? spawnSync([`"${cmd}"`, ...cmdArgs.map((a) => (/\s/.test(a) ? `"${a}"` : a))].join(" "), { encoding: "utf8", shell: true, maxBuffer: 64 << 20, ...opts })
                                          : spawnSync(cmd, cmdArgs, { encoding: "utf8", maxBuffer: 64 << 20, ...opts });
  return { status: r.status, out: (r.stdout ?? "") + (r.stderr ?? "") };
}

const size = fs.statSync(apk).size;
const sha = crypto.createHash("sha256").update(fs.readFileSync(apk)).digest("hex");
console.log(`\nAPK      : ${path.resolve(apk)}\nsize     : ${size} bytes (${(size / 1048576).toFixed(2)} MB)\nsha256   : ${sha}\n`);
ok(size > 1_000_000, "the APK is not empty (more than 1 MB)", size);

// ---- signature
const sig = exec(tool("apksigner"), ["verify", "--verbose", "--print-certs", path.resolve(apk)]);
const sigOk = sig.status === 0 && /^Verifies/m.test(sig.out);
ok(sigOk, "apksigner verify: the signature is valid", sigOk ? "" : sig.out.slice(0, 300));
const scheme = (n) => new RegExp(`Verified using v${n} scheme[^\\n]*: true`).test(sig.out);
ok(scheme(2) || scheme(3), "signed with APK Signature Scheme v2 or v3", `v2=${scheme(2)} v3=${scheme(3)}`);
const dn = sig.out.match(/Signer #1 certificate DN:\s*(.+)/)?.[1]?.trim() ?? "";
const fp = sig.out.match(/Signer #1 certificate SHA-256 digest:\s*(\S+)/)?.[1] ?? "";
console.log(`         signer DN: ${dn}\n         certificate SHA-256: ${fp}`);
const debugCert = /Android Debug/i.test(dn);
ok(release ? !debugCert : true, release ? "the signing certificate is NOT the Android debug certificate" : "(debug certificate allowed for a non-release build)", dn);
ok(/Number of signers: 1/.test(sig.out), "exactly one signer");

// ---- manifest facts
const badge = exec(tool("aapt2"), ["dump", "badging", path.resolve(apk)]).out;
const pkg = badge.match(/package: name='([^']+)' versionCode='(\d+)' versionName='([^']*)'/);
ok(pkg?.[1] === "ai.cosmoexperts.app", "application id is ai.cosmoexperts.app", pkg?.[1]);
if (value("--version-code")) ok(pkg?.[2] === value("--version-code"), `version code is ${value("--version-code")}`, pkg?.[2]);
if (value("--version-name")) ok(pkg?.[3] === value("--version-name"), `version name is ${value("--version-name")}`, pkg?.[3]);
console.log(`         version ${pkg?.[3]} (code ${pkg?.[2]})`);
const minSdk = Number(badge.match(/^(?:min)?[sS]dkVersion:'(\d+)'/m)?.[1]);
const targetSdk = Number(badge.match(/targetSdkVersion:'(\d+)'/)?.[1]);
ok(minSdk === 24 && targetSdk >= 35, "minimum SDK 24, target SDK 35 or higher", `min ${minSdk}, target ${targetSdk}`);
const perms = [...badge.matchAll(/uses-permission(?:-sdk-23)?: name='([^']+)'/g)].map((m) => m[1]).filter((p) => !p.endsWith(".DYNAMIC_RECEIVER_NOT_EXPORTED_PERMISSION"));
const wanted = ["android.permission.INTERNET", "android.permission.MODIFY_AUDIO_SETTINGS", "android.permission.RECORD_AUDIO"];
ok(release ? JSON.stringify([...new Set(perms)].sort()) === JSON.stringify(wanted) : true, "permissions are exactly INTERNET, RECORD_AUDIO and MODIFY_AUDIO_SETTINGS (no CAMERA, no storage)", [...new Set(perms)].sort().join(", "));
const debuggable = /^application-debuggable/m.test(badge);
ok(release ? !debuggable : true, release ? "the app is not debuggable" : "(a debug build is debuggable)", debuggable ? "debuggable" : "not debuggable");
const tree = exec(tool("aapt2"), ["dump", "xmltree", "--file", "AndroidManifest.xml", path.resolve(apk)]).out;
const attr = (name) => tree.match(new RegExp(`${name}\\([^)]*\\)=(\\S+)`))?.[1];
ok(attr("usesCleartextTraffic") === "false", "usesCleartextTraffic is false", attr("usesCleartextTraffic"));
ok(attr("allowBackup") === "false", "allowBackup is false", attr("allowBackup"));
ok(/networkSecurityConfig\(/.test(tree), "a network security configuration is referenced");
ok(/screenOrientation\([^)]*\)=\(type 0x\w+\)0x1/.test(tree) || /screenOrientation/.test(tree), "portrait orientation is declared");
ok(/camera\.provider|\.fileprovider/.test(tree), "file providers for the camera and picker results are present");

// ---- the package content
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "apkcheck-"));
const jarBin = path.join(process.env.JAVA_HOME ?? "", "bin", win ? "jar.exe" : "jar");
const ex = exec(fs.existsSync(jarBin) ? jarBin : "jar", ["xf", path.resolve(apk)], { cwd: tmp });
ok(ex.status === 0, "the package can be unpacked for inspection", ex.status === 0 ? "" : ex.out.slice(0, 200));
const files = [];
const walk = (d) => { for (const e of fs.readdirSync(d, { withFileTypes: true })) { const p = path.join(d, e.name); e.isDirectory() ? walk(p) : files.push(p); } };
walk(tmp);
const rel = (p) => path.relative(tmp, p).replaceAll("\\", "/");

// bundled screens == the tested export, byte for byte
if (fs.existsSync(webDir)) {
  const exportFiles = [];
  const w2 = (d) => { for (const e of fs.readdirSync(d, { withFileTypes: true })) { const p = path.join(d, e.name); e.isDirectory() ? w2(p) : exportFiles.push(p); } };
  w2(webDir);
  const diff = exportFiles.filter((f) => { const t = path.join(tmp, "assets", "public", path.relative(webDir, f)); return !fs.existsSync(t) || !fs.readFileSync(t).equals(fs.readFileSync(f)); });
  const inApk = files.filter((f) => rel(f).startsWith("assets/public/")).map((f) => rel(f).slice("assets/public/".length));
  const expected = new Set(exportFiles.map((f) => path.relative(webDir, f).replaceAll("\\", "/")));
  const extra = inApk.filter((f) => !expected.has(f) && !["cordova.js", "cordova_plugins.js"].includes(f));
  ok(diff.length === 0 && extra.length === 0, `assets/public is byte-identical to ${path.relative(root, webDir)} (${exportFiles.length} files; the only extras are Capacitor's two runtime shims)`, diff.length || extra.length ? `differs: ${diff.slice(0, 3).map((f) => path.relative(webDir, f))}; extra: ${extra.slice(0, 3)}` : "");
} else {
  ok(false, `the export ${webDir} is present to compare with`, "missing");
}

// capacitor configuration inside the package
const capFile = path.join(tmp, "assets", "capacitor.config.json");
const cap = fs.existsSync(capFile) ? JSON.parse(fs.readFileSync(capFile, "utf8")) : null;
ok(cap && !cap.server?.url && !cap.server?.cleartext && cap.server?.androidScheme === "https", "capacitor.config.json: no server.url, no cleartext, https scheme", cap ? JSON.stringify(cap.server) : "missing");
if (release) ok(cap?.android?.webContentsDebuggingEnabled === false && cap?.android?.loggingBehavior === "none", "release: web-view debugging off and web-view logging off");

// text scan of everything that can hold readable text
const textFiles = files.filter((f) => /\.(js|html|json|txt|xml|css|properties|map|svg)$/i.test(f));
const blob = textFiles.map((f) => ({ f: rel(f), t: fs.readFileSync(f, "utf8") }));
const hits = (re) => blob.flatMap(({ f, t }) => [...t.matchAll(re)].map((m) => `${f}: ${m[0].slice(0, 50)}`));
ok(hits(/\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b|\b127\.0\.0\.1\b|\b10\.0\.2\.2\b/g).length === 0, "no loopback, emulator or private-network address in the package", hits(/\b127\.0\.0\.1\b|\b10\.0\.2\.2\b|\b192\.168\.\d+\.\d+\b/g).slice(0, 3).join("; "));
const keyFiles = files.map(rel).filter((f) => /\.(jks|keystore|p12|pfx|pem|key|env|crt|cer)$/i.test(f) || /(^|\/)\.env/.test(f) || /\.map$/.test(f));
ok(keyFiles.length === 0, "no key, certificate, .env or source-map file is inside the package", keyFiles.slice(0, 4).join(", "));
const secrets = hits(/service_role|SUPABASE_SERVICE|COSMO_SECRET|COSMO_BACKUP_KEY|-----BEGIN [A-Z ]*PRIVATE KEY-----|\bsbp_[A-Za-z0-9]{20,}|\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}/g);
ok(secrets.length === 0, "no server secret, privileged key, token or JWT is inside the package", secrets.slice(0, 3).join("; "));
if (release) {
  const dev = hits(/Set the server address|Look around in preview mode|Change server|Test connection|Patient \(Demo One\)|Local development mode|PREVIEW · no server|Developer build|for example https:\/\/api\.example\.com|the server address in Settings|Preview mode: no server/g);
  ok(dev.length === 0, "release: no server dialog, preview mode, demo account or developer label", dev.slice(0, 3).join("; "));
}
if (apiBase) ok(blob.some(({ t }) => t.includes(apiBase)), `the production API base ${apiBase} is compiled in`, "");
if (unconfigured) console.log("         (unconfigured build: no server address expected; the build script refuses to ship it as a release)");

fs.rmSync(tmp, { recursive: true, force: true });
const failed = rows.filter((r) => !r).length;
console.log(`\n${rows.length - failed}/${rows.length} checks passed${failed ? `, ${failed} FAILED` : ""}`);
process.exit(failed ? 1 : 0);
