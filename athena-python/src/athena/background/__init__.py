"""ATHENA Python v1 — background package.

Stdlib-threading interval scheduler (no APScheduler, no paid deps),
calendar reminder wiring, and the periodic decay sweep. Jobs are
registered by the app; nothing here starts a thread on import.
"""

from __future__ import annotations

from athena.background.decay_watcher import DecayWatcher, sweep
from athena.background.reminders import check_reminders, due_alerts, mark_reminder_sent
from athena.background.scheduler import IntervalScheduler, ScheduledJob

__all__ = [
    "DecayWatcher",
    "IntervalScheduler",
    "ScheduledJob",
    "check_reminders",
    "due_alerts",
    "mark_reminder_sent",
    "sweep",
]
