# Android app: standalone debug APK

The Android app is the real Cosmo Experts web UI (the Next.js app in `frontend/`) packaged with Capacitor. **It does not need the development computer.** The screens, styles, routing and the app shell are
inside the APK; it opens from its icon with no `npm run dev`, no server on a PC and, for the screens themselves, no internet. Data, sign-in and AI answers come from **one explicit https server address** (the
production API base URL); until a backend is deployed and that address is set, the app opens, shows its screens and a labelled preview, and says plainly that sign-in needs the server.
**To use the whole app before anything is deployed, start the demo server on this computer (section 5a): one command gives the app a real backend over a secure https address.**

```
frontend/ (Next.js)  --static export-->  frontend/out-android  --Capacitor sync-->  android/ (Gradle)  --assembleDebug-->  cosmo-experts-ai-debug.apk
phone (APK: screens inside)  --https only-->  your deployed Cosmo Experts backend (FastAPI)           (no PC, no LAN address, no localhost)
```

## 1. What was wrong, and what changed

The first APK already carried the UI, but its build had picked up **this computer's Wi-Fi address as the default server** (from a personal, git-ignored `android.config.local.json`) and the debug network
configuration allowed plain http to it. Everything that needs data, including sign-in, therefore went to the PC. Fixed at the root:

* **No developer address can be baked in.** A normal build (`node scripts/build-android.mjs`) fails if the server address is plain http, `localhost`, loopback, `10.0.2.2`, a private-network or `.local` address, then scans the exported
  app for such addresses (none found) and uses an https-only Android network configuration. The old local config with the PC's address was deleted.
* **No silent fallback.** If no server address is configured the app says so; a development address typed in the app is refused; one found in the phone's storage (an older build) is ignored and erased.
* **The single production setting is `COSMO_API_BASE`** (or `--api-base`, or `apiBase` in `frontend/android.config.json`). `--dev-server <host>` is the explicit, loudly labelled opt-in for developers; its APK must never go to clients.
* **Honest offline and server-down states**: every patient screen shows "We can't connect right now" with Try again, a banner appears on every screen when the server cannot be reached, the sign-in screen offers Try again, Change server and
  Preview, and an offline banner appears in airplane mode. Android Back closes an open dialog, goes back, and exits only from the first screen.

## 2. What works without the PC, and what needs the cloud backend

