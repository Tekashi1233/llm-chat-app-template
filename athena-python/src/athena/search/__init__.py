"""ATHENA Python v1 — hybrid search over lessons.

FTS5 BM25 + hashed-vector cosine + MMR diversity ranking (adapted
from MemOS ``src/search/hybrid_search.py`` and ``src/search/mmr.py``).
No embedding model, no sqlite-vec, no Redis: vectors are SHA-256
hashed bag-of-words vectors (numpy only).
"""

from __future__ import annotations

from athena.search.fts_search import bm25_rank_to_score, build_fts_query, keyword_scores
from athena.search.hybrid_search import (
    DEFAULT_MIN_SCORE,
    DEFAULT_TEXT_WEIGHT,
    DEFAULT_VECTOR_WEIGHT,
    VECTOR_DIMS,
    SearchResult,
    cosine_similarity,
    hash_vector,
    jaccard_similarity,
    mmr_rerank,
    search,
    tokenize,
)

__all__ = [
    "DEFAULT_MIN_SCORE",
    "DEFAULT_TEXT_WEIGHT",
    "DEFAULT_VECTOR_WEIGHT",
    "VECTOR_DIMS",
    "SearchResult",
    "bm25_rank_to_score",
    "build_fts_query",
    "cosine_similarity",
    "hash_vector",
    "jaccard_similarity",
    "keyword_scores",
    "mmr_rerank",
    "search",
    "tokenize",
]
