"""6D promotion scoring — short-term notes -> durable lessons.

RECONSTRUCTED, not copied: MemOS's ``src/memory/promotion.py`` is
missing from the MemOS copy. The six dimensions, weights and the
0.5 threshold below are taken from ``OPENCLAW_COMPARISON.md`` §3 and
``src/config/settings.py`` (``promotion_min_score = 0.5``), and the
component shapes are pinned by ``tests/test_promotion.py``:

1. Frequency:    log1p(recalls) / log1p(10)
2. Relevance:    total_score / signal_count   (signal_count =
                 recall_count + daily_count — the MemOS test's
                 6.5 / 7.0 with recallCount 5 + dailyCount 2)
3. Diversity:    unique_queries / 5
4. Recency:      exp(-lambda * age_days), 30-day half-life
5. Consolidation: spacing + span over the distinct recall days
                 ([] -> 0.0, one day -> 0.2, spread days -> > 0.2)
6. Conceptual:   concept_tags / 6

Weights: [0.24, 0.30, 0.15, 0.15, 0.10, 0.06] (sum 1.0).
A candidate promotes when its weighted score >= 0.5.

``evaluate_promotion_candidate()`` keeps the MemOS test's entry shape
(camelCase keys) so the math is directly checkable.
``promote_notes()`` applies the same scoring to groups of similar
short-term notes in the local store: repeated observations of the
same thing, spread over several days, promote to a lesson.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .decay import HALF_LIFE_DAYS, LAMBDA_DECAY, age_days, parse_iso

if TYPE_CHECKING:
    from .lessons import Lesson, LessonStore

#: Weighted-score threshold for promotion (MemOS settings.promotion_min_score).
PROMOTE_THRESHOLD = 0.5

#: Dimension weights in order: frequency, relevance, diversity,
#: recency, consolidation, conceptual (OPENCLAW_COMPARISON.md §3).
WEIGHTS = (0.24, 0.30, 0.15, 0.15, 0.10, 0.06)

#: Stopwords ignored when deriving concept tags from note text.
_STOPWORDS = frozenset(
    "a an the and or but of to in on for with is are was were be been it its this that "
    "i you he she we they my your his her our their me him us them do does did not no yes "
    "so as at by from up out about into over after before when where what which who how why "
    "can could should would will just very really also then than too".split()
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def clamp_score(value: float) -> float:
    """Clamp a score into [0, 1] (MemOS promotion.clamp_score)."""
    return max(0.0, min(1.0, float(value)))


def frequency_component(recall_count: int) -> float:
    return clamp_score(math.log1p(max(0, recall_count)) / math.log1p(10))


def relevance_component(total_score: float, signal_count: int) -> float:
    if signal_count <= 0:
        return 0.0
    return clamp_score(total_score / signal_count)


def diversity_component(unique_queries: int) -> float:
    return clamp_score(unique_queries / 5.0)


def recency_component(age_days_value: float, half_life_days: float = HALF_LIFE_DAYS) -> float:
    if age_days_value <= 0:
        return 1.0
    return math.exp(-math.log(2) * age_days_value / half_life_days)


def consolidation_component(recall_days: list[str]) -> float:
    """Spacing + span over distinct recall days (MemOS test shape).

    No days -> 0.0; a single day -> 0.2; several days spread out score
    higher: 0.2 + 0.8 * (0.55 * spacing + 0.45 * span), where spacing
    grows with the number of distinct days (capped at 5) and span with
    the day range (capped at 30 days).
    """
    days = sorted({d[:10] for d in recall_days if d})
    if not days:
        return 0.0
    if len(days) == 1:
        return 0.2
    first = datetime.fromisoformat(days[0]).date()
    last = datetime.fromisoformat(days[-1]).date()
    span_days = (last - first).days
    spacing = min(1.0, (len(days) - 1) / 4.0)
    span = min(1.0, span_days / 30.0)
    return clamp_score(0.2 + 0.8 * (0.55 * spacing + 0.45 * span))


# MemOS test name alias.
calculate_consolidation_component = consolidation_component


def conceptual_component(concept_tags: list[str]) -> float:
    return clamp_score(len(set(concept_tags)) / 6.0)


def _get(entry: dict, *names: str, default=None):
    for name in names:
        if name in entry:
            return entry[name]
    return default


def _parse_now(now) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    # epoch milliseconds (MemOS test passes now_ms)
    return datetime.fromtimestamp(float(now) / 1000.0, tz=timezone.utc)


def evaluate_promotion_candidate(entry: dict, now=None) -> dict:
    """Score one promotion candidate (MemOS entry shape).

    ``entry`` keys (camelCase as in MemOS's test, snake_case accepted):
    recallCount, dailyCount, groundedCount, totalScore, queryHashes,
    recallDays, lastRecalledAt, conceptTags. Returns ``{"valid",
    "score", "components": {...}, "uniqueQueries": int, ...}``.
    ``valid`` means the weighted score reaches PROMOTE_THRESHOLD.
    """
    now_dt = _parse_now(now)
    recall_count = int(_get(entry, "recallCount", "recall_count", default=0) or 0)
    daily_count = int(_get(entry, "dailyCount", "daily_count", default=0) or 0)
    total_score = float(_get(entry, "totalScore", "total_score", default=0.0) or 0.0)
    query_hashes = list(_get(entry, "queryHashes", "query_hashes", default=[]) or [])
    recall_days = list(_get(entry, "recallDays", "recall_days", default=[]) or [])
    concept_tags = list(_get(entry, "conceptTags", "concept_tags", default=[]) or [])
    last_recalled = _get(entry, "lastRecalledAt", "last_recalled_at", default=None)

    signal_count = recall_count + daily_count
    if last_recalled:
        try:
            age = age_days(str(last_recalled), now_dt)
        except ValueError:
            age = 0.0
    else:
        age = 0.0

    components = {
        "frequency": frequency_component(recall_count),
        "relevance": relevance_component(total_score, signal_count),
        "diversity": diversity_component(len(set(query_hashes))),
        "recency": recency_component(age),
        "consolidation": consolidation_component(recall_days),
        "conceptual": conceptual_component(concept_tags),
    }
    ordered = (
        components["frequency"],
        components["relevance"],
        components["diversity"],
        components["recency"],
        components["consolidation"],
        components["conceptual"],
    )
    score = clamp_score(sum(w * c for w, c in zip(WEIGHTS, ordered)))
    return {
        "valid": score >= PROMOTE_THRESHOLD,
        "score": score,
        "components": components,
        "uniqueQueries": len(set(query_hashes)),
        "recallDays": len(set(recall_days)),
        "signalCount": signal_count,
    }


# ---------------------------------------------------------------------------
# Promoting short-term notes
# ---------------------------------------------------------------------------

def _note_key(text: str) -> str:
    """Normalised grouping key: first 8 content words, lowercased."""
    words = [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS]
    return " ".join(words[:8])


def _concept_tags(text: str) -> list[str]:
    words = [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS]
    seen: list[str] = []
    for word in words:
        if word not in seen:
            seen.append(word)
    return seen[:12]


def promote_notes(
    store: "LessonStore",
    now: datetime | None = None,
    threshold: float = PROMOTE_THRESHOLD,
) -> dict:
    """Promote repeated short-term notes to durable lessons.

    Notes are grouped by normalised key (same observation, possibly
    reworded). Each group is scored with the 6D formula; groups at or
    above ``threshold`` become a lesson (trigger = the observation,
    fix = the same observation as a standing instruction — a bare note
    carries no failure/fix pair, so the lesson is the observation
    itself) and their notes are consumed (deleted). Groups below the
    threshold stay as notes for a later dreaming pass.

    Returns ``{"promoted": [Lesson, ...], "kept": int, "consumed": int}``.
    """
    now_dt = now or datetime.now(timezone.utc)
    notes = store.list_notes(limit=1000)
    groups: dict[str, list] = {}
    for note in notes:
        groups.setdefault(_note_key(note.text), []).append(note)

    promoted: list["Lesson"] = []
    consumed = 0
    for key, group in groups.items():
        if not key:
            continue
        days = sorted({n.created_at[:10] for n in group})
        latest = max(n.created_at for n in group)
        entry = {
            "recallCount": len(group),
            "dailyCount": len(days),
            "totalScore": float(len(group)),  # each occurrence scores 1.0
            "queryHashes": days,  # distinct days stand in for distinct queries
            "recallDays": days,
            "lastRecalledAt": latest,
            "conceptTags": _concept_tags(group[0].text),
        }
        result = evaluate_promotion_candidate(entry, now_dt)
        if not result["valid"] or result["score"] < threshold:
            continue
        text = group[0].text
        lesson = store.add_lesson(
            trigger=text,
            fix=text,
            confidence=round(result["score"], 3),
            source="promotion",
            stated=False,
        )
        promoted.append(lesson)
        consumed += store.delete_notes([n.id for n in group])

    return {"promoted": promoted, "kept": len(notes) - consumed, "consumed": consumed}
