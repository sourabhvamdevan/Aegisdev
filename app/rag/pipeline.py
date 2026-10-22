"""
RAG pipeline: the orchestration layer the MCP tools call into.

Ties together parsing -> chunking -> local embedding -> hybrid (vector + BM25) store, plus
retrieval with citations and prompt-injection-aware context assembly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from . import injection
from .chunker import chunk_segments
from .embeddings import Embedder
from .parsers import SUPPORTED_EXTENSIONS, parse_file
from .store import Hit, VectorStore


@dataclass
class Citation:
    source: str
    locator: str
    score: float


@dataclass
class RetrievalResult:
    query: str
    context: str                       # injection-fenced context for the model
    citations: List[Citation]
    flagged_chunks: int = 0
    raw_hits: List[Hit] = field(default_factory=list)
    mode: str = "hybrid"               # retrieval mode actually used


class RagPipeline:
    def __init__(
        self,
        db_path: str = ":memory:",
        embedder: Optional[Embedder] = None,
        *,
        retrieval_mode: str = "hybrid",
    ):
        if retrieval_mode not in ("hybrid", "vector", "lexical"):
            raise ValueError(f"unknown retrieval_mode: {retrieval_mode!r}")
        self.store = VectorStore(db_path)
        self.embedder = embedder or Embedder()
        self.retrieval_mode = retrieval_mode

    @property
    def backend(self) -> str:
        return self.embedder.backend

    # -- ingestion ----------------------------------------------------------
    def ingest_file(self, path: Path, *, reindex: bool = True) -> dict:
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"unsupported file type: {path.suffix!r}")
        segments = parse_file(path)
        chunks = chunk_segments(segments)
        if not chunks:
            return {"source": path.name, "added": 0, "duplicates": 0, "chunks": 0}
        vectors = self.embedder.embed([c.text for c in chunks])
        if reindex:
            result = self.store.reindex_source(path.name, chunks, vectors)
        else:
            result = self.store.add(chunks, vectors)
        result["source"] = path.name
        result["chunks"] = len(chunks)
        result["embedding_backend"] = self.embedder.backend
        return result

    def ingest_text(self, name: str, content: str) -> dict:
        from .parsers import Segment

        seg = [Segment(text=content, source=name, locator="L1", kind="text")]
        chunks = chunk_segments(seg)
        vectors = self.embedder.embed([c.text for c in chunks])
        result = self.store.reindex_source(name, chunks, vectors)
        result["source"] = name
        result["chunks"] = len(chunks)
        return result

    def delete(self, source: str) -> int:
        return self.store.delete_source(source)

    def stats(self) -> dict:
        s = self.store.stats()
        s["embedding_backend"] = self.embedder.backend
        s["retrieval_mode"] = self.retrieval_mode
        return s

    # -- retrieval ----------------------------------------------------------
    def retrieve(self, query: str, top_k: int = 5, *, mode: Optional[str] = None) -> RetrievalResult:
        mode = mode or self.retrieval_mode
        qvec = self.embedder.embed_one(query)
        hits = self.store.search(qvec, top_k=top_k, query_text=query, mode=mode)

        safe_texts: List[str] = []
        citations: List[Citation] = []
        flagged = 0
        for h in hits:
            sanitized = injection.neutralize(h.text)
            if sanitized.flagged:
                flagged += 1
            labelled = f"[{h.source} :: {h.locator}]\n{sanitized.text}"
            safe_texts.append(labelled)
            citations.append(Citation(source=h.source, locator=h.locator, score=round(h.score, 4)))

        context = injection.fence(safe_texts) if safe_texts else ""
        return RetrievalResult(
            query=query,
            context=context,
            citations=citations,
            flagged_chunks=flagged,
            raw_hits=hits,
            mode=mode,
        )
