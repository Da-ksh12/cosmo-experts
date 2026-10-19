"""Voice (app/speech): bounded recordings, no made-up sentence from silence, language metadata, cancellation, provenance, no retention of the audio or the transcript, and a voice that is
never used for a language it cannot speak. Tests that need the local speech model or an installed voice skip when those are absent; nothing here claims anything about real patients' speech."""
import io
import json
import os
import tempfile
import threading
import time
import wave
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app import db
from app.ai.errors import Cancelled
from app.ai.engine import get_engine, reset_engine
from app.ai.resources import CancelToken
from app.main import app
from app.speech import adapters

MODEL = adapters.stt_status()["available"]
needs_model = pytest.mark.skipif(not MODEL, reason="local speech model is not installed")


def wav_bytes(seconds: float, rate: int = 16000, noise: float = 0.0, seed: int = 0) -> bytes:
    x = (np.random.default_rng(seed).normal(0, noise, int(seconds * rate)) * 32767).astype(np.int16) if noise else np.zeros(int(seconds * rate), np.int16)
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate); w.writeframes(x.tobytes())
    return b.getvalue()


def speech_wav(text: str) -> bytes | None:
    """A synthetic English utterance from an installed offline voice, or None."""
    try:
        import pyttsx3
        voices = [v for v in pyttsx3.init().getProperty("voices") if "english" in v.name.lower() or "en" in [str(l)[:2] for l in (v.languages or [])]]
        if not voices:
            return None
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        e = pyttsx3.init()
        e.setProperty("voice", voices[0].id)
        e.save_to_file(text, path)
        e.runAndWait()
        data = Path(path).read_bytes()
        os.remove(path)
        return data or None
    except Exception:
        return None


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def tok(c, u):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"user_id": u, "password": "cosmo-dev"}).json()["token"]}


# ---------------------------------------------------------------- limits (no model needed)
def test_limits_are_bounded_by_default_and_by_configuration(monkeypatch):
    lim = adapters.stt_limits()
    assert 0 < lim.max_bytes <= 25 * 1024 * 1024 and 0 < lim.max_seconds <= 300
    monkeypatch.setenv("COSMO_STT_MAX_SECONDS", "7"); monkeypatch.setenv("COSMO_STT_MAX_BYTES", "12345")
    assert (adapters.stt_limits().max_seconds, adapters.stt_limits().max_bytes) == (7, 12345)
    monkeypatch.setenv("COSMO_STT_MAX_SECONDS", "banana"); monkeypatch.setenv("COSMO_STT_MAX_BYTES", "-5")
    assert adapters.stt_limits() == lim                                                          # a bad setting never removes a bound
    monkeypatch.setenv("COSMO_STT_MAX_SECONDS", "999999")
    assert adapters.stt_limits().max_seconds <= 300                                                # nor does a huge one


def test_an_empty_or_oversize_recording_is_refused_before_anything_is_decoded(monkeypatch):
    with pytest.raises(adapters.AudioError, match="empty"):
        adapters.transcribe_detail(b"", "en")
    monkeypatch.setenv("COSMO_STT_MAX_BYTES", "100")
    with pytest.raises(adapters.AudioTooLarge, match="too large"):
        adapters.transcribe_detail(b"x" * 101, "en")
    assert issubclass(adapters.AudioTooLarge, adapters.AudioError) and issubclass(adapters.AudioTooLong, adapters.AudioError) and issubclass(adapters.NoSpeech, adapters.AudioError)


def test_a_pre_cancelled_request_never_reaches_the_model():
    tok_ = CancelToken()
    tok_.cancel()
    with pytest.raises(Cancelled):
        adapters.transcribe_detail(wav_bytes(1), "en", cancel=tok_)


# ---------------------------------------------------------------- with the local model
@needs_model
def test_a_recording_over_the_duration_limit_is_refused_before_it_is_transcribed(monkeypatch):
    monkeypatch.setenv("COSMO_STT_MAX_SECONDS", "3")
    t0 = time.time()
    with pytest.raises(adapters.AudioTooLong, match="longer than 3 seconds"):
        adapters.transcribe_detail(wav_bytes(8, noise=0.01), "en")
    assert time.time() - t0 < 20


@needs_model
def test_silence_is_no_speech_and_not_a_sentence_the_model_made_up():
    for audio in (wav_bytes(3), wav_bytes(3, noise=0.002), wav_bytes(4, noise=0.01, seed=3)):
        with pytest.raises(adapters.NoSpeech, match="didn't hear"):
            adapters.transcribe_detail(audio, "en")