| Works with the PC off and no connection (inside the APK) | Needs the deployed https backend |
|---|---|
| Launch from the icon, splash screen, status/navigation bars, keyboard handling, safe areas, Android Back | Sign-in (the app lists the backend's accounts; there is no OTP or onboarding flow in this project) |
| All patient screens open: Home, Photo capture, Adjust, Check, Reports, Report page, Ask AI, History, Care plan, Quotation, Privacy and consent, Settings | Loading and saving reports, photos, history, care plan, quotations and consent |
| Camera capture, choosing and adjusting a photo on the phone | The photo quality check, reading a report, AI answers, spoken answers and voice questions |
| Offline / server-unavailable states with retry; **preview mode** (real screens, empty states, a permanent PREVIEW banner, no request, no invented data) | Everything the clinician and administrator screens do |
| Settings: change the server address (https only) | |

Experimental research models (Phase 5) are not part of the app and are never used for patient-facing predictions. Nothing is simulated: no fake login, answer, analysis or patient record exists anywhere in the app.

## 3. Build (one command)

Needs Node 22+, JDK 21 (`JAVA_HOME`), the Android SDK with platform 36 and build-tools 36 (`ANDROID_HOME`); Gradle comes with the wrapper. PowerShell:

```
cd "F:\Cosmo Experts\frontend"
$env:COSMO_API_BASE = "https://api.your-domain.com"      # the deployed backend. Leave this line out to build an app that opens and asks for the address
node scripts\build-android.mjs --apk
```

Output: `F:\Cosmo Experts\android\app\build\outputs\apk\debug\cosmo-experts-ai-debug.apk` (debug-signed). The first Gradle run downloads dependencies (about 4 minutes on a small laptop), later runs about a minute. The script prints the
size, the SHA-256, the mode and the API base that went in. Set the version in `frontend/android.config.json` (`versionName`, `versionCode`; raise `versionCode` for every build you give to someone).

## 4. Install and check on a phone

```
adb devices                                               # the phone must be listed (USB debugging on)
adb install -r "F:\Cosmo Experts\android\app\build\outputs\apk\debug\cosmo-experts-ai-debug.apk"
adb shell am start -n ai.cosmoexperts.app/.MainActivity
adb logcat -d -s Capacitor:V Capacitor/Console:V chromium:E AndroidRuntime:E     # startup crashes, WebView errors, missing assets
```

Without a cable: copy the file to the phone and open it (Android asks once to allow installing from that source). To test the independence yourself: stop the PC server, switch the phone to airplane mode, open the app from its icon, and walk
through the screens with "Look around in preview mode".

## 5. A server to talk to: a demo today, a real deployment later

The app is only the screens. Sign-in, reports, photos and the AI conversation are done by the backend, so a running backend with an https address is needed before anything beyond the preview works.

### 5a. Demo today: this computer plus a secure tunnel (about two minutes, nothing to deploy)

```
powershell -ExecutionPolicy Bypass -File "F:\Cosmo Experts\ops\windows\start-cosmo-demo.ps1"
```

The first run downloads Cloudflare's tunnel program once (a pinned release whose SHA-256 is checked, into `F:\tools\cloudflared`), asks you to choose a demo password (at least 10 characters), creates a fresh demo database in
`F:\cosmo-demo-data`, starts the real backend on `127.0.0.1:8010`, opens a random `https://<four-words>.trycloudflare.com` address to it, checks that address from the internet side, prints it and copies it to the clipboard.
Keep the window open. In the installed app: **Set the server address** (first screen) or Settings > Server connection, paste the address, Save, then sign in as `patient1`, `patient2` or `doctor1` with the demo password.
Press Ctrl+C to stop; the address then stops working. To start over, stop it and delete `F:\cosmo-demo-data`. Options: `-Port`, `-DataDir`, `-Cloudflared <path to cloudflared.exe>`.

What this is, plainly: the computer must stay on, awake and online, and **the address is new every time you start it** (type the new one into the app); Cloudflare gives quick tunnels no uptime promise; the data is demo data on this computer.
It is a way to see and test the whole app, not a deployment, and not for real patients.

What to expect on an empty demo database (this is the app being honest, not broken): the assistant answers only from the patient's own reports and from content a doctor has approved, so before any report exists it says it has nothing to go on.
Upload a report (Reports), and the suggested questions become "What does my Ferritin result say?" and similar, answered with the source line quoted. The quotation screen says "No price list yet" and the assistant has no general
content until a signed-in doctor (`doctor1`, clinic workspace) publishes the price list and approves reviewed content. Nothing is pre-filled with invented data.

If something goes wrong: the script stops everything and says why. "The app says it can't reach your clinic" means the address changed, the computer slept, or the window was closed: run the script again and paste the new address.
(This computer's own DNS may remember a failed lookup of a brand-new address for a while; the script therefore checks the address through Cloudflare's DNS-over-HTTPS instead, and phones are not affected.)

What protects it: the backend listens on 127.0.0.1 only; a fresh random session-signing secret is created for every start and kept in memory (the repository's built-in development secret is never used: a token forged with it is
refused); the documented development password is refused and the demo administrator account is switched off; cross-origin calls are allowed only from the app's own origin `https://localhost`; the demo database is separate from your
development one and holds no research models; no Supabase or other cloud setting is read; nothing secret is in the app. The consent screen is not enforced in this local mode (its legal text needs the clinic's approval first,
`docs/PRIVACY_AND_CONSENT.md`).

### 5b. A real deployment: what the backend must provide

Nothing is deployed yet, so no API address is configured in the app, and none has been invented. To make sign-in, uploads and the AI conversation work for everyone, without this computer:

1. **A public https address** for the FastAPI backend (a domain with a certificate from a public authority, for example behind Caddy as in `ops/Caddyfile`). Plain http and addresses on a private network are refused by the app.
2. **The production configuration of the backend** (`docs/PRODUCTION_CONFIGURATION.md`, enforced at start-up by `app/prod_checks.py`): `COSMO_SECRET` (random, 32+ characters), `COSMO_AUTH=supabase` with `SUPABASE_URL`, the anon key and the
   server-only `SUPABASE_SERVICE_ROLE_KEY`, `COSMO_STORAGE=supabase`, `COSMO_BACKUP_KEY`, rate limiting on, and the database layer of `docs/DATABASE_MIGRATION.md`. Secrets stay on the server; none is in the app.
3. **`COSMO_CORS` must list the app's origin `https://localhost`** (what the WebView calls itself; it never touches the network) and the origin of the website if there is one. The start-up check accepts `https://localhost`.
4. **Accounts**: with Supabase sign-in, patients and clinicians sign in with their own e-mail accounts. Migrations 0006 to 0009 have **not** been applied to the real Supabase project; doing so needs your explicit go-ahead.
5. Then rebuild with `COSMO_API_BASE=https://<that address>` (section 3). Users can also type the address in the app (Settings > Server connection).

## 6. Development builds (developers only)

For a backend on your own computer: `node scripts\build-android.mjs --dev-server 10.0.2.2 --apk` (emulator) or `--dev-server <this computer's address>` (phone on the same Wi-Fi), then start the backend with
`ops\windows\android-dev-backend.ps1`. The build allows plain http to exactly those hosts, is labelled DEVELOPMENT, and must not be given to clients. A normal build removes the development network file again.

## 7. Verification report

| Check | Result |
|---|---|
| Production frontend build, type check, lint, the normal web build (`/reports/[id]` still dynamic) | pass |
| Standalone APK build (`assembleDebug`) with no server address and no development address | pass: `F:\Cosmo Experts\android\app\build\outputs\apk\debug\cosmo-experts-ai-debug.apk`, 5,184,856 bytes (4.9 MB), package `ai.cosmoexperts.app`, version 0.2.0-standalone (code 2), minimum SDK 24, target SDK 36, debug signature verifies |
| The APK contains the bundled screens (`assets/public`, 152 files: the 150 files of the tested bundle, byte for byte, plus Capacitor's two runtime shims), no `server.url`, https scheme, mixed content off, a network configuration with cleartext off and no host exception | pass |
| Search of the exported app and the APK for localhost, 127.0.0.1, 10.0.2.2 and private-network addresses | none as an address (no 127.0.0.1, 10.0.2.2, 192.168.x, 10.x or `http(s)://localhost`). The bare word `localhost` appears twice in the web bundle as code (a URL-parsing library and the app's own list of addresses it refuses) and in Capacitor's native code as the name of its internal origin, which never touches the network |
| `e2e/android-standalone.mjs` (the exact bundle, no backend, no internet, phone-sized Chromium) | 32/32: cold start, refuses every development address, unreachable-server state with Try again / Change server / Preview, stale development address erased, all patient screens in preview with content, upload flows fail honestly, airplane mode and reload, relaunch, zero requests outside the app |
| `e2e/android-bundle.mjs` (development bundle against the real backend) | 29/29: server setup, sign-in, a real assistant answer, failed send with retry and banner, offline, settings, preview |
| Installation and launch on an Android device or emulator, Android logs, physical-device camera, airplane mode on a phone | **not done**: no device was connected, and this computer cannot run the emulator (hardware virtualization is off; two boot attempts did not start the guest) |
| `e2e/android-live.mjs`: the exact APK bundle (served at `https://localhost`) against the real backend over a public https tunnel (section 5a), in a phone-sized Chromium | 27/27: the server address pasted without `https://` and tested, CORS from the app's origin, the development password refused, sign-in, an assistant answer that quotes an uploaded report with its source, photo check (queued job, "Photo accepted"), PDF report read and shown on `/reports/view/?id=`, history, care plan, quotation, privacy and settings from the server, sign-out, clinician sign-in with the patient's photos downloaded, zero requests outside the app and the one server |
| Outside checks of the demo server (over the internet) | 9/9: health, CORS allowed for `https://localhost` only, patient1, patient2 and doctor1 sign in, the documented development password refused, administrator account switched off, a token forged with the built-in development secret refused |
| Calls to a production deployment | **not testable**: none exists yet. The demo server (section 5a) is a stand-in for trying the whole app, not a deployment |

## 8. Known limitations

* Debug build only: no release signing or Play Store bundle (`assembleRelease` needs your own keystore).
* The sign-in token lives in the WebView's session storage and ends with the app, so the app asks again after it is closed; Supabase refresh cookies and password-reset e-mail links belong to the website, not the app.
* Real-device behaviour (edge-to-edge insets on Android 15 and later, keyboard overlap, permission prompts, TalkBack) is unverified.
* OTP verification and a patient onboarding flow do not exist in this project; the app has the sign-in that the backend provides.
