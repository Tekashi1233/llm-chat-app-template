"""Hybrid search over lessons — FTS5 BM25 + hashed-vector cosine + MMR.

Adapted from MemOS ``src/search/hybrid_search.py`` (FTS5 BM25 ranking
with ``bm25_rank_to_score``, OR-joined quoted tokens, LIKE fallback
when FTS5 returns nothing, weighted merge of vector + text scores)
and ``src/search/mmr.py`` (tokenize incl. CJK, Jaccard similarity,
MMR rerank with min-max normalised relevance).

Differences from MemOS, all deliberate:
* No embedding model, no sqlite-vec, no Redis cache. Vectors are
  HASHED vectors: tokens are hashed (SHA-256) into a fixed 128-dim
  space and cosine similarity is computed with numpy only. That is
  what "numpy only, no heavy embedding model" means here.
* Synchronous: there is no network call anywhere in this path.
* Vector/text default weights stay 0.7 / 0.3, min_score 0.1, as in
  MemOS ``HybridSearcher.search``.
* Decayed-below-threshold lessons are invisible (see decay.py) —
  they are filtered before ranking, never deleted.
* Search hits are recorded (``access_count`` + ``updated_at``) so
  promotion scoring can see that a lesson was actually recalled,
  mirroring MemOS ``tools.py`` recall tracking.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import numpy as np

from athena.memory.decay import MIN_VISIBLE_CONFIDENCE, effective_confidence
from athena.search.fts_search import keyword_scores

if TYPE_CHECKING:
    from athena.memory.lessons import Lesson, LessonStore

#: Hashed-vector dimensionality.
VECTOR_DIMS = 128

#: Default merge weights (MemOS HybridSearcher.search defaults).
DEFAULT_VECTOR_WEIGHT = 0.7
DEFAULT_TEXT_WEIGHT = 0.3
DEFAULT_MIN_SCORE = 0.1

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\u1100-\u11ff]")


@dataclass
class SearchResult:
    lesson_id: int
    trigger: str
    fix: str
    score: float
    vector_score: float = 0.0
    text_score: float = 0.0
    confidence: float = 0.0
    effective_confidence: float = 0.0


# ---------------------------------------------------------------------------
# Hashed vectors (numpy only — no embedding model)
# ---------------------------------------------------------------------------

def hash_vector(text: str, dims: int = VECTOR_DIMS) -> np.ndarray:
    """L2-normalised hashed bag-of-words vector for ``text``.

    Each token increments the bucket ``sha256(token) % dims``; the
    vector is then L2-normalised so cosine similarity is a dot product.
    Empty text yields the zero vector.
    """
    vec = np.zeros(dims, dtype=np.float64)
    for token in _TOKEN_RE.findall(text.lower()):
        bucket = int(hashlib.sha256(token.encode("utf-8")).hexdigest(), 16) % dims
        vec[bucket] += 1.0
    norm = float(np.linalg.norm(vec))
    if norm == 0.0:
        return vec
    return vec / norm


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two (already normalised) vectors."""
    if a.shape != b.shape or a.size == 0:
        return 0.0
    return float(np.dot(a, b))


# ---------------------------------------------------------------------------
# Keyword search (FTS5 BM25, copied shapes from MemOS hybrid_search)
# ---------------------------------------------------------------------------


def _vector_scores(
    lessons: list["Lesson"], query_vec: np.ndarray
) -> dict[int, float]:
    results: dict[int, float] = {}
    for lesson in lessons:
        vec = hash_vector(f"{lesson.trigger} {lesson.fix}")
        sim = cosine_similarity(query_vec, vec)
        if sim > 0.0:
            results[lesson.id] = max(0.0, sim)
    return results


# ---------------------------------------------------------------------------
# MMR diversity ranking (from MemOS src/search/mmr.py)
# ---------------------------------------------------------------------------

def tokenize(text: str) -> set[str]:
    """ASCII tokens plus CJK unigrams/bigrams (MemOS mmr.tokenize)."""
    lower = text.lower()
    ascii_tokens = _TOKEN_RE.findall(lower)
    chars = list(lower)
    cjk = [c for c in chars if _CJK_RE.match(c)]
    bigrams = [cjk[i] + cjk[i + 1] for i in range(len(cjk) - 1)]
    return set(ascii_tokens + bigrams + cjk)


def jaccard_similarity(set_a: set[str], set_b: set[str]) -> float:
    if not set_a and not set_b:
        return 1.0
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a) + len(set_b) - inter
    return inter / union if union > 0 else 0.0


