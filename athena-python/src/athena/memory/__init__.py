"""ATHENA Python v1 — local LESSONS store.

BOUNDARY (do not widen): the Cloudflare Worker + D1 is ATHENA's memory
of record (conversations, facts, profile). This package is NOT a second
memory system. It is a LOCAL SQLite store for LESSONS ONLY — things
ATHENA learned about its own behaviour from failures and corrections —
plus short-term notes awaiting consolidation, and calendar facts ATHENA
already knows (used only for local conflict detection / reminders).

Nothing here is synced to D1, and nothing here replaces D1 memory.
Adapted from MemOS (see each module's header for its source file);
MemOS modules that were missing from the copy
(``src/memory/promotion.py``, ``temporal_decay.py``, ``dreaming.py``,
``facts.py``, ``reflection.py``) were reconstructed from MemOS's own
tests, ``OPENCLAW_COMPARISON.md`` and ``src/config/settings.py`` —
the formulas are documented per-module where they were rebuilt
rather than copied. MemOS has no voice; nothing voice-related was
copied from it.
"""

from __future__ import annotations

from .calendar import CalendarFact, CalendarStore
from .compaction import CompactionResult, compact
from .decay import (
    HALF_LIFE_DAYS,
    MIN_VISIBLE_CONFIDENCE,
    decay_multiplier,
    effective_confidence,
    is_visible,
    visible_lessons,
)
from .dreaming import CircuitBreaker, Dreamer
from .lessons import DEFAULT_DB_PATH, Lesson, LessonStore, ShortNote
from .promotion import (
    PROMOTE_THRESHOLD,
    evaluate_promotion_candidate,
    promote_notes,
)
from .repair import extract_identifiers, repair_tool_use_result_pairing

__all__ = [
    "DEFAULT_DB_PATH",
    "HALF_LIFE_DAYS",
    "MIN_VISIBLE_CONFIDENCE",
    "PROMOTE_THRESHOLD",
    "CalendarFact",
    "CalendarStore",
    "CircuitBreaker",
    "CompactionResult",
    "Dreamer",
    "Lesson",
    "LessonStore",
    "ShortNote",
    "compact",
    "decay_multiplier",
    "effective_confidence",
    "evaluate_promotion_candidate",
    "extract_identifiers",
    "is_visible",
    "promote_notes",
    "repair_tool_use_result_pairing",
    "visible_lessons",
]
