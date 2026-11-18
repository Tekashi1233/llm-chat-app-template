"""Local lessons store — SQLite backend.

Adapted from MemOS ``src/database/db_manager.py`` (SQLite open with WAL
+ busy timeout, FTS5 table, ``facts`` table with ``reminder_sent``,
``short_term_recall`` table) — but scoped down to LESSONS ONLY:

* ``lessons`` — a behavioural lesson: what went wrong (``trigger``) and
  what to do instead (``fix``), with a confidence, a source and a
  ``stated`` flag (explicitly stated by the user == immune to decay,
  see :mod:`athena.memory.decay`).
* ``short_notes`` — short-term observations awaiting consolidation
  (promoted to lessons by :mod:`athena.memory.promotion`).
* ``calendar_facts`` — calendar facts ATHENA already knows, kept here
  only so :mod:`athena.memory.calendar` can detect conflicts locally.
  No calendar integration is built here; facts arrive from elsewhere.

NOT stored here: conversations, user facts, profile — those live in
the Worker + D1, the memory of record. This store never duplicates them.

The FTS5 table (``lessons_fts``) is maintained manually on every
insert/update/delete because it is a standalone FTS5 table, not an
external-content table.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ..config import APP_DATA_DIR

#: Default database file: %APPDATA%\\Athena\\lessons.db on Windows.
DEFAULT_DB_PATH = APP_DATA_DIR / "lessons.db"

#: Lessons whose effective (decayed) confidence falls below this are
#: invisible in retrieval but are NOT deleted (MemOS temporal-decay rule).
MIN_VISIBLE_CONFIDENCE = 0.10


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _clamp_confidence(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass
class Lesson:
    id: int
    trigger: str
    fix: str
    confidence: float
    source: str
    stated: bool
    created_at: str
    updated_at: str
    access_count: int


@dataclass
class ShortNote:
    id: int
    text: str
    created_at: str


class LessonStore:
    """SQLite store for lessons + short-term notes (+ calendar facts)."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        if self.db_path.parent != Path(""):
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        # WAL + busy timeout, as in MemOS db_manager._open_database.
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.ensure_schema()

    # -- schema ---------------------------------------------------------
    def ensure_schema(self) -> None:
        cur = self.conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS lessons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trigger TEXT NOT NULL,
                fix TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 0.5,
                source TEXT NOT NULL DEFAULT 'reflection',
                stated INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS short_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        # Calendar facts ATHENA already knows (MemOS facts table shape).
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS calendar_facts (
                id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                date_start TEXT NOT NULL,
                date_end TEXT NOT NULL,
                importance REAL DEFAULT 0.5,
                confidence REAL DEFAULT 1.0,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                reminder_sent TEXT DEFAULT NULL
            )
            """
        )
        cur.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS lessons_fts USING fts5(
                id UNINDEXED,
                trigger,
                fix,
                tokenize='porter'
            )
            """
        )
        self.conn.commit()

    # -- lessons: CRUD --------------------------------------------------
    def add_lesson(
        self,
        trigger: str,
        fix: str,
        confidence: float = 0.5,
        source: str = "reflection",
        stated: bool = False,
        created_at: str | None = None,
    ) -> Lesson:
        trigger = (trigger or "").strip()
        fix = (fix or "").strip()
        if not trigger or not fix:
            raise ValueError("lesson trigger and fix must both be non-empty")
        now = created_at or _utcnow_iso()
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO lessons
               (trigger, fix, confidence, source, stated, created_at, updated_at, access_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0)""",
            (trigger, fix, _clamp_confidence(confidence), source, 1 if stated else 0, now, now),
        )
        lesson_id = cur.lastrowid
        cur.execute(
            "INSERT INTO lessons_fts (id, trigger, fix) VALUES (?, ?, ?)",
            (lesson_id, trigger, fix),
        )
        self.conn.commit()
        lesson = self.get_lesson(lesson_id)
        assert lesson is not None
        return lesson

    def get_lesson(self, lesson_id: int) -> Optional[Lesson]:
        row = self.conn.execute(
            "SELECT * FROM lessons WHERE id = ?", (lesson_id,)
        ).fetchone()
        return _row_to_lesson(row) if row else None

    def list_lessons(self, limit: int = 100, offset: int = 0) -> list[Lesson]:
        rows = self.conn.execute(
            "SELECT * FROM lessons ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [_row_to_lesson(r) for r in rows]

    def count_lessons(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM lessons").fetchone()
        return int(row["n"])

    def update_lesson(
        self,
        lesson_id: int,
        trigger: str | None = None,
        fix: str | None = None,
        confidence: float | None = None,
        stated: bool | None = None,
        updated_at: str | None = None,
    ) -> Optional[Lesson]:
        lesson = self.get_lesson(lesson_id)
        if lesson is None:
            return None
        new_trigger = lesson.trigger if trigger is None else trigger.strip()
        new_fix = lesson.fix if fix is None else fix.strip()
        if not new_trigger or not new_fix:
            raise ValueError("lesson trigger and fix must both be non-empty")
        self.conn.execute(
            """UPDATE lessons SET trigger = ?, fix = ?, confidence = ?,
               stated = ?, updated_at = ? WHERE id = ?""",
            (
                new_trigger,
                new_fix,
                _clamp_confidence(confidence) if confidence is not None else lesson.confidence,
                (1 if stated else 0) if stated is not None else (1 if lesson.stated else 0),
                updated_at or _utcnow_iso(),
                lesson_id,
            ),
        )
        self.conn.execute("DELETE FROM lessons_fts WHERE id = ?", (lesson_id,))
        self.conn.execute(
            "INSERT INTO lessons_fts (id, trigger, fix) VALUES (?, ?, ?)",
            (lesson_id, new_trigger, new_fix),
        )
        self.conn.commit()
        return self.get_lesson(lesson_id)

    def delete_lesson(self, lesson_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM lessons WHERE id = ?", (lesson_id,))
        deleted = cur.rowcount > 0
        cur.execute("DELETE FROM lessons_fts WHERE id = ?", (lesson_id,))
        self.conn.commit()
        return deleted

    def record_access(self, lesson_id: int, now: str | None = None) -> None:
        """A recall: bump access_count and refresh updated_at (last use)."""
        self.conn.execute(
            "UPDATE lessons SET access_count = access_count + 1, updated_at = ? WHERE id = ?",
            (now or _utcnow_iso(), lesson_id),
        )
        self.conn.commit()

    def reinforce(
        self, lesson_id: int, confidence_delta: float = 0.05, now: str | None = None
    ) -> Optional[Lesson]:
        """A successful reuse: count it and nudge confidence up."""
        lesson = self.get_lesson(lesson_id)
        if lesson is None:
            return None
        self.conn.execute(
            """UPDATE lessons SET access_count = access_count + 1,
               confidence = ?, updated_at = ? WHERE id = ?""",
            (_clamp_confidence(lesson.confidence + confidence_delta), now or _utcnow_iso(), lesson_id),
        )
        self.conn.commit()
        return self.get_lesson(lesson_id)

    # -- short-term notes ----------------------------------------------
    def add_note(self, text: str, created_at: str | None = None) -> ShortNote:
        text = (text or "").strip()
        if not text:
            raise ValueError("note text must be non-empty")
        now = created_at or _utcnow_iso()
        cur = self.conn.cursor()
        cur.execute("INSERT INTO short_notes (text, created_at) VALUES (?, ?)", (text, now))
        note_id = cur.lastrowid
        self.conn.commit()
        return ShortNote(id=note_id, text=text, created_at=now)

    def list_notes(self, limit: int = 100) -> list[ShortNote]:
        rows = self.conn.execute(
            "SELECT * FROM short_notes ORDER BY created_at ASC LIMIT ?", (limit,)
        ).fetchall()
        return [ShortNote(id=r["id"], text=r["text"], created_at=r["created_at"]) for r in rows]

    def count_notes(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM short_notes").fetchone()
        return int(row["n"])

    def delete_note(self, note_id: int) -> bool:
        cur = self.conn.cursor()
        cur.execute("DELETE FROM short_notes WHERE id = ?", (note_id,))
        deleted = cur.rowcount > 0
        self.conn.commit()
        return deleted

    def delete_notes(self, note_ids: list[int]) -> int:
        if not note_ids:
            return 0
        placeholders = ",".join("?" for _ in note_ids)
        cur = self.conn.cursor()
        cur.execute(f"DELETE FROM short_notes WHERE id IN ({placeholders})", note_ids)
        deleted = cur.rowcount
        self.conn.commit()
        return deleted

    # -- lifecycle ------------------------------------------------------
    def close(self) -> None:
        try:
            self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.Error:
            pass
        self.conn.close()

    def __enter__(self) -> "LessonStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _row_to_lesson(row: sqlite3.Row) -> Lesson:
    return Lesson(
        id=int(row["id"]),
        trigger=row["trigger"],
        fix=row["fix"],
        confidence=float(row["confidence"]),
        source=row["source"],
        stated=bool(row["stated"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        access_count=int(row["access_count"]),
    )
