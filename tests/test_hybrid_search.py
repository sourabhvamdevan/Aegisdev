"""Hybrid retrieval: BM25 (FTS5) + vector fused with Reciprocal Rank Fusion."""
import random

import pytest

from app.rag import RagPipeline, lexical
from app.rag.store import RRF_K, rrf_fuse

WORDS = ["compute", "prorated", "refund", "billing", "cycle", "invoice", "plan", "price", "days", "unused"]


@pytest.fixture()
def crowded():
    """40 look-alike distractors (same vocabulary, different identifiers) + one exact target."""
    rng = random.Random(1)
    p = RagPipeline()
    for i in range(40):
        a, b, c = rng.sample(WORDS, 3)
        p.ingest_text(
            f"mod_{i}.py",
            f'def {b}_{a}_{c}_{i}(x):\n    """{a} {b} {c} {rng.choice(WORDS)} helper"""\n    return x\n',
        )
    p.ingest_text("target.py", "def compute_prorated_refund(price, used, cycle):\n    return price * (cycle - used) / cycle\n")
    return p


def test_fts5_available_and_reported():
    p = RagPipeline()
    assert p.stats()["lexical_backend"] == "fts5-bm25"
    assert p.stats()["retrieval_mode"] == "hybrid"


def test_exact_identifier_wins_in_hybrid_where_vector_misses(crowded):
    for q in ("compute_prorated_refund", "where is compute_prorated_refund defined"):
        assert crowded.retrieve(q, top_k=1, mode="hybrid").citations[0].source == "target.py"
        # documents WHY hybrid exists: dense-only is fooled by the shared vocabulary
        assert crowded.retrieve(q, top_k=1, mode="vector").citations[0].source != "target.py"


def test_camelcase_query_finds_snake_case_definition(crowded):
    # canonical joined key: computeProratedRefund == compute_prorated_refund
    assert crowded.retrieve("computeProratedRefund", top_k=1, mode="hybrid").citations[0].source == "target.py"
    assert crowded.retrieve("computeProratedRefund", top_k=1, mode="vector").citations[0].source != "target.py"


def test_hybrid_still_handles_paraphrase_queries():
    p = RagPipeline()
    p.ingest_text("net.md", "The sandbox disables network access by default.")
    p.ingest_text("other.md", "Citations include the filename and a locator.")
    assert p.retrieve("how is network handled?", top_k=1).citations[0].source == "net.md"


def test_hit_exposes_component_scores(crowded):
    hit = crowded.retrieve("compute_prorated_refund", top_k=1, mode="hybrid").raw_hits[0]
    assert hit.vector_score is not None and hit.lexical_score is not None
    assert 0.0 < hit.score <= 1.0


def test_modes_and_validation():
    p = RagPipeline()
    p.ingest_text("a.md", "alpha beta gamma")
    assert p.retrieve("alpha", mode="vector").mode == "vector"
    assert p.retrieve("alpha", mode="lexical").citations[0].source == "a.md"
    with pytest.raises(ValueError):
        RagPipeline(retrieval_mode="bogus")


def test_query_without_lexical_terms_falls_back_to_vector():
    p = RagPipeline()
    p.ingest_text("a.md", "alpha beta gamma")
    # only stopwords/punctuation -> no lexical query; must still return vector results
    assert p.retrieve("the of and ???", top_k=1).citations


def test_fts_rows_follow_delete_and_reindex():
    p = RagPipeline()
    p.ingest_text("a.md", "zebrafish quantum entanglement")
    assert p.retrieve("zebrafish", mode="lexical").citations[0].source == "a.md"
    p.ingest_text("a.md", "completely different words here")  # incremental reindex
    # old terms are gone from the lexical index after reindex -> falls back to vector, never the stale row
    assert p.store._lexical_ranked("zebrafish", 5) == []
    n_fts = p.store.conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0]
    n_chunks = p.store.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert n_fts == n_chunks
    p.delete("a.md")
    assert p.store.conn.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == 0


def test_query_cannot_inject_fts_syntax():
    p = RagPipeline()
    p.ingest_text("a.md", "alpha beta")
    for evil in ['" OR 1=1 --', "alpha NEAR(", "*", 'alpha" AND "', "chunks_fts:alpha"]:
        p.retrieve(evil, top_k=2)  # must not raise


def test_legacy_database_is_backfilled(tmp_path):
    import sqlite3

    from app.rag.store import VectorStore

    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE chunks (hash TEXT PRIMARY KEY, source TEXT NOT NULL, locator TEXT NOT NULL,"
                 " kind TEXT NOT NULL, text TEXT NOT NULL, vector TEXT NOT NULL)")
    conn.execute("INSERT INTO chunks VALUES ('h1','old.md','L1','text','legacy_token_here','[0.1]')")
    conn.commit()
    conn.close()
    store = VectorStore(db)
    assert store._lexical_ranked("legacy_token_here", 5)


def test_rrf_math_and_weighting():
    fused = rrf_fuse([["a", "b"], ["b", "a"]])
    assert fused["a"] == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 2))
    weighted = rrf_fuse([["a"], ["b"]], weights=[1.0, 2.0])
    assert weighted["b"] > weighted["a"]


def test_identifier_helpers():
    assert lexical.split_identifier("getUserById") == ["get", "user", "by", "id"]
    assert lexical.has_identifier("where is get_user") and not lexical.has_identifier("how does it work")
    q = lexical.build_match_query("computeProratedRefund")
    assert '"computeproratedrefund"' in q and '"prorated"' in q


def test_no_exact_pin_without_a_clear_outlier():
    # identifier-shaped query, but many chunks match equally -> ordering stays RRF-based, no crash
    p = RagPipeline()
    for i in range(5):
        p.ingest_text(f"f{i}.py", f"def get_user_{i}():\n    return {i}\n")
    r = p.retrieve("get_user", top_k=3)
    assert len(r.citations) == 3
