"""ATHENA Python v1 — FTS5 keyword search helper.

The small SQLite FTS5 half of hybrid search (adapted from MemOS
``src/search/hybrid_search.py``): OR-joined quoted tokens so
partial matches work, BM25 ranking mapped to [0, 1], and a LIKE
fallback when FTS5 returns nothing. Used by
:mod:`athena.search.hybrid_search`; nothing else needs to import it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

__all__ = ["bm25_rank_to_score", "build_fts_query", "keyword_scores"]

if TYPE_CHECKING:
    from athena.memory.lessons import LessonStore


def build_fts_query(raw_query: str) -> str:
    """OR-joined quoted tokens so partial matches work."""
    tokens = re.findall(r"[\w]+", raw_query.lower())
    if not tokens:
        return ""
    return " OR ".join(f'"{t}"' for t in tokens)


def bm25_rank_to_score(rank: float) -> float:
    """Map an FTS5 bm25 rank (lower = better, usually negative) to [0, 1]."""
    if rank < 0:
        relevance = -rank
        return relevance / (1.0 + relevance)
    return 1.0 / (1.0 + rank)


def keyword_scores(store: "LessonStore", query: str, limit: int) -> dict[int, float]:
    """Lesson id -> BM25 text score; LIKE fallback when FTS finds nothing."""
    fts_query = build_fts_query(query)
    results: dict[int, float] = {}
    if fts_query:
        try:
            rows = store.conn.execute(
                """SELECT id, bm25(lessons_fts) AS rank FROM lessons_fts
                   WHERE lessons_fts MATCH ? ORDER BY rank ASC LIMIT ?""",
                (fts_query, limit * 4),
            ).fetchall()
            for row in rows:
                results[int(row["id"])] = bm25_rank_to_score(float(row["rank"]))
        except Exception:
            results = {}
    if not results:
        tokens = re.findall(r"[\w]+", query.lower())
        if not tokens:
            return {}
        conditions = " OR ".join(["LOWER(trigger) LIKE ? OR LOWER(fix) LIKE ?"] * len(tokens))
        params: list[str] = []
        for token in tokens:
            params.extend([f"%{token}%", f"%{token}%"])
        try:
            rows = store.conn.execute(
                f"SELECT id, trigger, fix FROM lessons WHERE ({conditions}) LIMIT ?",
                (*params, limit * 4),
            ).fetchall()
        except Exception:
            return {}
        for row in rows:
            haystack = f"{row['trigger']} {row['fix']}".lower()
            matches = sum(1 for t in tokens if t in haystack)
            results[int(row["id"])] = (matches / len(tokens)) * 0.5  # max 0.5, as in MemOS
    return results
