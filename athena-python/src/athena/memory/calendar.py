"""Calendar conflict detection + reminders over facts ATHENA knows.

NO new calendar integration and no Google/Outlook wiring: this works
only on ``calendar_facts`` rows already in the local lessons database
(the MemOS ``facts`` table shape from ``src/database/db_manager.py``,
including ``reminder_sent``). Facts arrive from elsewhere (e.g. the
Worker or a local extractor); this module only reads them.

Conflict detection is a SQL self-join (Friday README / MemOS
``FactStore.lint_memory_conflicts`` design): two active events overlap
when each starts before the other ends. The 3-window alerts
(1h / 15min / now) are disjoint bands so one event appears in exactly
one window; reminders cover events starting in the next 24–48h that
have not been reminded yet (MemOS ``loop.py`` day-before reminder).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .lessons import DEFAULT_DB_PATH, LessonStore


@dataclass
class CalendarFact:
    id: str
    content: str
    date_start: str
    date_end: str
    importance: float = 0.5
    confidence: float = 1.0
    status: str = "active"
    created_at: str = ""
    reminder_sent: Optional[str] = None


def _parse(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class CalendarStore:
    """Calendar facts + conflict detection on the local lessons DB."""

    def __init__(self, db_path: str | Path | None = None, store: LessonStore | None = None) -> None:
        # Reuse LessonStore's connection/schema so both share one DB file.
        self._store = store or LessonStore(db_path or DEFAULT_DB_PATH)
        self._owns_store = store is None
        self.conn = self._store.conn

    def close(self) -> None:
        if self._owns_store:
            self._store.close()

    # -- facts ------------------------------------------------------------
    def add_event(
        self,
        content: str,
        date_start: datetime,
        date_end: datetime | None = None,
        importance: float = 0.5,
        confidence: float = 1.0,
        event_id: str | None = None,
    ) -> CalendarFact:
        content = (content or "").strip()
        if not content:
            raise ValueError("event content must be non-empty")
        end = date_end or date_start
        if end < date_start:
            raise ValueError("date_end must not be before date_start")
        fact = CalendarFact(
            id=event_id or str(uuid.uuid4()),
            content=content,
            date_start=date_start.astimezone(timezone.utc).isoformat(),
            date_end=end.astimezone(timezone.utc).isoformat(),
            importance=importance,
            confidence=confidence,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.conn.execute(
            """INSERT INTO calendar_facts
               (id, content, date_start, date_end, importance, confidence, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'active', ?)""",
            (fact.id, fact.content, fact.date_start, fact.date_end,
             fact.importance, fact.confidence, fact.created_at),
        )
        self.conn.commit()
        return fact

    def get_event(self, event_id: str) -> Optional[CalendarFact]:
        row = self.conn.execute(
            "SELECT * FROM calendar_facts WHERE id = ?", (event_id,)
        ).fetchone()
        return _row_to_fact(row) if row else None

    def list_events(self, status: str = "active") -> list[CalendarFact]:
        rows = self.conn.execute(
            "SELECT * FROM calendar_facts WHERE status = ? ORDER BY date_start ASC",
            (status,),
        ).fetchall()
        return [_row_to_fact(r) for r in rows]

    def cancel_event(self, event_id: str) -> bool:
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE calendar_facts SET status = 'cancelled' WHERE id = ?", (event_id,)
        )
        self.conn.commit()
        return cur.rowcount > 0

    # -- conflicts: SQL self-join -----------------------------------------
    def detect_conflicts(self) -> list[tuple[CalendarFact, CalendarFact]]:
        """Overlapping active events, via a SQL self-join.

        Two events overlap iff a.start < b.end AND b.start < a.end.
        ``a.id < b.id`` keeps each pair once. Cancelled events are out.
        """
        rows = self.conn.execute(
            """SELECT a.*, b.id AS b_id, b.content AS b_content,
                      b.date_start AS b_start, b.date_end AS b_end,
                      b.importance AS b_importance, b.confidence AS b_confidence,
                      b.status AS b_status, b.created_at AS b_created,
                      b.reminder_sent AS b_reminder
               FROM calendar_facts a
               JOIN calendar_facts b
                 ON a.id < b.id
                AND a.status = 'active' AND b.status = 'active'
                AND a.date_start < b.date_end
                AND b.date_start < a.date_end
               ORDER BY a.date_start ASC"""
        ).fetchall()
        conflicts: list[tuple[CalendarFact, CalendarFact]] = []
        for row in rows:
            a = _row_to_fact(row)
            b = CalendarFact(
                id=row["b_id"], content=row["b_content"],
                date_start=row["b_start"], date_end=row["b_end"],
                importance=float(row["b_importance"]), confidence=float(row["b_confidence"]),
                status=row["b_status"], created_at=row["b_created"],
                reminder_sent=row["b_reminder"],
            )
            conflicts.append((a, b))
        return conflicts

    # -- 3-window alerts ---------------------------------------------------
    def alerts(self, now: datetime | None = None) -> dict[str, list[CalendarFact]]:
        """Disjoint windows: ``now`` (ongoing), ``15min``, ``1h``.

        * ``now``:   start <= now < end (happening right now)
        * ``15min``: starts within the next 15 minutes
        * ``1h``:    starts in (15, 60] minutes
        Past events that already ended appear in none.
        """
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        windows: dict[str, list[CalendarFact]] = {"now": [], "15min": [], "1h": []}
        for fact in self.list_events(status="active"):
            start, end = _parse(fact.date_start), _parse(fact.date_end)
            if start <= now < end:
                windows["now"].append(fact)
            elif timedelta(0) < (start - now) <= timedelta(minutes=15):
                windows["15min"].append(fact)
            elif timedelta(minutes=15) < (start - now) <= timedelta(hours=1):
                windows["1h"].append(fact)
        return windows

    # -- proactive reminders ----------------------------------------------
    def events_needing_reminder(self, now: datetime | None = None) -> list[CalendarFact]:
        """Active events starting in 24–48h with no reminder sent yet."""
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        due = []
        for fact in self.list_events(status="active"):
            if fact.reminder_sent is not None:
                continue
            start = _parse(fact.date_start)
            if timedelta(hours=24) <= (start - now) <= timedelta(hours=48):
                due.append(fact)
        return due

    def mark_reminder_sent(self, event_id: str, now: datetime | None = None) -> bool:
        stamp = (now or datetime.now(timezone.utc)).isoformat()
        cur = self.conn.cursor()
        cur.execute(
            "UPDATE calendar_facts SET reminder_sent = ? WHERE id = ?",
            (stamp, event_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def upcoming_itinerary(self, now: datetime | None = None) -> list[CalendarFact]:
        """All active future events, soonest first (for prompt context)."""
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return [f for f in self.list_events(status="active") if _parse(f.date_start) >= now]


def _row_to_fact(row) -> CalendarFact:
    return CalendarFact(
        id=row["id"],
        content=row["content"],
        date_start=row["date_start"],
        date_end=row["date_end"],
        importance=float(row["importance"]),
        confidence=float(row["confidence"]),
        status=row["status"],
        created_at=row["created_at"],
        reminder_sent=row["reminder_sent"],
    )