@needs_model
def test_an_undecodable_recording_is_an_audio_error_with_a_safe_message():
    with pytest.raises(adapters.AudioError) as e:
        adapters.transcribe_detail(b"this is not audio at all, just bytes " * 20, "en")
    assert "couldn't read that recording" in str(e.value) and "av." not in str(e.value) and "Traceback" not in str(e.value)


@needs_model
def test_speech_gives_text_language_metadata_and_provenance_and_keeps_nothing():
    audio = speech_wav("What is a reference range on my report?")
    if audio is None:
        pytest.skip("no offline English voice to make a test recording")
    before = set(os.listdir(tempfile.gettempdir()))
    t = adapters.transcribe_detail(audio, None)
    assert "reference range" in t.text.lower()            # (medical terms are measured in eval/voice_eval.py; the model heard 'ferritin' as 'Faradon' - which is why the person confirms the text)
    assert t.language == "en" and t.language_requested is None and t.language_probability > 0.5 and t.audio_seconds > 1
    assert t.model.startswith("faster-whisper") and t.processing_version.startswith("stt-") and t.retention == {"audio": "deleted immediately after decoding", "transcript": "returned to the person for confirmation; not stored"}
    assert not [f for f in set(os.listdir(tempfile.gettempdir())) - before if f.endswith(".webm")]            # the temporary copy of the audio is gone
    assert "reference range" in adapters.transcribe(audio, "en").lower()                                          # the older text-only entry point still works
    mismatch = adapters.transcribe_detail(audio, "hi")
    assert any("language" in w for w in mismatch.warnings)                                                       # asked for Hindi, heard English: said so, not hidden


@needs_model
def test_the_engine_and_the_api_return_the_transcript_for_confirmation_only(client):
    audio = speech_wav("What is a reference range?")
    if audio is None:
        pytest.skip("no offline English voice to make a test recording")
    reset_engine()
    env = get_engine().run("speech.transcribe", {"audio": audio, "language": "en"}, user={"id": "patient1", "role": "patient", "name": "P"})
    assert env.status == "review_required" and env.result["needs_confirmation"] is True and env.result["language"]["detected"] == "en"
    assert "reference" in env.result["text"].lower() and env.result["retention"]["audio"].startswith("deleted")
    h = tok(client, "patient1")
    r = client.post("/api/speech/transcribe", data={"language": "en"}, files={"file": ("q.wav", audio)}, headers=h)
    assert r.status_code == 200 and r.json()["needs_confirmation"] is True and "reference" in r.json()["text"].lower() and r.json()["language"]["detected"] == "en"
    silent = client.post("/api/speech/transcribe", data={"language": "en"}, files={"file": ("q.wav", wav_bytes(3))}, headers=h)
    assert silent.status_code == 422 and "didn't hear" in silent.json()["detail"]
    empty = client.post("/api/speech/transcribe", files={"file": ("q.wav", b"")}, headers=h)
    assert empty.status_code in (400, 422)
    assert not any("ferritin" in (a["detail"] or "").lower() or "reference range" in (a["detail"] or "").lower() for a in db.q("SELECT detail FROM audit"))      # no transcript in the audit trail


# ---------------------------------------------------------------- text to speech
def test_the_languages_a_voice_can_speak_are_reported_honestly():
    langs = adapters.get_tts().languages()
    assert set(langs) >= {"en", "hi"} and all(isinstance(v, bool) for v in langs.values())
    if not langs["hi"]:
        with pytest.raises(RuntimeError, match="No installed offline voice supports"):
            adapters.get_tts().synthesize("नमस्ते", "hi-IN", 1.0)                                  # refused, never read with another language's voice


def test_the_language_report_claims_nothing_that_was_not_checked(client):
    r = client.get("/api/v1/ai/speech/languages", headers=tok(client, "patient1"))
    assert r.status_code == 200
    j = r.json()
    assert j["languages"]["en"]["speech_recognition"].startswith("checked on synthetic English speech only")
    for lang in ("hi", "hinglish"):
        e = j["languages"][lang]
        assert e["speech_recognition"] == "not checked" and "UNREVIEWED" in e["explanations"] and e["reviewed_translations"] is False and "no claim" in e["claim"]
        assert "voice_installed" in e
    assert client.get("/api/v1/ai/speech/languages").status_code == 401
