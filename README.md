# Cosmo Experts AI

Patient-friendly assistant for photos and medical reports, with clinician review, quotations, and an
administrator AI lab. It **does not diagnose or prescribe**. Everything runs locally; no patient data
leaves the machine and no paid AI/speech API is used.

## Run (Windows/macOS/Linux, CPU only)

```
cd backend
py -3.11 -m venv .venv && .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu   # training only
.venv\Scripts\python -m uvicorn app.main:app --port 8000

cd frontend && npm install && npm run build && npm run start     # http://localhost:3000
```

Demo accounts (local mode only): `patient1`, `patient2`, `doctor1`, `admin1`, password `cosmo-dev`
(override with `COSMO_DEV_PASSWORD`). Set `COSMO_SECRET` for anything beyond local use.

## Android app

`cd frontend && node scripts/build-android.mjs --apk` builds a **standalone** debug APK of the real UI (needs JDK 21 and the Android SDK): the screens are inside the app and it needs no development server. Sign-in, data and the AI assistant use one https server
address (`COSMO_API_BASE=https://...` at build time, or entered in the app); until a backend is deployed the app opens and says so. See `docs/ANDROID_APK.md`.

To try the **whole** app before anything is deployed, run `powershell -ExecutionPolicy Bypass -File ops\windows\start-cosmo-demo.ps1`: it starts the real backend on this computer (local demo accounts, a password you choose) behind a temporary secure https address
(Cloudflare quick tunnel) that you paste into the app. Demo only: the computer must stay on and the address changes every start (`docs/ANDROID_APK.md`, section 5a).

## Tests

* `cd backend && .venv\Scripts\python -m pytest -q tests` — 27 tests: image checks, document pipeline,
  tenant isolation, assistant refusal/abstention/grounding, quotations, licence gate, import/dedupe/leak-free
  splits, real PyTorch training, approval gate, rollback.
* `cd frontend && npm run lint && npx tsc --noEmit && npm run build`.
* Browser E2E (Playwright, fresh database): `e2e/assistant-guide.mjs` (the skin guide: education, warning-sign banners, reports, booking, and the photo journey), `e2e/phase3-ui.mjs`, `e2e/p11-a11y.mjs`, `e2e/android-standalone.mjs`. `e2e/ui-journey.mjs` and `clinical-journey.mjs` are legacy baseline scripts and no longer match the app. Earlier manual run: login, photo rejection message, PDF upload,
  exact value/unit/page citation, Hinglish toggle, audio playback, grounded answer, prescribing refusal.

## What is implemented vs. not

| Area | State |
|---|---|
| Image suitability (quality, face, region) | **Available** (heuristic; hair/scalp and body checks may return "uncertain") |
| Document pipeline (PDF/JPG/PNG/DOCX/TXT, fields, source page, printed-range flags, comparison, EN/HI/Hinglish) | **Available**; scanned files need Tesseract installed, otherwise reported as unreadable |
| Assistant (TF-IDF retrieval over the patient's own documents, approved KB, approved quotes) | **Available**; extractive, refuses prescribing/diagnosis, abstains without evidence |
| Voice playback | Offline OS voice via pyttsx3 (worked in test on Windows); falls back to local browser voice, then text |
| Voice input | **Unavailable** until faster-whisper and a local model are installed (clear admin message) |
| Quotations + PDF | Available; only items on the doctor-approved price list |
| Dataset registry / licence gate / compatibility / import | Available; every dataset starts **blocked** |
| Training / evaluation / model registry / approval / rollback | Working pipeline, **research only**. No clinical model exists |
| Concern models (acne, pigmentation, hair thinning, ...) | **Unavailable**: no validated model, no suitable dataset verified. No predictions are produced |

## Known gaps (need your input)

* Spec text was cut off at section 9 ("Start with suitable pretrained models and transfer lear…"); sections after it were not seen, so only the parts above were built.
* Clinical approvals: seeded KB articles are drafts; escalation wording for urgent findings is a placeholder pending clinician approval.
* Dataset licences in the registry are *claims* recorded from memory and must be verified by an administrator against official terms (the app stores a hash snapshot as evidence). No dataset was downloaded.
* Supabase: schema and RLS are in `supabase/migrations/0001_core.sql` but not applied to any project, and the API still uses local SQLite and HMAC tokens. Wiring Supabase Auth/Storage needs your project credentials.
* OCR (Tesseract) is not installed here, so scanned-document reading is untested; the unreadable-scan path is tested.
* Background workers: training runs in a thread; document processing is synchronous.
* No fairness/clinical validation of the image checks has been done (Haar face detector is weaker on some faces/poses).

See `docs/LICENSES.md` for software and dataset licence notes.

## UI
See [BUILD_STATUS.md](BUILD_STATUS.md) for the 10-screen premium UI, design system, verification results and known limitations.
