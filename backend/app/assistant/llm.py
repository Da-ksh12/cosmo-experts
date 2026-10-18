"""Self-hosted language-model backends behind one interface. Nothing here calls a commercial API.

Backends:
* LlamaCppBackend      - in-process llama.cpp (GGUF) via llama-cpp-python, CPU.
* OpenAICompatBackend  - any self-hosted OpenAI-compatible server (llama.cpp server, Ollama, vLLM) at a URL YOU control.
The application never requires one specific vendor; with no backend configured the assistant stays extractive."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass

from ..config import MODEL_DIR


class LLMError(Exception):
    pass


@dataclass
class Generation:
    text: str
    seconds: float
    completion_tokens: int
    model: str


class LLMBackend(ABC):
    name = "base"

    @abstractmethod
    def generate(self, system: str, user: str, max_tokens: int = 400, json_schema: dict | None = None, timeout: float = 120) -> Generation: ...

    def status(self) -> dict:
        return {"available": True, "backend": self.name}


class LlamaCppBackend(LLMBackend):
    def __init__(self, path: str, n_ctx: int = 4096, threads: int = 2):
        self.path, self.n_ctx, self.threads = path, n_ctx, threads
        self.name = "llama.cpp:" + os.path.basename(path)
        self._llm = None
        self._lock = threading.Lock()   # one generation at a time per process: bounded CPU/RAM

    def _load(self):
        if self._llm is None:
            from llama_cpp import Llama
            self._llm = Llama(model_path=self.path, n_ctx=self.n_ctx, n_threads=self.threads, verbose=False)
        return self._llm

    def generate(self, system, user, max_tokens=400, json_schema=None, timeout=120):
        t = time.time()
        with self._lock:
            llm = self._load()
            kw = {"response_format": {"type": "json_object", "schema": json_schema}} if json_schema else {}
            out = llm.create_chat_completion(messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                                             max_tokens=max_tokens, temperature=0.0, **kw)
        dt = time.time() - t
        if dt > timeout:
            raise LLMError(f"generation exceeded {timeout}s")
        return Generation(out["choices"][0]["message"]["content"], dt, out["usage"]["completion_tokens"], self.name)


def _url_allowed(url: str) -> bool:
    """https anywhere, or plaintext http only to a loopback/private IP literal or 'localhost' (parsed, not prefix-matched)."""
    import ipaddress
    from urllib.parse import urlparse
    u = urlparse(url)
    if u.scheme == "https" and u.hostname:
        return True
    if u.scheme != "http" or not u.hostname:
        return False
    if u.hostname == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(u.hostname)
    except ValueError:
        return False                      # a hostname such as 10.0.0.5.evil.com is not an IP literal
    return ip.is_loopback or ip.is_private


class OpenAICompatBackend(LLMBackend):
    def __init__(self, base_url: str, model: str):
        if not _url_allowed(base_url):
            raise LLMError("Refusing a non-local plaintext LLM URL.")
        self.base_url, self.model, self.name = base_url.rstrip("/"), model, f"openai-compat:{model}"

    def generate(self, system, user, max_tokens=400, json_schema=None, timeout=120):
        t = time.time()
        body = {"model": self.model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": max_tokens, "temperature": 0}
        if json_schema:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(self.base_url + "/v1/chat/completions", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - operator-configured self-hosted URL
                j = json.loads(r.read())
        except Exception as e:
            raise LLMError(str(e))
        return Generation(j["choices"][0]["message"]["content"], time.time() - t, j.get("usage", {}).get("completion_tokens", 0), self.name)


_backend: LLMBackend | None | bool = False


def get_backend() -> LLMBackend | None:
    """COSMO_LLM_URL (+COSMO_LLM_MODEL) or COSMO_LLM_GGUF, or the default GGUF directory if a model has been placed there."""
    global _backend
    if _backend is False:
        url, gguf = os.environ.get("COSMO_LLM_URL"), os.environ.get("COSMO_LLM_GGUF")
        if url:
            _backend = OpenAICompatBackend(url, os.environ.get("COSMO_LLM_MODEL", "local"))
        else:
            gguf = gguf or next((str(p) for p in sorted((MODEL_DIR / "llm").glob("*.gguf"))), None) if (MODEL_DIR / "llm").exists() else gguf
            try:
                import llama_cpp  # noqa: F401
                _backend = LlamaCppBackend(gguf) if gguf and os.path.isfile(gguf) and os.environ.get("COSMO_LLM_ENABLED") == "1" else None
            except ImportError:
                _backend = None
    return _backend or None


def reset_backend(b: LLMBackend | None | bool = False):
    global _backend
    _backend = b
