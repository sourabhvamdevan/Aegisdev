"""
Document parsers.

Each parser turns a source file into a list of `Segment`s. A segment carries
the text plus enough location metadata to build a citation later
(filename + page / section / line-range), which the assessment requires.

Supported: PDF, Markdown, text, JSON, Python, JavaScript, TypeScript.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass
class Segment:
    text: str
    source: str          # filename
    locator: str         # e.g. "p.3", "L10-40", "section: Setup", "$.items[0]"
    kind: str            # "pdf" | "markdown" | "text" | "json" | "code"


def _by_lines(text: str, source: str, kind: str, window: int = 40) -> List[Segment]:
    """Split code/text into line-windowed segments with line-range locators."""
    lines = text.splitlines()
    segments: List[Segment] = []
    for start in range(0, max(len(lines), 1), window):
        chunk = "\n".join(lines[start : start + window])
        if chunk.strip():
            end = min(start + window, len(lines))
            segments.append(
                Segment(text=chunk, source=source, locator=f"L{start + 1}-{end}", kind=kind)
            )
    return segments


def parse_pdf(path: Path) -> List[Segment]:
    from pypdf import PdfReader  # imported lazily so non-PDF runs need no dep

    reader = PdfReader(str(path))
    segments: List[Segment] = []
    for i, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            segments.append(Segment(text=text, source=path.name, locator=f"p.{i}", kind="pdf"))
    return segments


def parse_markdown(path: Path) -> List[Segment]:
    text = path.read_text(encoding="utf-8", errors="replace")
    segments: List[Segment] = []
    current_section = "(intro)"
    buffer: List[str] = []

    def flush():
        joined = "\n".join(buffer).strip()
        if joined:
            segments.append(
                Segment(text=joined, source=path.name, locator=f"section: {current_section}", kind="markdown")
            )

    for line in text.splitlines():
        if line.lstrip().startswith("#"):
            flush()
            buffer = []
            current_section = line.lstrip("#").strip() or current_section
        else:
            buffer.append(line)
    flush()
    return segments or _by_lines(text, path.name, "markdown")


def parse_json(path: Path) -> List[Segment]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _by_lines(raw, path.name, "text")

    segments: List[Segment] = []

    def walk(node, jsonpath: str):
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{jsonpath}.{k}")
        elif isinstance(node, list):
            for idx, v in enumerate(node):
                walk(v, f"{jsonpath}[{idx}]")
        else:
            segments.append(
                Segment(text=f"{jsonpath} = {node}", source=path.name, locator=jsonpath, kind="json")
            )

    walk(data, "$")
    return segments


def parse_text_or_code(path: Path) -> List[Segment]:
    text = path.read_text(encoding="utf-8", errors="replace")
    kind = "code" if path.suffix in {".py", ".js", ".ts"} else "text"
    return _by_lines(text, path.name, kind)


_DISPATCH = {
    ".pdf": parse_pdf,
    ".md": parse_markdown,
    ".markdown": parse_markdown,
    ".json": parse_json,
    ".txt": parse_text_or_code,
    ".py": parse_text_or_code,
    ".js": parse_text_or_code,
    ".ts": parse_text_or_code,
}

SUPPORTED_EXTENSIONS = frozenset(_DISPATCH.keys())


def parse_file(path: Path) -> List[Segment]:
    """Parse any supported file into segments. Raises on unsupported types."""
    parser = _DISPATCH.get(path.suffix.lower())
    if parser is None:
        raise ValueError(f"unsupported file type: {path.suffix!r}")
    return parser(path)
