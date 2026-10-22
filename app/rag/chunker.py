"""
Chunking.

Segments from the parsers can be large (a whole PDF page, a markdown section).
We split them into overlapping character windows so retrieval is granular while
keeping each chunk's citation locator intact.

Character-based windows are used deliberately for zero-dependency determinism;
swapping in a token-aware splitter is a documented future improvement.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List

from .parsers import Segment


@dataclass
class Chunk:
    text: str
    source: str
    locator: str
    kind: str


def chunk_segments(
    segments: List[Segment],
    *,
    max_chars: int = 900,
    overlap: int = 150,
) -> List[Chunk]:
    chunks: List[Chunk] = []
    for seg in segments:
        text = seg.text
        if len(text) <= max_chars:
            chunks.append(Chunk(text, seg.source, seg.locator, seg.kind))
            continue
        step = max(max_chars - overlap, 1)
        for i, start in enumerate(range(0, len(text), step)):
            piece = text[start : start + max_chars]
            if piece.strip():
                locator = f"{seg.locator}#chunk{i}"
                chunks.append(Chunk(piece, seg.source, locator, seg.kind))
    return chunks
