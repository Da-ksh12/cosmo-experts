# Android app (debug APK): build, install, connect

The Android app is the real Cosmo Experts web UI (the Next.js app in `frontend/`) packaged with Capacitor, so every screen, the AI conversation and the agent actions are the ones the web app has.
It is a **thin client**: all data, sign-in and AI answers come from a Cosmo Experts server that you point it at. No backend, model, key or password is inside the app.

```
frontend/ (Next.js)  --static export-->  frontend/out-android  --Capacitor sync-->  android/ (Gradle project)  --assembleDebug-->  cosmo-experts-ai-debug.apk
                                                                        phone  --https (or a listed dev host over http)-->  Cosmo Experts backend (FastAPI)
```

## 1. Build the APK (one command)

Needs Node 22+, JDK 21 (`JAVA_HOME`), and the Android SDK with platform 36 and build-tools 36 (`ANDROID_HOME`). Gradle downloads itself through the wrapper.

```
cd frontend
node scripts/build-android.mjs --apk          # static export -> Capacitor sync -> Gradle assembleDebug
```

Output: `android/app/build/outputs/apk/debug/cosmo-experts-ai-debug.apk` (debug-signed, about 5 MB). `node scripts/build-android.mjs` without `--apk` only exports and syncs.
The first Gradle run downloads dependencies (about 4 minutes on a small laptop); later runs take about a minute. Set `GRADLE_USER_HOME` to a drive with space if the system drive is small.

## 2. Install

* **Phone with USB debugging:** `adb install -r android/app/build/outputs/apk/debug/cosmo-experts-ai-debug.apk`
* **Any phone:** copy the file to the phone and open it; Android asks once to allow installing from that source. It is a debug-signed build for demos and testing, not a Play Store build.
* **Emulator:** `adb install` as above. (The emulator on this development computer has no hardware acceleration, see section 6.)

## 3. Connect the app to a server

The app asks for the server address on first start (**Connect to your clinic**) and keeps it on the phone; it can be changed any time in **Settings > Server connection** (and on the sign-in screen). It tests the address
against the server's health endpoint before saving.

| Server | Address to enter | What the server needs |
|---|---|---|
| A deployed backend | `https://your-server` | `COSMO_CORS` must list the app's origin: `http://localhost` (the default of this build) or `https://localhost` (if built with `"androidScheme": "https"`) |
| This development computer, phone on the same Wi-Fi | `http://<computer address>:8000` | the helper below; the address must be listed in `devCleartextHosts` when the app was built |
| This computer, Android emulator | `http://10.0.2.2:8000` | the helper below (already allowed in every debug build) |

**Development backend helper** (Windows): `powershell -ExecutionPolicy Bypass -File ops\windows\android-dev-backend.ps1 -DataDir F:\cosmo-demo-data`. It asks you for the password of the demo accounts
(`patient1`, `patient2`, `doctor1`; accounts that already exist in an old database keep their old password, which is why a fresh `-DataDir` is recommended), allows only the app's origin through CORS, listens on the
network and prints the addresses to type into the app. Windows Firewall asks once to allow Python on private networks. Stop it with Ctrl+C. Do not expose this development backend to the internet.

**Signing in.** The sign-in screen lists the demo accounts of the backend you run (`patient1`, `patient2`, `doctor1`; the administrator console is not offered). On a development backend their password is the one set by
`COSMO_DEV_PASSWORD` when the accounts were created, or the documented development default in `README.md`; they exist only on the server, never in the app. A deployed backend signs people in with their own accounts.

## 4. Configuration values

