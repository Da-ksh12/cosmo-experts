"""Measured speech-recognition evaluation of the local Whisper model.

Audio is SYNTHETIC (offline OS text-to-speech voices), optionally degraded with additive noise and telephone-band filtering.
This is an optimistic test: it is NOT evidence of performance on real patients' voices, accents, Hindi or Hinglish.
Only languages for which an installed voice exists can be tested here; others are reported as unverified.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import tempfile
import time
import wave
from pathlib import Path

import numpy as np
import pyttsx3

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SENTENCES = [
    "What does my ferritin result say?", "Is my haemoglobin low on this report?", "Can you explain my thyroid stimulating hormone value?",
    "What is a reference range?", "Which page shows my vitamin D result?", "What questions should I ask my doctor about this report?",
    "My creatinine is marked high, what does that mean?", "How much is my quotation for the treatment?", "Please explain the cholesterol results in simple words.",
    "Does this report show my blood sugar level?", "What does the platelet count mean?", "Is the uric acid inside the printed range?",
    "I want to understand my prescription in easy language.", "What is the date of this laboratory report?", "Can I see the original text of the report?",
    "Why is one result marked low by the laboratory?", "Please read my report summary aloud.", "What does serum zinc measure?",
    "Where is the reference range printed for glucose?", "Tell me what the doctor notes say about my skin treatment.",
]
VARIANTS = {"haemoglobin": ["haemoglobin", "hemoglobin"], "cholesterol": ["cholesterol"], "thyroid": ["thyroid"]}  # accepted spelling variants
KEY_TERMS = ["ferritin", "haemoglobin", "thyroid", "vitamin", "creatinine", "cholesterol", "platelet", "glucose", "zinc", "quotation", "prescription", "report"]


def wer(ref: str, hyp: str) -> tuple[int, int]:
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower().replace("-", " ")).split()
    r, h = norm(ref), norm(hyp)
    d = list(range(len(h) + 1))
    for i in range(1, len(r) + 1):
        prev, d[0] = d[0], i
        for j in range(1, len(h) + 1):
            cur = d[j]
            d[j] = min(d[j] + 1, d[j - 1] + 1, prev + (r[i - 1] != h[j - 1]))
            prev = cur
    return d[len(h)], len(r)


def synth(text: str, voice_id: str, path: str):
    e = pyttsx3.init()
    e.setProperty("voice", voice_id)
    e.save_to_file(text, path)
    e.runAndWait()


def degrade(path: str, cond: str, out: str, seed: int):
    with wave.open(path, "rb") as w:
        p, frames = w.getparams(), w.readframes(w.getnframes())
    x = np.frombuffer(frames, np.int16).astype(np.float32)
    rng = np.random.default_rng(seed)
    if cond.startswith("noise"):
        snr = float(cond.split("_")[1].replace("dB", ""))
        sig = np.sqrt(np.mean(x ** 2)) + 1e-9
        x = x + rng.normal(0, sig / (10 ** (snr / 20)), x.shape)
    elif cond == "telephone":
        n = len(x); X = np.fft.rfft(x); f = np.fft.rfftfreq(n, 1 / p.framerate)
        X[(f < 300) | (f > 3400)] = 0; x = np.fft.irfft(X, n)
    x = np.clip(x, -32768, 32767).astype(np.int16)
    with wave.open(out, "wb") as w:
        w.setparams(p); w.writeframes(x.tobytes())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", default=str(Path(__file__).resolve().parent.parent / "data/models/stt/faster-whisper-base"))
    ap.add_argument("--out", default="../docs/eval")
    ap.add_argument("--n", type=int, default=len(SENTENCES))
    a = ap.parse_args()
    os.environ["COSMO_WHISPER_MODEL_DIR"] = a.model_dir
    from app.speech import adapters
    voices = [v for v in pyttsx3.init().getProperty("voices")]
    conds = ["clean", "noise_20dB", "noise_10dB", "telephone"]
    res = {c: {"err": 0, "words": 0, "terms_hit": 0, "terms_total": 0, "sec": 0.0, "audio": 0.0, "n": 0} for c in conds}
    tmp = tempfile.mkdtemp(prefix="stt_")
    adapters._get_model()   # load once; excluded from timing
    for vi, v in enumerate(voices):
        for si, text in enumerate(SENTENCES[: a.n]):
            base = os.path.join(tmp, f"v{vi}s{si}.wav")
            synth(text, v.id, base)
            for c in conds:
                path = base if c == "clean" else os.path.join(tmp, f"v{vi}s{si}_{c}.wav")
                if c != "clean":
                    degrade(base, c, path, seed=vi * 1000 + si)
                t = time.time()
                hyp, dur, lang = adapters.transcribe_file(path, "en")
                r = res[c]
                e, n = wer(text, hyp)
                r["err"] += e; r["words"] += n; r["n"] += 1; r["sec"] += time.time() - t; r["audio"] += dur
                for k in KEY_TERMS:
                    if k in text.lower():
                        r["terms_total"] += 1; r["terms_hit"] += int(any(alt in hyp.lower() for alt in VARIANTS.get(k, [k])))
        print(f"voice {v.name} done", flush=True)
    rep = {"model": "Systran/faster-whisper-base (MIT), int8, CPU", "voices": [v.name for v in voices], "utterances_per_condition": res["clean"]["n"],
           "caveat": "Synthetic TTS speech; clean/noisy/telephone simulations. English only. Hindi/Hinglish NOT verified (no Hindi voice/audio available).", "conditions": {}}
    for c, r in res.items():
        rep["conditions"][c] = {"WER": round(r["err"] / r["words"], 3), "key_term_recall": round(r["terms_hit"] / r["terms_total"], 3), "real_time_factor": round(r["sec"] / r["audio"], 2), "utterances": r["n"]}
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    (out / "stt.json").write_text(json.dumps(rep, indent=2), encoding="utf-8")
    lines = ["| Condition | Utterances | WER | Key-term recall | Real-time factor (CPU) |", "|---|---|---|---|---|"]
    for c, m in rep["conditions"].items():
        lines.append(f"| {c} | {m['utterances']} | {m['WER']} | {m['key_term_recall']} | {m['real_time_factor']} |")
    (out / "stt.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
