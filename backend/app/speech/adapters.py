"""Replaceable local speech adapters. Nothing here calls a network service.

TTS: pyttsx3 (OS offline voices: SAPI5 on Windows, NSSpeechSynthesizer on macOS, eSpeak on Linux). A voice is only ever used for a language it can speak.
STT: faster-whisper, only if installed AND the model is already present locally (local_files_only).

Speech recognition is bounded and honest about what it heard:
* a recording over the byte limit, or empty, is refused before anything is decoded; one over the duration limit is refused right after decoding and before any transcription;
* silence, or noise the model itself marks as "no speech", is reported as no speech - never as a sentence the model made up;
* the result carries the language heard (and the language asked for, if different), the length of the audio, the model and processing version;
* the audio exists only as a temporary file for decoding and is deleted at once; the transcript is returned for the person to confirm and is not stored;
* a cancelled or timed-out request stops between segments."""
from __future__ import annotations

import os
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class TTSAdapter(ABC):
    name = "base"

    @abstractmethod
    def status(self) -> dict: ...

    @abstractmethod
    def synthesize(self, text: str, language: str, rate: float) -> bytes: ...

    def languages(self) -> dict[str, bool]:
        """For each language the system offers: is there an installed voice that can speak it?"""
        return {}


import concurrent.futures as _cf

# pyttsx3 uses COM (SAPI5) on Windows, whose objects are only usable on the thread that created them. Request handlers run on a thread pool,
# so every pyttsx3 call is marshalled onto ONE dedicated thread. (Before this, voice availability differed between threads.)
_tts_thread = _cf.ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")


class Pyttsx3Adapter(TTSAdapter):
    name = "pyttsx3-offline"

    def _engine(self):
        import pyttsx3
        return pyttsx3.init()

    def voices(self):
        return _tts_thread.submit(self._voices).result(timeout=30)

    def _voices(self):
        try:
            e = self._engine()
            out = [{"id": v.id, "name": v.name, "languages": [str(l) for l in (v.languages or [])]} for v in e.getProperty("voices")]
            del e                                                                  # release the COM object on this (the TTS) thread
            return out
        except Exception:
            return []

    def status(self) -> dict:
        v = self.voices()
        if not v:
            return {"available": False, "adapter": self.name, "voices": [],
                    "admin_message": "No offline text-to-speech voice is installed. Install an OS voice (or eSpeak NG on Linux). "
                                     "Patients will see text only; the browser's on-device voice may be used if the device has one."}
        return {"available": True, "adapter": self.name, "voices": v}

    LANG_NAMES = {"en": "english", "hi": "hindi"}
    _voice_cache: list[dict] | None = None

    def _pick_voice(self, voices: list[dict], language: str):
        """Match on language TAGS (or the voice's display name), never on substrings of the registry path."""
        want = language.split("-")[0].lower()
        for v in voices:
            tags = {l.replace("_", "-").split("-")[0].lower() for l in v["languages"]}
            if want in tags or (want in self.LANG_NAMES and self.LANG_NAMES[want] in v["name"].lower()):
                return v["id"]
        return None

    def languages(self) -> dict[str, bool]:
        voices = self.voices()
        return {lang: self._pick_voice(voices, lang) is not None for lang in ("en", "hi")}

    def synthesize(self, text: str, language: str, rate: float = 1.0) -> bytes:
        if self._voice_cache is None:
            self._voice_cache = self.voices()
        vid = self._pick_voice(self._voice_cache, language)
        if vid is None:
            # Never read a language with a voice that cannot speak it (e.g. Devanagari through an English voice). Checked BEFORE any COM object exists.
            raise RuntimeError(f"No installed offline voice supports '{language}'.")
        return _tts_thread.submit(self._synthesize, text, vid, rate).result(timeout=120)

    def _synthesize(self, text: str, voice_id: str, rate: float) -> bytes:
        import gc
        e = self._engine()
        try:
            e.setProperty("voice", voice_id)
            e.setProperty("rate", int(e.getProperty("rate") * max(0.5, min(2.0, rate))))
            fd, path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            try:
                e.save_to_file(text[:5000], path)
                e.runAndWait()
                with open(path, "rb") as f:
                    data = f.read()
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            if not data:
                raise RuntimeError("tts produced no audio")
            return data
        finally:
            del e          # release the COM object here, on the TTS thread
            gc.collect()


