"""Replaceable local speech adapters. Nothing here calls a network service.

TTS: pyttsx3 (OS offline voices: SAPI5 on Windows, NSSpeechSynthesizer on macOS, eSpeak on Linux).
STT: faster-whisper, only if installed AND the model is already present locally (local_files_only)."""
from __future__ import annotations

import os
import tempfile
from abc import ABC, abstractmethod


class TTSAdapter(ABC):
    name = "base"

    @abstractmethod
    def status(self) -> dict: ...

    @abstractmethod
    def synthesize(self, text: str, language: str, rate: float) -> bytes: ...


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
            return [{"id": v.id, "name": v.name, "languages": [str(l) for l in (v.languages or [])]} for v in e.getProperty("voices")]
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

    def _voices(self):
        try:
            e = self._engine()
            out = [{"id": v.id, "name": v.name, "languages": [str(l) for l in (v.languages or [])]} for v in e.getProperty("voices")]
            del e
            return out
        except Exception:
            return []

    def _pick_voice(self, voices: list[dict], language: str):
        """Match on language TAGS (or the voice's display name), never on substrings of the registry path."""
        want = language.split("-")[0].lower()
        for v in voices:
            tags = {l.replace("_", "-").split("-")[0].lower() for l in v["languages"]}
            if want in tags or (want in self.LANG_NAMES and self.LANG_NAMES[want] in v["name"].lower()):
                return v["id"]
        return None

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
    return {"available": True, "adapter": "faster-whisper (local, base, int8)", "languages_verified": ["en (synthetic speech only)"],
            "note": "Hindi/Hinglish recognition has not been verified."}


_model = None


def _get_model():
    """Load the local Whisper model once. local_files_only: the app never downloads weights at request time."""
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        _model = WhisperModel(_model_dir(), device="cpu", compute_type="int8", cpu_threads=2, local_files_only=True)
    return _model


def transcribe_file(path: str, language: str | None) -> tuple[str, float, str]:
    """Returns (text, audio_seconds, detected_language)."""
    segs, info = _get_model().transcribe(path, language=language, beam_size=1, vad_filter=False, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segs).strip(), float(info.duration), info.language


class AudioError(RuntimeError):
    """The recording could not be decoded. The message is safe to show to the user."""


def transcribe(audio: bytes, language: str | None) -> str:
    """Audio is written to a temp file only for decoding and deleted immediately (no retention)."""
    st = stt_status()
    if not st["available"]:
        raise RuntimeError(st["admin_message"])
    fd, path = tempfile.mkstemp(suffix=".webm")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(audio)
        try:
            return transcribe_file(path, language)[0]
        except Exception as e:
            if type(e).__module__.startswith("av") or isinstance(e, (ValueError, OSError)):
                raise AudioError("We couldn't read that recording. Please try again or type your question.") from e
            raise
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
