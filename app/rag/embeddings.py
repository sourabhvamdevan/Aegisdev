"""
Local embeddings.

Privacy-first requirement: embeddings must be produced locally with no paid API.

Two backends:
  * "sentence-transformers" (default when installed + model available) using the
    small all-MiniLM-L6-v2 model. Fully local, CPU-friendly, free.
  * "hashing" fallback: a deterministic bag-of-hashed-ngrams vector. This needs
    no model download, which keeps CI hermetic and guarantees the offline local
    mode works even before any model is fetched. It is weaker semantically but
    keeps the whole pipeline runnable everywhere.

The active backend is reported to the UI/API so the user always knows how their
data is being processed (a stated requirement).
"""
from __future__ import annotations

import hashlib
import math
import os
import re
from typing import List

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class Embedder:
    def __init__(self, prefer: str | None = None):
        self.backend = "hashing"
        self.dim = 512
        self._model = None
        prefer = prefer or os.environ.get("EMBEDDING_BACKEND", "auto")

        if prefer in ("auto", "sentence-transformers"):
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore

                model_name = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
                self._model = SentenceTransformer(model_name)
                self.dim = self._model.get_sentence_embedding_dimension()
                self.backend = "sentence-transformers"
            except Exception:
                # Any failure (not installed, no model, offline) -> fallback.
                self._model = None
                self.backend = "hashing"

    def _hash_embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dim
        tokens = _TOKEN_RE.findall(text.lower())
        for i in range(len(tokens)):
            unigram = tokens[i]
            bigram = tokens[i - 1] + "_" + tokens[i] if i > 0 else unigram
            for feat in (unigram, bigram):
                h = int(hashlib.md5(feat.encode(), usedforsecurity=False).hexdigest(), 16)
                idx = h % self.dim
                sign = 1.0 if (h >> 1) % 2 == 0 else -1.0
                vec[idx] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed(self, texts: List[str]) -> List[List[float]]:
        if self._model is not None:
            arr = self._model.encode(texts, normalize_embeddings=True)
            return [list(map(float, row)) for row in arr]
        return [self._hash_embed(t) for t in texts]

    def embed_one(self, text: str) -> List[float]:
        return self.embed([text])[0]
