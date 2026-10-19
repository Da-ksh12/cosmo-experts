"""Voice evaluation: speech recognition on synthetic English speech, silence, the voices that exist for each language, how faithfully an explanation is read aloud, and whether Hindi and Hinglish
sentences keep the facts of their English counterparts.

    python eval/voice_eval.py [--out ../docs/eval] [--quick]

Evidence class: synthetic_fixture. The speech is produced by offline operating-system voices (and degraded with noise and a telephone band); it is NOT real patients, accents, Hindi or Hinglish. The
explanations are generated from random synthetic lab values. A good figure here says the mechanisms work on these inputs; it is not a claim about real use. Languages with no installed voice are
reported as untested, not as passing."""
from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai import multilingual  # noqa: E402
from app.ai.evaluation import common  # noqa: E402
from app.documents import explain  # noqa: E402
from app.speech import adapters, spoken  # noqa: E402
from eval import stt_eval  # noqa: E402

NUMBER_SENTENCES = [("My ferritin is 8 nanograms per millilitre.", ["8"]), ("The haemoglobin is 13.5 grams per decilitre.", ["13.5"]), ("Page 3 shows a value of 250.", ["3", "250"]),
                    ("The report date is the 12th of March 2025.", ["12", "2025"]), ("Is 7.9 higher than the printed range of 0.4 to 4.0?", ["7.9", "0.4", "4.0"]),
                    ("Glucose is 92 and cholesterol is 180.", ["92", "180"]), ("Platelets are 410 thousand.", ["410000"]), ("The creatinine is 0.9 milligrams per decilitre.", ["0.9"])]
WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: 10 * i for i, w in enumerate("_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()) if w != "_"}


def numbers_in(text: str) -> list[str]:
    """Numbers in a transcript as digits, reading simple number words ('twelve point five' -> 12.5, 'one hundred and fifty' -> 150)."""
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)                         # 410,000 is 410000
    toks = re.findall(r"[a-z]+|\d+(?:[.,]\d+)?", text.lower().replace("-", " "))
    out, i = [], 0
    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"\d+(?:[.,]\d+)?", t):
            v = float(t.replace(",", "."))
            if i + 1 < len(toks) and toks[i + 1] == "thousand":                    # "410 thousand" and "410,000" are the same number
                v *= 1000; i += 1
            out.append(str(int(v)) if v == int(v) and "." not in t else t.replace(",", "."))
        elif t in WORDS or t in TENS:
            v = TENS.get(t, WORDS.get(t, 0))
            if t in TENS and i + 1 < len(toks) and toks[i + 1] in WORDS and WORDS[toks[i + 1]] < 10:
                v += WORDS[toks[i + 1]]; i += 1
            if i + 1 < len(toks) and toks[i + 1] == "hundred":
                v *= 100; i += 1
                if i + 1 < len(toks) and toks[i + 1] == "and":
                    i += 1
                if i + 1 < len(toks) and (toks[i + 1] in WORDS or toks[i + 1] in TENS):
                    i += 1
                    v += TENS.get(toks[i], WORDS.get(toks[i], 0))
                    if toks[i] in TENS and i + 1 < len(toks) and toks[i + 1] in WORDS and WORDS[toks[i + 1]] < 10:
                        i += 1; v += WORDS[toks[i]]
            s = str(v)
            if i + 2 < len(toks) + 1 and i + 1 < len(toks) and toks[i + 1] == "point":
                j, frac = i + 2, ""
                while j < len(toks) and toks[j] in WORDS and WORDS[toks[j]] < 10:
                    frac += str(WORDS[toks[j]]); j += 1
                if frac:
                    s += "." + frac; i = j - 1
            out.append(s)
        i += 1
    return out


def _silence(sec, noise=0.0, seed=0):
    import io, wave
    x = (np.random.default_rng(seed).normal(0, noise, int(sec * 16000)) * 32767).astype(np.int16) if noise else np.zeros(int(sec * 16000), np.int16)
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(x.tobytes())
    return b.getvalue()


