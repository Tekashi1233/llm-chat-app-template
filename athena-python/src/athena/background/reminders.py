"""ATHENA Python v1 — calendar reminders (background wiring).

Thin wiring over :class:`athena.memory.calendar.CalendarStore`:
which events need a reminder, which alerts are due right now, and
marking a reminder sent. Pure functions over an injected store —
no threads, no I/O of their own.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

__all__ = ["check_reminders", "due_alerts", "mark_reminder_sent"]

if TYPE_CHECKING:
    from athena.memory.calendar import CalendarFact, CalendarStore


def check_reminders(
    store: "CalendarStore", now: datetime | None = None
) -> list["CalendarFact"]:
    """Events starting in 24–48h with no reminder sent yet."""
    return store.events_needing_reminder(now=now)


def due_alerts(
    store: "CalendarStore", now: datetime | None = None
) -> dict[str, list["CalendarFact"]]:
    """Alert windows right now: ``now`` / ``15min`` / ``1h``."""
    return store.alerts(now=now)


def mark_reminder_sent(
    store: "CalendarStore", event_id: str, now: datetime | None = None
) -> bool:
    """Record that a reminder was sent for ``event_id``."""
    return store.mark_reminder_sent(event_id, now=now)
