"""ATHENA Python v1 — periodic decay sweep over lessons.

Lessons are never deleted by decay — they just fade below the
visibility threshold (see :mod:`athena.memory.decay`). This watcher
runs the sweep on an interval (wired into the background scheduler)
and reports how many lessons are visible vs faded, so the app can
show honest memory health without a second memory system.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from athena.memory.decay import MIN_VISIBLE_CONFIDENCE, visible_lessons

__all__ = ["DecayWatcher", "sweep"]

if TYPE_CHECKING:
    from athena.memory.lessons import LessonStore


def sweep(store: "LessonStore", now: datetime | None = None) -> dict[str, int]:
    """One sweep: count visible vs faded lessons. Pure read, no writes."""
    now = now or datetime.now(timezone.utc)
    visible = visible_lessons(store, now=now)
    total = store.count_lessons()
    return {
        "total": total,
        "visible": len(visible),
        "faded": max(0, total - len(visible)),
        "min_visible_confidence": MIN_VISIBLE_CONFIDENCE,
    }


class DecayWatcher:
    """Sweep runner with a last-report memory (for scheduler jobs)."""

    def __init__(self, store: "LessonStore") -> None:
        self.store = store
        self.last_report: dict[str, int] | None = None
        self.run_count: int = 0

    def run(self, now: datetime | None = None) -> dict[str, int]:
        """Run one sweep and remember the report."""
        self.last_report = sweep(self.store, now=now)
        self.run_count += 1
        return self.last_report