_tts: TTSAdapter = Pyttsx3Adapter()


def get_tts() -> TTSAdapter:
    return _tts


def set_tts(adapter: TTSAdapter):
    global _tts
    _tts = adapter


# ---------------------------------------------------------------- speech recognition
PROCESSING_VERSION = "stt-2.0.0"
MODEL_LABEL = "faster-whisper base (local, CPU, int8)"
DEFAULT_MAX_BYTES, HARD_MAX_BYTES = 10 * 1024 * 1024, 25 * 1024 * 1024
DEFAULT_MAX_SECONDS, HARD_MAX_SECONDS = 60, 300
SAMPLE_RATE = 16000
MIN_SECONDS = 0.3
SILENCE_RMS = 0.003                                     # digital silence / a dead microphone: nothing is sent to the model
NO_SPEECH_PROB, LOW_LOGPROB = 0.6, -1.0                 # the model's own "this segment is not speech" criteria (a segment must show both to be dropped)
RETENTION = {"audio": "deleted immediately after decoding", "transcript": "returned to the person for confirmation; not stored"}


@dataclass(frozen=True)
class SttLimits:
    max_bytes: int
    max_seconds: int


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        v = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return default if v < lo else min(v, hi)           # a bad setting never removes a bound; a huge one is capped


def stt_limits() -> SttLimits:
    return SttLimits(_env_int("COSMO_STT_MAX_BYTES", DEFAULT_MAX_BYTES, 1, HARD_MAX_BYTES), _env_int("COSMO_STT_MAX_SECONDS", DEFAULT_MAX_SECONDS, 1, HARD_MAX_SECONDS))


def _model_dir() -> str | None:
    """COSMO_WHISPER_MODEL_DIR, else the default local model location if it has been downloaded."""
    from ..config import MODEL_DIR
    d = os.environ.get("COSMO_WHISPER_MODEL_DIR") or str(MODEL_DIR / "stt" / "faster-whisper-base")
    return d if os.path.isfile(os.path.join(d, "model.bin")) else None


def stt_status() -> dict:
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return {"available": False,
                "admin_message": "Local speech recognition is not installed. Install faster-whisper and place a licensed model "
                                 "locally (see docs/SPEECH.md). Text questions work without it."}
    model_dir = _model_dir()
    if not model_dir:
        return {"available": False, "admin_message": "Place a licensed faster-whisper model in data/models/stt/faster-whisper-base or set COSMO_WHISPER_MODEL_DIR."}
    lim = stt_limits()
    return {"available": True, "adapter": "faster-whisper (local, base, int8)", "languages_verified": ["en (synthetic speech only)"],
            "limits": {"max_seconds": lim.max_seconds, "max_bytes": lim.max_bytes}, "note": "Hindi/Hinglish recognition has not been verified."}


_model = None


