# Voice and multilingual behaviour (Phase 3)

How speech enters and leaves the system, what is promised about it, and exactly what has and has not been checked for each language. Code: `backend/app/speech/{adapters,spoken}.py`, `backend/app/ai/multilingual.py`,
`backend/app/documents/explain.py` (templates), `frontend/components/{VoiceInput,AudioPlayer}.tsx`. Tests: `tests/test_voice.py` (10), `tests/test_spoken.py` (8), `tests/test_multilingual.py` (12).
Evaluation: `backend/eval/voice_eval.py` -> `docs/eval/voice.json`.

**Nothing here is a claim of reliable Hindi or Hinglish.** Speech recognition was checked on synthetic English speech only; the fixed Hindi and Hinglish explanation sentences were written without review by
a Hindi-speaking clinician and are labelled unreviewed wherever they are shown. `GET /api/v1/ai/speech/languages` reports this per language, and the code that produces the report is the same text this
document quotes (`ai/multilingual.EVIDENCE`).

## 1. Speech to text (`speech/adapters.py`, `stt-2.0.0`)

Local `faster-whisper` "base" (int8, CPU), loaded from a model directory that must already exist (`local_files_only`: nothing is downloaded at request time). It is reached through the engine capability
`speech.transcribe` (bounded queue, timeout, cancellation) by both `POST /api/speech/transcribe` and the job route `POST /api/v1/speech/transcribe`.

| Promise | How it is kept |
|---|---|
| A huge or empty recording is refused before anything runs | byte limit (default 10 MB, hard cap 25 MB, `COSMO_STT_MAX_BYTES`); empty -> a plain message |
| A long recording is refused before transcription work | duration limit (default 60 s, hard cap 300 s, `COSMO_STT_MAX_SECONDS`) checked right after decoding; a bad setting never removes a bound |
| Silence is not turned into words | under 0.3 s or RMS under 0.003 -> "no speech" without calling the model; a segment is dropped only when the model itself says *both* `no_speech_prob > 0.6` and `avg_logprob < -1.0` |
| The person knows what language was heard | the result carries the language requested, the language detected and its probability; if the person chose a language and the recording sounds like another (probability >= 0.6) a warning says so |
| The audio is not kept | written to a temporary file only to decode, deleted at once; the transcript is returned for the person to **confirm** (`needs_confirmation`), not stored |
| A cancelled or timed-out request stops | cancellation is checked between steps and between segments |
| What produced the text is recorded | model label, processing version, audio length in the result |

A transcript is never sent to the assistant automatically: the person sees it first. A failed or unavailable recogniser says so ("Please type your question"); it never returns a made-up sentence.

## 2. Text to speech

Offline operating-system voices (`pyttsx3`: SAPI5, NSSpeechSynthesizer, eSpeak). **A voice is used only for a language it can speak.** On this machine three English voices are installed and **no Hindi
voice is**: a Hindi request fails with "Voice playback failed. The text is shown instead." and is never read aloud by an English voice. Hinglish (Hindi in Latin letters) is read with an English voice and may be
mispronounced; this was not evaluated. The interface also lets a device use its own on-device browser voice, which this system cannot test or vouch for. Text is always shown.

## 3. What is spoken is what is shown (`speech/spoken.py`)

The spoken explanation is built from the same statements as the screen, and `check()` verifies it sentence by sentence:

* every spoken statement is a shown statement, word for word (the one change: English unit symbols are read in words, "ng/mL" -> "nanograms per millilitre");
* numbers, dates and quoted printed text are spoken exactly as shown - nothing rounded, translated or dropped; the page each statement comes from is spoken too;
* what is still uncertain, the request for clinician review and the statement that this is not a diagnosis are **always** spoken, whatever else is left out;
* if there are more statements than are read (default cap 12), the voice says how many are left on the screen.

## 4. Languages (`ai/multilingual.py`)

Three things are kept apart and labelled: the **original** (exactly what the document or person said, in its own script), the **normalised** copy (spacing; Devanagari digits read as ASCII digits - used only for
matching, never shown as the original), and a **translation** (with how it was made - fixed template, machine, human - and whether a reader of that language reviewed it).