| Where | Setting | Meaning |
|---|---|---|
| `frontend/android.config.json` (committed defaults) and `frontend/android.config.local.json` (yours, ignored by Git) | `apiBase` | Server address baked into the app as the default; empty means the app asks. Must be `https://` (or `http://` on a private network) |
| same | `devCleartextHosts` | Hosts a **debug** build may reach over plain `http://` (default `10.0.2.2`, `localhost`). Written into the debug-only `src/debug/res/xml/network_security_config.xml`. The release configuration allows https only |
| same | `androidScheme` | `http` (default, lets a debug build talk to a development host without a mixed-content block) or `https` (use this with an https server for a release build) |
| same | `appId`, `appName` | Package id (`ai.cosmoexperts.app`) and app name (`Cosmo Experts AI`) |
| `frontend/capacitor.config.ts` | splash, system bars, keyboard | Ivory splash, dark system-bar icons, native keyboard resizing |
| server environment | `COSMO_CORS` | Origins allowed to call the API: add `http://localhost` for this build |
| server environment | `COSMO_DEV_PASSWORD` | Password of the local demo accounts (never ship the documented default) |

After changing the config, run `node scripts/build-android.mjs --apk` again.

## 5. What is in the app

The patient dashboard with quick actions, photo capture and upload (camera permission is asked only when you start a photo), report upload and results history, the AI conversation (real answers from the backend, suggested
questions, history, delete, Hindi/Hinglish text, voice input and spoken answers where the backend has them), history and report pages, **Settings** (profile, server connection, privacy and consent, sign out), the doctor
workspace and the agent's confirm-before-change actions. The administrator console is not offered on the app's sign-in screen.

* **Safety:** the five experimental research models of Phase 5 are not part of the app and are never shown to patients; the assistant does not diagnose or prescribe and abstains when it has no approved source.
* **Honest failures:** a request that fails is shown as failed ("Not sent", "We can't reach the server (host)", an offline banner); nothing is answered, saved or predicted on the phone's own.
* **Preview mode (no server):** the connection screen offers "Look around in preview mode": the real screens with **empty states only**, a permanent PREVIEW banner, no network request at all, and every action that would
  send, save, upload or analyse something refused with a plain message. It never shows an example record, answer or result. It ends with the session (banner: "Connect to a server").
* **Security:** the app has no secrets; the only stored setting is the server address; the sign-in token lives in the WebView's session storage and is gone when the app is closed; Android backup is off; cleartext
  `http://` is refused except for the listed debug hosts; the release network configuration allows https only.

## 6. What has been tested, and what has not

| Check | Result |
|---|---|
| Static export of the real UI (18 routes, 2 MB) | builds; `tsc`, `eslint`, web `next build` unchanged |
| APK build | `assembleDebug` succeeds; package `ai.cosmoexperts.app`, target SDK 36, minimum SDK 24; permissions INTERNET, CAMERA, RECORD_AUDIO, MODIFY_AUDIO_SETTINGS (camera and microphone not required) |
| The exact bundle in a phone-sized Chromium against the **real backend** (`e2e/android-bundle.mjs`, built with `node scripts/build-android.mjs --no-local`) | 27/27: server setup and validation, sign-in, a real assistant answer, a failed send with retry, offline banner, settings, unreachable server, preview mode (labelled, empty, zero API requests) |
| Installation and launch on an Android device or emulator | **not tested**: no device was connected, and this computer's emulator has no hardware acceleration (virtualization is off) |
| Camera, microphone, file picker, back button, keyboard and system bars inside the Android WebView | **not tested** (they need a device); they use Capacitor's standard handling and the web code that passed in Chromium |

## 7. Known limitations

* Debug build only: no release signing, no Play Store bundle, no crash reporting. Rebuild with your own signing key for distribution.
* The server address of a development computer changes with the network; re-enter it in Settings (and list it in `devCleartextHosts` for a debug build that must reach it over http).
* Sign-in is a bearer token kept for the session; the app asks again after it is closed. Supabase-mode refresh cookies and password-reset e-mail links belong to the website, not the app (no deep links yet).
* The research-only AI lab and the administrator console are desktop screens and are not offered on the phone sign-in screen.
* Real-device behaviour (edge-to-edge insets on Android 15+, keyboard overlap, permission prompts, TalkBack) is unverified.
