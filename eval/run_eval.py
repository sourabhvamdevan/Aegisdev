"""
Reproducible retrieval evaluation.

Ingests the fixture corpus in eval/corpus/, runs each eval question, and reports
recall@k: whether the expected source appears in the top-k citations. Run with:

    python -m eval.run_eval                 # hybrid (BM25 + vector), the default
    python -m eval.run_eval --mode vector   # dense only
    python -m eval.run_eval --compare       # side-by-side vector / lexical / hybrid

Deterministic (offline hashing embedder by default) so results are reproducible
in CI without any model download or network.
"""
from __future__ import annotations

import json
from pathlib import Path

from app.rag import RagPipeline

EVAL_DIR = Path(__file__).resolve().parent
CORPUS_DIR = EVAL_DIR / "corpus"
EVAL_FILE = EVAL_DIR / "retrieval_eval.jsonl"


def load_eval():
    with EVAL_FILE.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def build_pipeline() -> RagPipeline:
    pipe = RagPipeline()
    for path in sorted(CORPUS_DIR.glob("*")):
        if path.is_file():
            pipe.ingest_file(path)
    return pipe


def recall(pipe: RagPipeline, cases, mode: str, top_k: int):
    """Returns (hits, per-case results) for one retrieval mode."""
    results = []
    for case in cases:
        sources = [c.source for c in pipe.retrieve(case["question"], top_k=top_k, mode=mode).citations]
        results.append((case, sources, case["expected_source"] in sources))
    return sum(ok for _, _, ok in results), results


def main(top_k: int = 3, mode: str = "hybrid", compare: bool = False) -> int:
    pipe = build_pipeline()
    cases = load_eval()
    print(f"Backend: {pipe.backend}  |  lexical: {pipe.store.lexical_backend}  |  corpus files: {len(list(CORPUS_DIR.glob('*')))}\n")

    if compare:
        print(f"{'mode':10} recall@{top_k}")
        for m in ("vector", "lexical", "hybrid"):
            hits, _ = recall(pipe, cases, m, top_k)
            print(f"{m:10} {hits}/{len(cases)} = {hits / len(cases):.2f}")
        return 0

    hits, results = recall(pipe, cases, mode, top_k)
    for case, sources, ok in results:
        mark = "PASS" if ok else "MISS"
        print(f"[{mark}] {case['question'][:60]:60}  expected={case['expected_source']:22} got={sources}")
    rate = hits / len(cases) if cases else 0.0
    print(f"\nrecall@{top_k} ({mode}): {hits}/{len(cases)} = {rate:.2f}")
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["hybrid", "vector", "lexical"], default="hybrid")
    ap.add_argument("--compare", action="store_true")
    ap.add_argument("--top-k", type=int, default=3)
    args = ap.parse_args()
    raise SystemExit(main(args.top_k, args.mode, args.compare))