A rendering must carry every **protected token** of the text it renders: numbers (including Devanagari digits), dates, units, quoted printed text and the medicine and test names. `preserved(original, rendering)`
returns what is missing, so "8 ng/mL" can never become "80 ng/mL" or lose its unit. A test compares every fixed Hindi and Hinglish explanation sentence with its English counterpart.

| Language | Speech recognition | Explanation wording | Voice |
|---|---|---|---|
| English | checked on **synthetic** English speech only (section 5); not real patients, accents or noise beyond the conditions listed | fixed templates written by the project's author | an installed offline English voice, when present |
| Hindi | **not checked** | fixed Hindi templates, **unreviewed** (`translation_reviewed: false`; the report screen shows the notice "no Hindi-speaking clinician has reviewed" above the explanation) | only if a Hindi voice is installed; never substituted |
| Hinglish | **not checked** | fixed Hinglish templates, **unreviewed** | English voice; may mispronounce |
| any other | nothing in the system has been checked | - | - |

There is no machine translation in this system (`translation.text` is registered as planned and unavailable). Approved knowledge-base background is English only. Hindi OCR is experimental and its language
pack is not installed on this machine (MEDICAL_DOCUMENT_INTELLIGENCE.md).

## 5. Evaluation (`eval/voice_eval.py`; evidence class `synthetic_fixture`, not real-world)

Run on 2026-10-19 (`docs/eval/voice.json`, 357 s; the file records the code revision, configuration and environment). Three offline Windows voices (David and Zira, US English; Hazel, UK English) read 12 sentences from the
Phase 1 set, each recording clean and with a simulated telephone band: 36 utterances per condition. 24 further utterances carry 39 numeric values. 10 clips are digital silence or low-level noise.

| Measure | Result |
|---|---|
| Word error rate, clean / telephone band | **4.4% / 8.9%** |
| Key clinical terms recovered, clean / telephone band | 87.9% / 78.8% (so roughly one key term in eight is misheard even on clean synthetic speech; the transcript is always shown for the person to confirm) |
| Language identified as English | 36/36 clean, 36/36 telephone band (mean probability 0.99 / 0.96) |
| Numbers recovered | 39 of 39 |
| Silence and noise-only clips reported as "no speech" | 10 of 10; none turned into words |
| Real-time factor on this CPU | 1.2 (slower than real time) |
| Spoken explanations checked against the screen | 60 synthetic explanations in 3 languages x 2 length caps = 360 scripts, 5,661 sentences: **0** sentences differed from what is shown; 87 scripts were cut by the cap and each said how many statements remained on the screen |
| Hindi and Hinglish statements compared with their English counterpart | 1,584 statements: **0** protected tokens (numbers, dates, units, printed text, names) lost |
| Voices | English: yes. Hindi: **none installed**, so Hindi is never read aloud |

What these figures are and are not. The speech is clean, single-speaker synthesis in two English accents; real voices, accents, children, noisy rooms and telephone calls will be harder, and nothing here was
recorded from a person. The real-time factor depends on the machine's state: Phase 1 recorded 0.67 on a different sentence set, and a side-by-side timing of the Phase 1 recognition path and the Phase 3 path on the
same eight clips, in the same minute, gave the same factor for both (1.3-1.7 across three rounds), so the difference is the machine and not the new code. An earlier run of this script scored 36 of 39 numbers because
it did not treat "four hundred and ten thousand" and "410,000" as the same number; that was a defect in the script (the recogniser's words were the same), corrected before the figures above. Hindi and Hinglish
were **not** tested for recognition at all.

## 6. Not done

Real speakers, accents, children, elderly voices, noisy rooms or telephone lines beyond the one simulated condition; any Hindi or Hinglish recognition; a Hindi voice; human review of the Hindi and Hinglish
sentences; streaming recognition; speaker identification (not wanted). Closing those gaps needs consented recordings and a speaker-clinician reviewer; until then Hindi and Hinglish stay labelled as above.