def mmr_rerank(results: list[SearchResult], lambda_param: float = 0.5) -> list[SearchResult]:
    """Maximal Marginal Relevance rerank (MemOS mmr.mmr_rerank).

    ``lambda_param`` 1.0 == pure relevance order; 0.5 (default) balances
    relevance against diversity vs already-selected results.
    """
    if len(results) <= 1:
        return list(results)
    lam = max(0.0, min(1.0, lambda_param))
    if lam == 1.0:
        return sorted(results, key=lambda r: r.score, reverse=True)
    token_cache = {r.lesson_id: tokenize(f"{r.trigger} {r.fix}") for r in results}
    scores = [r.score for r in results]
    lo, hi = min(scores), max(scores)
    span = hi - lo

    def normalised(score: float) -> float:
        return 1.0 if span == 0 else (score - lo) / span

    selected: list[SearchResult] = []
    remaining = list(results)
    while remaining:
        best, best_mmr = None, float("-inf")
        for cand in remaining:
            max_sim = 0.0
            for sel in selected:
                sim = jaccard_similarity(token_cache[cand.lesson_id], token_cache[sel.lesson_id])
                max_sim = max(max_sim, sim)
            mmr = lam * normalised(cand.score) - (1.0 - lam) * max_sim
            if mmr > best_mmr or (mmr == best_mmr and best is not None and cand.score > best.score):
                best, best_mmr = cand, mmr
        selected.append(best)  # type: ignore[arg-type]
        remaining.remove(best)  # type: ignore[arg-type]
    return selected


# ---------------------------------------------------------------------------
# Combined hybrid search
# ---------------------------------------------------------------------------

def search(
    store: "LessonStore",
    query: str,
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    text_weight: float = DEFAULT_TEXT_WEIGHT,
    min_score: float = DEFAULT_MIN_SCORE,
    max_results: int = 10,
    mmr_lambda: float = 0.5,
    apply_mmr: bool = True,
    now: datetime | None = None,
    record_access: bool = True,
) -> list[SearchResult]:
    """Hybrid search over visible lessons.

    Merges FTS5 BM25 keyword scores and hashed-vector cosine scores
    (``score = vector*vector_weight + text*text_weight``), drops
    below-``min_score`` and decay-invisible lessons, then MMR-reranks
    for diversity. Returns at most ``max_results`` results.
    """
    now = now or datetime.now(timezone.utc)
    try:
        vector_weight = float(vector_weight)
        text_weight = float(text_weight)
        min_score = float(min_score)
        max_results = int(max_results)
    except (TypeError, ValueError):
        pass
    if not query or not query.strip() or max_results <= 0:
        return []

    visible = {lesson.id: (lesson, eff) for lesson, eff in _visible_map(store, now)}
    if not visible:
        return []

    query_vec = hash_vector(query)
    vector_scores = _vector_scores([lesson for lesson, _ in visible.values()], query_vec)
    text_scores = keyword_scores(store, query, max_results)

    merged: dict[int, SearchResult] = {}
    for lesson_id in set(vector_scores) | set(text_scores):
        if lesson_id not in visible:
            continue
        lesson, eff = visible[lesson_id]
        v_score = vector_scores.get(lesson_id, 0.0)
        t_score = text_scores.get(lesson_id, 0.0)
        combined = v_score * vector_weight + t_score * text_weight
        if combined < min_score:
            continue
        merged[lesson_id] = SearchResult(
            lesson_id=lesson_id,
            trigger=lesson.trigger,
            fix=lesson.fix,
            score=combined,
            vector_score=v_score,
            text_score=t_score,
            confidence=lesson.confidence,
            effective_confidence=eff,
        )

    results = sorted(merged.values(), key=lambda r: r.score, reverse=True)
    if apply_mmr:
        results = mmr_rerank(results, lambda_param=mmr_lambda)
    results = results[:max_results]
    if record_access:
        for result in results:
            store.record_access(result.lesson_id)
    return results


def _visible_map(store: "LessonStore", now: datetime) -> list[tuple["Lesson", float]]:
    """(lesson, effective_confidence) for lessons still visible."""
    out = []
    for lesson in store.list_lessons(limit=1000):
        eff = effective_confidence(lesson, now)
        if eff >= MIN_VISIBLE_CONFIDENCE:
            out.append((lesson, eff))
    return out
