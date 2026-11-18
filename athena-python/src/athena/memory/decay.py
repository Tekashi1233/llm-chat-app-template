"""Temporal decay for lessons — 30-day half-life exponential decay.

RECONSTRUCTED, not copied: MemOS's ``src/memory/temporal_decay.py``
is missing from the MemOS copy (only its test survives in
``tests/test_temporal_decay.py``). The formula below is the one that
test pins down, and it matches ``OPENCLAW_COMPARISON.md`` §9:

    lambda_decay = ln(2) / half_life_days
    score *= exp(-lambda_decay * age_days)

with ``half_life_days = 30``. The Friday README claims a 45-day
half-life for its Layer 4 — that contradicts MemOS's own comparison
doc and its test (30d -> 0.5, 60d -> 0.25), so 30 days is used here.

Rules (Friday README §"Temporal Memory Decay", MemOS comparison §9):
* Explicitly stated facts (``stated=True``) are IMMUNE to decay.
* Lessons below the visibility threshold become INVISIBLE in
  retrieval but are NOT deleted.
* Decay is computed at read time from ``updated_at`` (last use /
  reinforcement); the stored confidence is never rewritten by decay.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid a runtime import cycle; duck-typed in practice
    from .lessons import Lesson, LessonStore

#: Half-life in days (MemOS test_temporal_decay + OPENCLAW_COMPARISON §9).
HALF_LIFE_DAYS = 30.0

#: lambda = ln(2) / half-life.
LAMBDA_DECAY = math.log(2) / HALF_LIFE_DAYS

#: Below this effective confidence a lesson is invisible, not deleted.
MIN_VISIBLE_CONFIDENCE = 0.10


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp, accepting a trailing ``Z``."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def age_days(timestamp_iso: str, now: datetime | None = None) -> float:
    """Age in days (fractional) between ``timestamp_iso`` and ``now``."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    delta = (now - parse_iso(timestamp_iso)).total_seconds() / 86400.0
    return max(0.0, delta)


def decay_multiplier(age_days_value: float, half_life_days: float = HALF_LIFE_DAYS) -> float:
    """Exponential decay multiplier: 1.0 at age 0, 0.5 at one half-life."""
    if age_days_value <= 0:
        return 1.0
    return math.exp(-math.log(2) * age_days_value / half_life_days)


# MemOS test name alias.
calculate_temporal_decay_multiplier = decay_multiplier


def effective_confidence(
    lesson: "Lesson",
    now: datetime | None = None,
    half_life_days: float = HALF_LIFE_DAYS,
) -> float:
    """Confidence after decay. Stated (explicit) lessons are immune."""
    if lesson.stated:
        return float(lesson.confidence)
    age = age_days(lesson.updated_at, now)
    return float(lesson.confidence) * decay_multiplier(age, half_life_days)


def is_visible(
    lesson: "Lesson",
    now: datetime | None = None,
    threshold: float = MIN_VISIBLE_CONFIDENCE,
    half_life_days: float = HALF_LIFE_DAYS,
) -> bool:
    """True when the lesson's effective confidence is at/above threshold."""
    return effective_confidence(lesson, now, half_life_days) >= threshold


def visible_lessons(
    store: "LessonStore",
    now: datetime | None = None,
    threshold: float = MIN_VISIBLE_CONFIDENCE,
    half_life_days: float = HALF_LIFE_DAYS,
    limit: int = 100,
) -> list[tuple["Lesson", float]]:
    """(lesson, effective_confidence) pairs still visible, best first.

    Decayed-below-threshold lessons are filtered out here — they stay
    in the database and can come back if reinforced (``reinforce()``
    refreshes ``updated_at`` and raises confidence).
    """
    now = now or datetime.now(timezone.utc)
    visible: list[tuple["Lesson", float]] = []
    for lesson in store.list_lessons(limit=limit):
        eff = effective_confidence(lesson, now, half_life_days)
        if eff >= threshold:
            visible.append((lesson, eff))
    visible.sort(key=lambda pair: pair[1], reverse=True)
    return visible
