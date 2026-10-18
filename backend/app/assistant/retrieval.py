"""Retrieval layer: replaceable dense embedder + lexical scorer + hybrid fusion.

Access control happens BEFORE retrieval: callers pass only passages the requesting user may see. Dense vectors are cached by content hash,
so changed/added passages are re-embedded and unchanged ones are not (this is the re-indexing mechanism)."""
from __future__ import annotations

import hashlib
import os
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from ..config import MODEL_DIR

QUERY_PREFIX = "Represent this sentence for searching relevant passages: "   # BGE retrieval convention


class Embedder(ABC):
    name = "base"

    @abstractmethod
    def embed_passages(self, texts: list[str]) -> np.ndarray: ...

    @abstractmethod
    def embed_query(self, text: str) -> np.ndarray: ...


class SentenceTransformerEmbedder(Embedder):
    """Local sentence-transformers model, loaded with local_files_only (never downloads at request time)."""

    def __init__(self, path: str | None = None):
        self.path = path or os.environ.get("COSMO_EMBED_MODEL_DIR") or str(MODEL_DIR / "embed" / "bge-small-en-v1.5")
        self.name = "st:" + os.path.basename(self.path)
        self._m = None
        self._lock = threading.Lock()

    @property
    def available(self) -> bool:
        return os.path.isfile(os.path.join(self.path, "model.safetensors")) or os.path.isfile(os.path.join(self.path, "pytorch_model.bin"))

    def _model(self):
        with self._lock:
            if self._m is None:
                from sentence_transformers import SentenceTransformer
                self._m = SentenceTransformer(self.path, device="cpu", local_files_only=True)
            return self._m

    def embed_passages(self, texts):
        return np.asarray(self._model().encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False))

    def embed_query(self, text):
        return np.asarray(self._model().encode([QUERY_PREFIX + text], normalize_embeddings=True, show_progress_bar=False))[0]


# Abstention thresholds measured by eval/retrieval_eval.py (max F1 subject to <=5% unanswerable-wrongly-answered; 10 synthetic docs,
# 140 answerable + 120 unanswerable queries). Re-run the evaluation after changing models, weights or corpus.
CALIBRATED_THRESHOLDS = {0.0: 0.166, 0.3: 0.301, 0.5: 0.372, 0.7: 0.472, 1.0: 0.676}


@dataclass
class Hit:
    index: int
    score: float
    dense: float
    lexical: float


class HybridRetriever:
    def __init__(self, embedder: Embedder | None = None, dense_weight: float = 0.5):
        self.embedder = embedder
        self.dense_weight = dense_weight if embedder else 0.0
        self._cache: dict[str, np.ndarray] = {}
        self.embedded_count = 0          # observable for tests: how many passages were actually (re-)embedded

    @property
    def threshold(self) -> float:
        """Minimum top score for answering; below it the assistant abstains."""
        w = round(self.dense_weight, 1)
        return CALIBRATED_THRESHOLDS.get(w, max(CALIBRATED_THRESHOLDS.values()))   # unknown weight => most conservative

    @property
    def mode(self) -> str:
        return "lexical" if not self.embedder or self.dense_weight == 0 else "dense" if self.dense_weight == 1.0 else f"hybrid({self.dense_weight})"

    def _vectors(self, texts: list[str]) -> np.ndarray:
        keys = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
        missing = [(k, t) for k, t in zip(keys, texts) if k not in self._cache]
        if missing:
            uniq = list({k: t for k, t in missing}.items())
            vecs = self.embedder.embed_passages([t for _, t in uniq])
            for (k, _), v in zip(uniq, vecs):
                self._cache[k] = v
            self.embedded_count += len(uniq)
        return np.stack([self._cache[k] for k in keys]) if keys else np.zeros((0, 1))

    def search(self, query: str, passages: list[dict], k: int = 5) -> list[Hit]:
        if not passages:
            return []
        lex_docs = [p.get("match", p["text"]) for p in passages]
        try:
            vec = TfidfVectorizer(stop_words="english", sublinear_tf=True)
            m = vec.fit_transform(lex_docs + [query])
            lex = (m[:-1] @ m[-1].T).toarray().ravel()
        except ValueError:
            lex = np.zeros(len(passages))
        if self.embedder and self.dense_weight > 0:
            dense = self._vectors([p["text"] for p in passages]) @ self.embedder.embed_query(query)
            dense = np.clip(dense, 0, 1)
        else:
            dense = np.zeros(len(passages))
        w = self.dense_weight
        score = w * dense + (1 - w) * lex
        order = np.argsort(-score)[:k]
        return [Hit(int(i), float(score[i]), float(dense[i]), float(lex[i])) for i in order]


_default: HybridRetriever | None = None


def get_retriever() -> HybridRetriever:
    """Configuration chosen from measured results (see docs/eval/retrieval.md); COSMO_RETRIEVAL=tfidf forces lexical only."""
    global _default
    if _default is None:
        emb = SentenceTransformerEmbedder()
        mode = os.environ.get("COSMO_RETRIEVAL", "hybrid")
        use = emb if (mode != "tfidf" and emb.available) else None
        _default = HybridRetriever(use, float(os.environ.get("COSMO_DENSE_WEIGHT", "1.0")))
    return _default
