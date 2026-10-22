"""Ties the retrieval evaluation into the automated suite with a recall floor."""
from pathlib import Path

from app.rag import RagPipeline
from eval.run_eval import load_eval

CORPUS = Path(__file__).resolve().parent.parent / "eval" / "corpus"


def test_retrieval_recall_meets_threshold():
    pipe = RagPipeline()
    for path in sorted(CORPUS.glob("*")):
        if path.is_file():
            pipe.ingest_file(path)
    cases = load_eval()
    assert len(cases) >= 8  # requirement: at least 8 questions
    hits = 0
    for case in cases:
        r = pipe.retrieve(case["question"], top_k=3)
        if case["expected_source"] in [c.source for c in r.citations]:
            hits += 1
    recall = hits / len(cases)
    assert recall >= 0.7, f"recall@3 too low: {recall:.2f}"
