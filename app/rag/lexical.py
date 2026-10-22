"""
Lexical (BM25) support for hybrid retrieval.

Dense vectors are good at paraphrase and weak on exact identifiers: a query for
`compute_prorated_refund` should find the function literally called that, not a
vaguely "refund-like" paragraph. Every chunk is therefore also indexed in SQLite
FTS5 (BM25), and the two rankings are fused (see store.py).

Code needs a little help over a stock text tokenizer, so identifiers are expanded:

    get_user_by_id   ->  indexed as the whole token AND  get user by id AND  getuserbyid
    getUserById      ->  indexed as the whole token AND  get user by id AND  getuserbyid

The joined lowercase form (`getuserbyid`) is a canonical key, so a query written
in camelCase finds a snake_case definition and vice versa.

The FTS table uses `tokenchars '_'` so snake_case stays one token, plus the
porter stemmer so "handled"/"handles" match in prose. Queries are expanded the
same way and every term is double-quoted before reaching FTS5, so user text can
never inject FTS query syntax.
"""
from __future__ import annotations

import re
from typing import List

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

STOPWORDS = frozenset(
    "a an and are as at be by can do does for from how i if in into is it its of on or "
    "so than that the their then there these this to was what when where which who why "
    "will with would you your".split()
)


def split_identifier(token: str) -> List[str]:
    """`getUserById` / `get_user_by_id` -> ['get', 'user', 'by', 'id']."""
    parts: List[str] = []
    for chunk in token.split("_"):
        if chunk:
            parts.extend(p.lower() for p in _CAMEL_RE.split(chunk) if p)
    return parts


def expand_terms(text: str) -> str:
    """Text to index: the original plus the sub-words of every compound identifier."""
    extra: List[str] = []
    for token in _WORD_RE.findall(text):
        parts = split_identifier(token)
        if len(parts) > 1:
            extra.extend(parts)
            extra.append("".join(parts))
    return text if not extra else f"{text}\n{' '.join(extra)}"


def has_identifier(query: str) -> bool:
    """True if the query contains a compound identifier (snake_case or camelCase)."""
    return any(len(split_identifier(tok)) > 1 for tok in _WORD_RE.findall(query))


def build_match_query(query: str, max_terms: int = 24) -> str:
    """FTS5 MATCH expression (OR of quoted terms), or '' when nothing is searchable."""
    terms: List[str] = []
    seen = set()

    def add(term: str) -> None:
        term = term.lower()
        if len(term) > 1 and term not in seen:
            seen.add(term)
            terms.append(term)

    for token in re.findall(r"[A-Za-z0-9_]+", query):
        parts = split_identifier(token)
        compound = len(parts) > 1
        if not compound and token.lower() in STOPWORDS:
            continue
        add(token)
        if compound:
            add("".join(parts))  # canonical joined key
            for part in parts:
                if part not in STOPWORDS:
                    add(part)
        if len(terms) >= max_terms:
            break
    return " OR ".join(f'"{t}"' for t in terms[:max_terms])
