"""
Bootstrap sample data.

Ingests the eval corpus into a local index so you can immediately try the
console without uploading anything. Safe, synthetic content only.

    python -m scripts.bootstrap_sample_data
"""
from __future__ import annotations

from pathlib import Path

from app.services import services

CORPUS = Path(__file__).resolve().parent.parent / "eval" / "corpus"


def main() -> None:
    count = 0
    for path in sorted(CORPUS.glob("*")):
        if path.is_file():
            result = services.ingest_path(path)
            print(f"ingested {result['source']}: {result.get('added', 0)} chunks")
            count += 1
    print(f"\nDone. {count} files indexed. Backend: {services.rag.backend}")


if __name__ == "__main__":
    main()
