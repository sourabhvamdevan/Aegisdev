"""
Hybrid vector + lexical store (local, zero-cost).

Backed by SQLite so it works on an ephemeral filesystem and needs no external
service. Each chunk is indexed twice:

  * dense   - vector stored as JSON, exact cosine over the candidate set
  * sparse  - SQLite FTS5 table scored with BM25 (identifier-aware, see lexical.py)

`search()` runs both and fuses the two ranked lists with Reciprocal Rank Fusion:

    score(chunk) = sum over retrievers of  w / (k + rank_in_that_retriever)    (k = 60)

with w = 1 for both retrievers, except w = 2 for BM25 when the query contains an
identifier (snake_case / camelCase): an exact lexical hit then outweighs fuzzy
semantic similarity. Additionally, a clear BM25 outlier for an identifier query
is pinned to rank 1 (exact-identifier fast path).

RRF needs no score calibration between cosine similarity and BM25 (different
scales), which is why it is the standard way to combine them. The fused score is
normalised to 0..1 (1.0 = ranked first by every active retriever) so existing
API/UI consumers keep a meaningful "score".

If this SQLite build lacks FTS5 the store silently degrades to vector-only and
reports `lexical_backend = "none"` in stats(). A FAISS/HNSW index remains a
documented scaling upgrade for the dense side.

Implements the required lifecycle operations:
  * content hashing (sha256 of chunk text) for stable IDs
  * duplicate detection (skip identical chunk hashes)
  * incremental re-indexing (re-ingesting a file replaces only its rows)
  * deletion of indexed content by source (chunk rows AND their FTS rows)
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import lexical
from .chunker import Chunk

RRF_K = 60
_CANDIDATES_FLOOR = 20
# When the query names an identifier (snake_case / camelCase) an exact lexical hit
# is far stronger evidence than semantic similarity, so BM25 gets double weight.
IDENTIFIER_LEXICAL_WEIGHT = 2.0
# Exact-identifier fast path: RRF only sees ranks, so it throws away the fact that a
# chunk containing the whole identifier scores far above every look-alike. If the
# query names an identifier and the top BM25 hit beats the runner-up by this factor,
# that hit is pinned to rank 1 and the rest are ordered by RRF.
EXACT_MATCH_GAP = 1.5


@dataclass
class Hit:
    text: str
    source: str
    locator: str
    kind: str
    score: float                           # fused (hybrid) or single-retriever score
    vector_score: Optional[float] = None   # cosine, if the dense retriever ranked it
    lexical_score: Optional[float] = None  # BM25 (higher = better), if the sparse retriever ranked it


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def rrf_fuse(
    rankings: List[List[str]], k: int = RRF_K, weights: Optional[List[float]] = None
) -> Dict[str, float]:
    """(Weighted) Reciprocal Rank Fusion over ranked lists of ids -> {id: fused score}."""
    weights = weights or [1.0] * len(rankings)
    fused: Dict[str, float] = {}
    for ranking, weight in zip(rankings, weights, strict=True):
        for rank, key in enumerate(ranking, start=1):
            fused[key] = fused.get(key, 0.0) + weight / (k + rank)
    return fused


class VectorStore:
    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        if db_path != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.lexical_enabled = False
        self._init_schema()

    @property
    def lexical_backend(self) -> str:
        return "fts5-bm25" if self.lexical_enabled else "none"

    def _init_schema(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                hash     TEXT PRIMARY KEY,
                source   TEXT NOT NULL,
                locator  TEXT NOT NULL,
                kind     TEXT NOT NULL,
                text     TEXT NOT NULL,
                vector   TEXT NOT NULL
            )
            """
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS idx_source ON chunks(source)")
        try:
            self.conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5("
                "terms, hash UNINDEXED, tokenize=\"porter unicode61 tokenchars '_'\")"
            )
            self.lexical_enabled = True
            self._backfill_fts()
        except sqlite3.OperationalError:
            self.lexical_enabled = False  # SQLite built without FTS5 -> vector-only
        self.conn.commit()

    def _backfill_fts(self) -> None:
        """Databases created before hybrid search have chunks but no FTS rows; index them once."""
        missing = self.conn.execute(
            "SELECT hash, text FROM chunks WHERE hash NOT IN (SELECT hash FROM chunks_fts)"
        ).fetchall()
        self.conn.executemany(
            "INSERT INTO chunks_fts(terms, hash) VALUES (?, ?)",
            [(lexical.expand_terms(text), h) for h, text in missing],
        )

    # -- write ops ----------------------------------------------------------
    def add(self, chunks: List[Chunk], vectors: List[List[float]]) -> dict:
        """Insert chunks. Returns counts of added vs skipped duplicates."""
        added, duplicates = 0, 0
        for chunk, vec in zip(chunks, vectors, strict=False):
            h = _sha256(chunk.text)
            exists = self.conn.execute(
                "SELECT 1 FROM chunks WHERE hash = ?", (h,)
            ).fetchone()
            if exists:
                duplicates += 1
                continue
            self.conn.execute(
                "INSERT INTO chunks(hash, source, locator, kind, text, vector) VALUES (?,?,?,?,?,?)",
                (h, chunk.source, chunk.locator, chunk.kind, chunk.text, json.dumps(vec)),
            )
            if self.lexical_enabled:
                self.conn.execute(
                    "INSERT INTO chunks_fts(terms, hash) VALUES (?, ?)",
                    (lexical.expand_terms(chunk.text), h),
                )
            added += 1
        self.conn.commit()
        return {"added": added, "duplicates": duplicates}

    def delete_source(self, source: str) -> int:
        if self.lexical_enabled:
            self.conn.execute(
                "DELETE FROM chunks_fts WHERE hash IN (SELECT hash FROM chunks WHERE source = ?)", (source,)
            )
        cur = self.conn.execute("DELETE FROM chunks WHERE source = ?", (source,))
        self.conn.commit()
        return cur.rowcount

    def reindex_source(self, source: str, chunks: List[Chunk], vectors: List[List[float]]) -> dict:
        """Incremental re-index: drop the file's old rows, then add fresh ones."""
        removed = self.delete_source(source)
        result = self.add(chunks, vectors)
        result["removed"] = removed
        return result

    # -- read ops -----------------------------------------------------------
    def _vector_ranked(self, query_vec: List[float], n: int) -> List[Tuple[str, float]]:
        rows = self.conn.execute("SELECT hash, vector FROM chunks").fetchall()
        scored = [(h, _cosine(query_vec, json.loads(v))) for h, v in rows]
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:n]

    def _lexical_ranked(self, query_text: str, n: int) -> List[Tuple[str, float]]:
        if not self.lexical_enabled:
            return []
        match = lexical.build_match_query(query_text)
        if not match:
            return []
        try:
            rows = self.conn.execute(
                "SELECT hash, bm25(chunks_fts) FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY 2 LIMIT ?",
                (match, n),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [(h, -score) for h, score in rows]  # FTS5 bm25 is "lower is better"; flip

    def _hit(self, h: str, score: float, vec: Optional[float], lex: Optional[float]) -> Hit:
        source, locator, kind, text = self.conn.execute(
            "SELECT source, locator, kind, text FROM chunks WHERE hash = ?", (h,)
        ).fetchone()
        return Hit(text=text, source=source, locator=locator, kind=kind, score=score,
                   vector_score=vec, lexical_score=lex)

    def search(
        self,
        query_vec: List[float],
        top_k: int = 5,
        *,
        query_text: Optional[str] = None,
        mode: str = "hybrid",
    ) -> List[Hit]:
        """
        mode: "hybrid" (BM25 + vector fused with RRF), "vector", or "lexical".
        Without `query_text` (or without FTS5) this is the original vector search.
        """
        pool = max(top_k * 4, _CANDIDATES_FLOOR)
        use_lexical = bool(query_text) and self.lexical_enabled and mode in ("hybrid", "lexical")
        use_vector = mode in ("hybrid", "vector") or not use_lexical

        vec_ranked = self._vector_ranked(query_vec, pool) if use_vector else []
        lex_ranked = self._lexical_ranked(query_text or "", pool) if use_lexical else []

        if not lex_ranked:  # pure vector (also the fallback when nothing matched lexically)
            ranked = vec_ranked or self._vector_ranked(query_vec, pool)
            return [self._hit(h, s, s, None) for h, s in ranked[:top_k]]
        if not vec_ranked:  # pure lexical
            top = lex_ranked[0][1] or 1.0
            return [self._hit(h, s / top, None, s) for h, s in lex_ranked[:top_k]]

        vec_score, lex_score = dict(vec_ranked), dict(lex_ranked)
        lex_weight = IDENTIFIER_LEXICAL_WEIGHT if lexical.has_identifier(query_text or "") else 1.0
        fused = rrf_fuse([[h for h, _ in vec_ranked], [h for h, _ in lex_ranked]], weights=[1.0, lex_weight])
        ceiling = (1.0 + lex_weight) / (RRF_K + 1)  # best possible: rank 1 in both lists
        order = sorted(fused, key=lambda h: fused[h], reverse=True)
        if lex_weight > 1.0 and self._clear_lexical_outlier(lex_ranked):
            pinned = lex_ranked[0][0]
            order = [pinned] + [h for h in order if h != pinned]
            fused[pinned] = max(fused[pinned], max(fused.values()))  # keep scores monotonic with order
        order = order[:top_k]
        return [self._hit(h, fused[h] / ceiling, vec_score.get(h), lex_score.get(h)) for h in order]

    @staticmethod
    def _clear_lexical_outlier(lex_ranked: List[Tuple[str, float]]) -> bool:
        if not lex_ranked:
            return False
        if len(lex_ranked) == 1:
            return True
        return lex_ranked[0][1] >= EXACT_MATCH_GAP * lex_ranked[1][1]

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        sources = [
            r[0] for r in self.conn.execute("SELECT DISTINCT source FROM chunks").fetchall()
        ]
        return {"chunks": total, "sources": sources, "lexical_backend": self.lexical_backend}

    def close(self) -> None:
        self.conn.close()
