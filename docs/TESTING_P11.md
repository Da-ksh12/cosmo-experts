# Browser, device, accessibility and admin-workflow testing (P11)

How to run (stack on :3000/:8000; the runs recorded here used a copy of the development database in a temporary directory so the real one was untouched):

```
cd e2e && npm install && npx playwright install chromium firefox webkit
node p11-a11y.mjs        # axe-core WCAG 2.0-2.2 A/AA on every page and role, keyboard, focus, reduced motion, reflow, text size
node p11-browsers.mjs    # Chromium + Firefox + WebKit journeys, 5 emulated devices, touch targets, landscape, fake camera
node p11-admin.mjs       # clinician and administrator workflows, including AI Studio training Start/Stop
```
Results are written to `docs/runs/p11_{a11y,browsers,admin}.json`; desktop and device screenshots to `docs/runs/p11_screens/`.

## What was covered
| Area | Evidence |
|---|---|
| Automated accessibility | axe-core with `wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa` on 10 routes × roles at 1280 px and 390 px, plus the open review dialog: 0 violations |
| Keyboard | complete sign-in with only the keyboard; every focusable control on the clinic page shows a focus indicator; modal dialog: focus moves in, Tab and Shift+Tab never leave, Escape closes and returns focus to the opener |
| Motion | with `prefers-reduced-motion`, nothing animates for more than 50 ms; control run proves the page does animate otherwise |
| Reflow / zoom | no sideways scrolling at 320 px (WCAG 1.4.10), at 640 px (200% desktop zoom), and with text enlarged to 200% on a 390 px phone |
| Browsers | Chromium 131, Firefox 132, WebKit 18.2: sign-in, assistant (emergency redirect and abstention), reports, photo upload, clinician workspace, sign-out |
| Devices (emulated) | iPhone 13, iPhone SE, iPad (gen 7) in WebKit; Pixel 7, Galaxy S9+ in Chromium: no sideways scrolling on 7 pages, bottom navigation by tap, every touch target ≥ 24×24 px (WCAG 2.2 SC 2.5.8), landscape layouts |
| Camera / microphone | Chromium with fake media devices: live preview receives frames and capture proceeds; microphone control present |
| Clinician / admin workflows | knowledge-base review with attestation, edit-returns-to-draft and assistant use before/after; price list add; quotation settings persistence; patient request → clinician approval → server-computed tax → PDF; administrator restrictions; clinician verification; role isolation; training Start → running → real log lines → Stop → stopped, with no model registered by a stopped job |

## Defects found by these tests and fixed
* Modal dialogs had no focus trap (Tab could reach the page behind). Fixed in `components/ui.tsx`.
* Insufficient colour contrast on the photo-guidance "Not good" tip; scrollable tables not reachable by keyboard (admin, model cards, clinic); horizontal overflow at 320 px on Home and Photo; header, bottom navigation, action cards and headings overflowing at 200% text size; decorative leaf artwork widening the page.
* Patients could load the `/clinic` and `/admin` page shells (the API refused all data, but the pages rendered). They are now redirected and never rendered.
* Tests themselves also exposed that editing KB text clears the attestation: this is intended and is now asserted.

## Not covered (stated plainly)
* **Real devices.** Everything above is emulation (viewport, touch, user agent). Real iOS Safari/Android Chrome behaviour, real cameras and microphones, device performance and OS-level text scaling were not tested.
* **Screen readers.** No NVDA, JAWS, VoiceOver or TalkBack session was run. axe-core finds roughly a third of WCAG problems automatically; reading order, label quality, announcements of live regions (assistant answers, upload progress) and the meaning of alternative text need a person using a screen reader. This remains a manual task.
* Hindi/Hinglish UI and speech, high-contrast / forced-colours mode, 400% zoom on desktop, and voice-control software.
* WebKit printed "Fetch API cannot load … due to access control checks" for requests cancelled by navigation (15 occurrences recorded and excluded from the console-error check). A separate run proved none appear on a page left idle after sign-in. Treat as browser noise unless it recurs on idle pages.
* One Firefox sign-in click timed out once under heavy CPU load (animated button "stable" wait) and passed in six repeated attempts; the helper timeout was raised.
* The training test stops the job during data preparation on a 2-core CPU; it proves the Start/Stop controls and progress reporting, not training quality.