def stt_section(quick: bool) -> dict:
    st = adapters.stt_status()
    if not st["available"]:
        return {"tested": False, "reason": st["admin_message"]}
    import pyttsx3
    voices = [v for v in pyttsx3.init().getProperty("voices") if "english" in v.name.lower() or any(str(l).lower().startswith("en") for l in (v.languages or []))]
    if not voices:
        return {"tested": False, "reason": "no offline English voice is installed to make test recordings"}
    sentences = stt_eval.SENTENCES[: (6 if quick else 12)]
    conds = ["clean", "telephone"]
    tmp = tempfile.mkdtemp(prefix="voice_eval_")
    adapters._get_model()
    agg = {c: {"err": 0, "words": 0, "terms_hit": 0, "terms_total": 0, "sec": 0.0, "audio": 0.0, "n": 0, "lang_ok": 0, "lang_p": 0.0} for c in conds}
    nums = {"expected": 0, "found": 0, "n": 0, "missed": []}
    for vi, v in enumerate(voices):
        for si, text in enumerate(sentences):
            base = os.path.join(tmp, f"v{vi}s{si}.wav")
            stt_eval.synth(text, v.id, base)
            for c in conds:
                path = base if c == "clean" else os.path.join(tmp, f"v{vi}s{si}_{c}.wav")
                if c != "clean":
                    stt_eval.degrade(base, c, path, seed=vi * 1000 + si)
                t0 = time.time()
                try:
                    r = adapters.transcribe_detail(Path(path).read_bytes(), None)
                except adapters.AudioError:
                    r = None
                a = agg[c]
                a["n"] += 1
                hyp = r.text if r else ""
                e, n = stt_eval.wer(text, hyp)
                a["err"] += e; a["words"] += n; a["sec"] += time.time() - t0; a["audio"] += (r.audio_seconds if r else 0)
                a["lang_ok"] += bool(r and r.language == "en"); a["lang_p"] += (r.language_probability if r else 0)
                for k in stt_eval.KEY_TERMS:
                    if k in text.lower():
                        a["terms_total"] += 1; a["terms_hit"] += int(any(alt in hyp.lower() for alt in stt_eval.VARIANTS.get(k, [k])))
        for si, (text, want) in enumerate(NUMBER_SENTENCES[: (4 if quick else None)]):
            p = os.path.join(tmp, f"v{vi}n{si}.wav")
            stt_eval.synth(text, v.id, p)
            try:
                heard_text = adapters.transcribe_detail(Path(p).read_bytes(), "en").text
                got = numbers_in(heard_text)
            except adapters.AudioError:
                heard_text, got = "", []
            nums["n"] += 1; nums["expected"] += len(want); nums["found"] += sum(1 for w in want if w in got)
            nums["missed"] += [{"voice": v.name, "said": text, "heard": heard_text, "number": w} for w in want if w not in got]
    silence = {"clips": 0, "no_speech": 0, "invented": []}
    for k, (sec, noise) in enumerate([(2, 0), (3, 0), (4, 0), (3, 0.002), (3, 0.005), (4, 0.01), (5, 0.01), (2, 0.02), (6, 0.003), (3, 0.01)]):
        silence["clips"] += 1
        try:
            tr = adapters.transcribe_detail(_silence(sec, noise, k), "en")
            silence["invented"].append(tr.text[:60])
        except adapters.NoSpeech:
            silence["no_speech"] += 1
        except adapters.AudioError:
            silence["invented"].append("(audio error)")
    out = {"tested": True, "model": "faster-whisper base int8 (local, CPU)", "voices": [v.name for v in voices], "sentences": len(sentences),
           "conditions": {c: {"utterances": a["n"], "WER": round(a["err"] / a["words"], 3), "key_term_recall": round(a["terms_hit"] / max(a["terms_total"], 1), 3), "language_id_correct": round(a["lang_ok"] / a["n"], 3),
                              "mean_language_probability": round(a["lang_p"] / a["n"], 3), "real_time_factor": round(a["sec"] / max(a["audio"], 1e-9), 2)} for c, a in agg.items()},
           "numbers": {"utterances": nums["n"], "values_expected": nums["expected"], "values_recovered": nums["found"], "recovery_rate": round(nums["found"] / nums["expected"], 3), "missed": nums["missed"]},
           "silence_and_noise_only": {**silence, "no_speech_rate": round(silence["no_speech"] / silence["clips"], 3)}}
    return out


def tts_section() -> dict:
    t = adapters.get_tts()
    if not t.status()["available"]:
        return {"tested": False, "reason": "no offline voice is installed"}
    langs = t.languages()
    return {"tested": True, "voices": [v["name"] for v in t.voices()], "language_has_a_suitable_voice": langs,
            "hindi": "a Hindi voice is installed" if langs.get("hi") else "no Hindi voice is installed: Hindi text is shown, never read with an English voice",
            "hinglish": "Hinglish is read with the English voice and may be mispronounced; not evaluated"}


