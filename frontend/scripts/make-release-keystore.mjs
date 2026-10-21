// Creates the dedicated RELEASE signing key for the Android app. Run it ONCE, on the machine that builds releases:
//
//   node scripts/make-release-keystore.mjs [--dir <folder>] [--alias cosmo-release] [--dname "CN=..., O=..., C=IN"] [--validity-days 10950]
//
// It writes two files into a private folder (default: ~/.cosmo-signing, readable only by you) and prints only their paths and the PUBLIC certificate fingerprint:
//     cosmo-release.p12            the keystore (RSA 4096, PKCS12, valid about 30 years)
//     release-signing.properties   the keystore path, alias and a random 32-character password, read by scripts/build-android.mjs --release
// Neither file belongs in Git (they are ignored), and neither is ever printed. The password reaches keytool through an environment variable, not a command line.
//
// THE KEY IS THE IDENTITY OF THE APP. Every future update must be signed with this same key, or phones will refuse to install it over the old version. If the key is lost it cannot be recovered:
// back up BOTH files now (docs/PRODUCTION_RELEASE.md, "Back up the signing key"). This script refuses to replace an existing key.
import { spawnSync } from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const win = process.platform === "win32";
const args = process.argv.slice(2);
const value = (name) => { const i = args.indexOf(name); return i >= 0 ? args[i + 1] : undefined; };
const fail = (msg) => { console.error(`\nERROR: ${msg}`); process.exit(2); };

const dir = path.resolve(value("--dir") ?? path.join(os.homedir(), ".cosmo-signing"));
const alias = value("--alias") ?? "cosmo-release";
const dname = value("--dname") ?? "CN=Cosmo Experts AI, OU=Android Release, O=Cosmo Experts, C=IN";
const validityDays = Number(value("--validity-days") ?? 10950);
if (!Number.isInteger(validityDays) || validityDays < 9500) fail("--validity-days must be a whole number of at least 9500 (the key must stay valid well beyond 2033).");
if (!/^[A-Za-z0-9._-]{3,40}$/.test(alias)) fail("--alias may contain only letters, digits, dot, dash and underscore.");
const ksFile = path.join(dir, "cosmo-release.p12");
const propsFile = path.join(dir, "release-signing.properties");
if (fs.existsSync(ksFile) || fs.existsSync(propsFile)) {
  fail(`A release key already exists in ${dir}. It is never replaced: a new key could not update apps that are already installed. Back it up instead (docs/PRODUCTION_RELEASE.md).`);
}

const javaHome = process.env.JAVA_HOME;
const keytool = [javaHome && path.join(javaHome, "bin", win ? "keytool.exe" : "keytool"), win ? "keytool.exe" : "keytool"].filter(Boolean).find((k) => k === "keytool" || k === "keytool.exe" || fs.existsSync(k));
if (!keytool) fail("keytool was not found. Set JAVA_HOME to a JDK (21).");

// A random password from an unambiguous alphabet (rejection sampling: no modulo bias).
const alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789";
let password = "";
while (password.length < 32) { const b = crypto.randomBytes(1)[0]; if (b < 256 - (256 % alphabet.length)) password += alphabet[b % alphabet.length]; }

// A private folder first, so the files inherit its permissions from the moment they exist.
fs.mkdirSync(dir, { recursive: true, mode: 0o700 });
if (win) {
  const me = `${process.env.USERDOMAIN ?? "."}\\${os.userInfo().username}`;
  const acl = spawnSync("icacls", [dir, "/inheritance:r", "/grant:r", `${me}:(OI)(CI)F`], { encoding: "utf8" });
  if (acl.status !== 0) console.warn(`WARNING: could not restrict ${dir} to your account (${(acl.stderr || acl.stdout || "").trim().slice(0, 160)}). Restrict it yourself.`);
}

const env = { ...process.env, COSMO_KS_PASS: password };
const gen = spawnSync(keytool, ["-genkeypair", "-storetype", "PKCS12", "-keystore", ksFile, "-alias", alias, "-keyalg", "RSA", "-keysize", "4096", "-sigalg", "SHA256withRSA",
  "-validity", String(validityDays), "-dname", dname, "-storepass:env", "COSMO_KS_PASS", "-keypass:env", "COSMO_KS_PASS"], { env, encoding: "utf8" });
if (gen.error) { fs.rmSync(ksFile, { force: true }); fail(`keytool could not be started (${gen.error.message}). Set JAVA_HOME to a JDK (21).`); }
if (gen.status !== 0) { fs.rmSync(ksFile, { force: true }); fail(`keytool could not create the key: ${(gen.stderr || gen.stdout || "").replaceAll(password, "<password>").trim().slice(0, 400)}`); }

fs.writeFileSync(propsFile, [
  "# Release signing configuration read by frontend/scripts/build-android.mjs --release.",
  "# SECRET: never commit it, never paste it anywhere. Back it up together with cosmo-release.p12 (docs/PRODUCTION_RELEASE.md).",
  `storeFile=${ksFile.replaceAll("\\", "/")}`,
  `storePassword=${password}`,
  `keyAlias=${alias}`,
  `keyPassword=${password}`,
  "",
].join("\n"), { mode: 0o600 });
if (!win) { fs.chmodSync(ksFile, 0o600); fs.chmodSync(propsFile, 0o600); }

// Read it back: only the public facts are printed.
const list = spawnSync(keytool, ["-list", "-v", "-keystore", ksFile, "-storepass:env", "COSMO_KS_PASS", "-alias", alias], { env, encoding: "utf8" });
if (list.status !== 0) fail("The keystore was created but could not be read back. Do not use it; delete the folder and run this again.");
const out = list.stdout;
const pick = (re) => (out.match(re)?.[1] ?? "").trim();
console.log("\nRelease signing key created.");
console.log(`  folder        : ${dir}`);
console.log(`  keystore      : ${ksFile}`);
console.log(`  properties    : ${propsFile}   (holds the password; not printed)`);
console.log(`  alias         : ${alias}`);
console.log(`  algorithm     : ${pick(/Signature algorithm name:\s*(.+)/)} / ${pick(/Subject Public Key Algorithm:\s*(.+)/)}`);
console.log(`  valid         : ${pick(/Valid from:\s*(.+)/)}`);
console.log(`  SHA-256 (public certificate fingerprint): ${pick(/SHA256:\s*(.+)/)}`);
console.log("\nNEXT: back up the folder NOW (two copies, offline, e.g. an encrypted USB drive and a password manager). If this key is lost, installed apps can no longer be updated.");