def _get_model():
    """Load the local Whisper model once. local_files_only: the app never downloads weights at request time."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(_model_dir(), device="cpu", compute_type="int8", cpu_threads=2, local_files_only=True)
    return _model


def transcribe_file(path: str, language: str | None) -> tuple[str, float, str]:
    """Returns (text, audio_seconds, detected_language). Used by the evaluation scripts; the application uses transcribe_detail."""
    segs, info = _get_model().transcribe(path, language=language, beam_size=1, vad_filter=False, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segs).strip(), float(info.duration), info.language


class AudioError(RuntimeError):
    """The recording could not be used. The message is safe to show to the user."""


class AudioTooLarge(AudioError):
    pass


class AudioTooLong(AudioError):
    pass


class NoSpeech(AudioError):
    pass


@dataclass
class Transcript:
    text: str
    language_requested: str | None
    language: str                       # the language the recording sounds like
    language_probability: float
    audio_seconds: float
    model: str = MODEL_LABEL
    processing_version: str = PROCESSING_VERSION
    warnings: list[str] = field(default_factory=list)
    retention: dict = field(default_factory=lambda: dict(RETENTION))

    def public(self) -> dict:
        return {"text": self.text, "needs_confirmation": True, "language": {"requested": self.language_requested, "detected": self.language, "probability": self.language_probability},
                "audio_seconds": self.audio_seconds, "provenance": {"model": self.model, "processing_version": self.processing_version}, "retention": self.retention, "warnings": self.warnings}


def transcribe_detail(audio: bytes, language: str | None, cancel=None) -> Transcript:
    """Audio is written to a temp file only for decoding and deleted immediately (no retention). `cancel`: an ai.resources.CancelToken, checked between steps and segments."""
    lim = stt_limits()
    if not audio:
        raise AudioError("The recording is empty. Please try again.")
    if len(audio) > lim.max_bytes:
        size = f"{lim.max_bytes // (1024 * 1024)} MB" if lim.max_bytes >= 1024 * 1024 else f"{max(1, lim.max_bytes // 1024)} KB"
        raise AudioTooLarge(f"The recording is too large. The limit is {size}.")
    if cancel is not None:
        cancel.raise_if_cancelled()
    st = stt_status()
    if not st["available"]:
        raise RuntimeError(st["admin_message"])
    fd, path = tempfile.mkstemp(suffix=".webm")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        try:
            return _transcribe_path(path, language, lim, cancel)
        except AudioError:
            raise
        except Exception as e:
            if type(e).__module__.startswith("av") or isinstance(e, (ValueError, OSError)):
                raise AudioError("We couldn't read that recording. Please try again or type your question.") from e
            raise
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _transcribe_path(path: str, language: str | None, lim: SttLimits, cancel) -> Transcript:
    import numpy as np
    from faster_whisper.audio import decode_audio
    audio = decode_audio(path, sampling_rate=SAMPLE_RATE)
    seconds = len(audio) / SAMPLE_RATE
    if seconds > lim.max_seconds:                                                    # before any transcription work
        raise AudioTooLong(f"The recording is longer than {lim.max_seconds} seconds. Please record a shorter question.")
    nospeech = NoSpeech("We didn't hear any speech in that recording. Please try again or type your question.")
    if seconds < MIN_SECONDS or float(np.sqrt(np.mean(np.square(audio)))) < SILENCE_RMS:
        raise nospeech
    if cancel is not None:
        cancel.raise_if_cancelled()
    model = _get_model()
    want = (language or "").split("-")[0].lower() or None
    warnings = []
    heard = prob = None
    if want:                                                                         # the person chose a language: also LISTEN for the language, so a mismatch is said aloud, not hidden
        heard, prob, _ = model.detect_language(audio)
        if heard != want and prob >= 0.6:
            warnings.append(f"language: you chose '{want}' but the recording sounds like '{heard}'. Check the text carefully.")
        if cancel is not None:
            cancel.raise_if_cancelled()
    segs, info = model.transcribe(audio, language=want, beam_size=1, vad_filter=False, condition_on_previous_text=False)
    if heard is None:                                                                # nothing chosen: the single decoding pass also tells which language it heard
        heard, prob = info.language, info.language_probability
    kept = []
    for s in segs:                                                                   # transcription happens as this generator is consumed
        if cancel is not None:
            cancel.raise_if_cancelled()
        if getattr(s, "no_speech_prob", 0.0) > NO_SPEECH_PROB and getattr(s, "avg_logprob", 0.0) < LOW_LOGPROB:
            continue                                                                  # the model itself says this is not speech: do not turn it into words
        t = s.text.strip()
        if t:
            kept.append(t)
    text = " ".join(kept).strip()
    if not text:
        raise nospeech
    return Transcript(text=text, language_requested=language, language=heard, language_probability=round(float(prob), 3), audio_seconds=round(seconds, 2), warnings=warnings)


def transcribe(audio: bytes, language: str | None) -> str:
    """The text only (the earlier entry point). Raises the same typed errors as transcribe_detail."""
    return transcribe_detail(audio, language).text