def synthetic_explanations(n: int, seed: int) -> list[tuple[dict, dict]]:
    """(fields, facts, meds) bundles of random synthetic lab results with the facts a report prints."""
    rng = random.Random(seed)
    tests = [("Ferritin", "ng/mL", (3, 400)), ("Hemoglobin", "g/dL", (6, 18)), ("TSH", "mIU/L", (0.1, 12)), ("Glucose Fasting", "mg/dL", (60, 250)), ("Creatinine", "mg/dL", (0.4, 3)),
             ("Vitamin D", "ng/mL", (5, 80)), ("Platelet Count", "10^3/uL", (50, 600)), ("Uric Acid", "mg/dL", (2, 10))]
    out = []
    for k in range(n):
        fields = []
        for i in range(rng.randint(1, 25)):
            name, unit, (lo, hi) = rng.choice(tests)
            v = round(rng.uniform(lo, hi), rng.choice([0, 1]))
            v = int(v) if v == int(v) else v
            a, b = round(lo * 1.5, 1), round(hi * 0.6, 1)
            ref = f"{a} - {b}"
            flag = rng.choice(["printed_flag_low", "printed_flag_high", "within_printed_range", "outside_printed_range", ""])
            src = f"{name}  {v}  {unit}  {ref}" + ("  L" if flag == "printed_flag_low" else "  H" if flag == "printed_flag_high" else "")
            fields.append(dict(id=f"f{k}-{i}", name=name, value=str(v), unit=unit, ref_range=ref, source_page=rng.randint(1, 3), source_text=src, confidence=0.9, flag=flag, review_status="unreviewed",
                               processing_version="eval", provenance="{}", ambiguity="[]"))
        facts = [dict(id=f"fa{k}", kind="report_date", label="Report date", value=f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/2025", source_page=1, source_text="Reported: x", confidence=0.8, provenance="{}",
                      normalized="{}", ambiguity='["day_month_order_ambiguous"]', review_status="unreviewed")]
        facts[0]["source_text"] = f"Reported: {facts[0]['value']}"
        out.append((fields, facts))
    return out


def spoken_section(n: int) -> dict:
    bad, checked, truncated, sentences = [], 0, 0, 0
    pres_bad, pres_checked = [], 0
    en_cache = {}
    for lang in ("en", "hi", "hinglish"):
        for k, (fields, facts) in enumerate(synthetic_explanations(n, seed=17)):
            ex = explain.explain_document({"id": "d"}, fields, [{"method": "embedded", "ocr_confidence": None}], lang, facts=facts)
            for cap in (12, 40):
                sc = spoken.script(ex, max_items=cap)
                checked += 1; sentences += len(sc.sentences); truncated += not sc.complete
                bad += [f"{lang}#{k} cap {cap}: {b}" for b in spoken.check(ex, sc)]
            if lang == "en":
                en_cache[k] = ex
            else:
                by = lambda e, i: next((s for s in e["sections"] if s.get("id") == i), {"items": []})           # noqa: E731
                for sid in ("found", "facts"):
                    sa, sb = by(en_cache[k], sid), by(ex, sid)
                    for a, b in zip(sa["items"], sb["items"]):
                        pres_checked += 1
                        names = [f["name"] for f in fields if f["name"] in a["text"]]
                        miss = multilingual.preserved(a["text"], b["text"], names=names)
                        if miss:
                            pres_bad.append(f"{lang}#{k}: {miss}")
    return {"explanations": n, "scripts_checked": checked, "sentences_checked": sentences, "scripts_truncated_and_announced": truncated, "fidelity_violations": len(bad), "violations": bad[:20],
            "translated_statements_checked": pres_checked, "protected_tokens_lost": len(pres_bad), "lost": pres_bad[:20]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../docs/eval")
    ap.add_argument("--quick", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    report = common.header("voice", "synthetic_fixture", {"quick": a.quick, "stt_limits": adapters.stt_limits().__dict__},
                           dataset={"speech": "offline operating-system voices, English only, optionally degraded", "explanations": "random synthetic lab results"},
                           notes=["Synthetic speech from offline OS voices: not real patients, accents, Hindi or Hinglish.", "Languages without an installed voice are reported as untested, never as passing.",
                                  "No claim of reliable Hindi or Hinglish is made anywhere in this system: speech recognition for them was not tested, and the fixed Hindi/Hinglish explanation templates are unreviewed."])
    report["speech_recognition"] = stt_section(a.quick)
    report["text_to_speech"] = tts_section()
    report["spoken_explanations"] = spoken_section(20 if a.quick else 60)
    report["language_status"] = {l: multilingual.language_status(l) for l in ("en", "hi", "hinglish")}
    report["seconds"] = round(time.time() - t0, 1)
    common.write_report(Path(a.out) / "voice.json", report)
    s = report["speech_recognition"]
    print(json.dumps({"stt": s if not s.get("tested") else {"clean_WER": s["conditions"]["clean"]["WER"], "telephone_WER": s["conditions"]["telephone"]["WER"], "numbers_recovered": s["numbers"]["recovery_rate"],
                                                              "silence_no_speech_rate": s["silence_and_noise_only"]["no_speech_rate"], "language_id_clean": s["conditions"]["clean"]["language_id_correct"]},
                      "tts": report["text_to_speech"].get("language_has_a_suitable_voice"), "spoken": {k: report["spoken_explanations"][k] for k in ("scripts_checked", "fidelity_violations", "protected_tokens_lost")}}, indent=1))


if __name__ == "__main__":
    main()
