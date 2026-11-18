"""Background consolidation ("dreaming") — notes -> lessons.

Design only from Friday's README (its memory code was never in that
repo): a ``ProjectDreamer`` running every 30 minutes, a
``ChronicleCircuitBreaker`` that trips after 5 errors, and dreaming
triggered every 5 writes (MemOS ``src/agent/tools.py`` auto-dream).
The consolidation step itself is MemOS promotion (see promotion.py):
repeated short-term notes become durable lessons.

This Dreamer does NOT write dream-narrative diary entries (MemOS's
``test_dreaming.py`` covers a ``MemoryDreamer`` diary that is part of
a different, narrative feature) and does NOT touch D1 — it only
consolidates the LOCAL lessons store.

Circuit breaker: after ``max_failures`` consecutive failed runs the
breaker opens and ``run()`` becomes a no-op returning ``None`` until
``reset()`` is called. A successful run clears the failure count.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from .promotion import promote_notes

if TYPE_CHECKING:
    from .lessons import LessonStore

#: Default dreaming interval (Friday README: ProjectDreamer, 30 min).
DEFAULT_INTERVAL_SECONDS = 1800.0

#: Consecutive failures before the breaker trips (Friday README:
#: ChronicleCircuitBreaker trips after 5 errors).
DEFAULT_MAX_FAILURES = 5


class CircuitBreaker:
    """Stops repeated consolidation attempts after repeated errors."""

    def __init__(self, max_failures: int = DEFAULT_MAX_FAILURES) -> None:
        self.max_failures = max_failures
        self.failures = 0
        self.tripped = False

    def record_success(self) -> None:
        self.failures = 0
        self.tripped = False

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.max_failures:
            self.tripped = True

    def reset(self) -> None:
        self.failures = 0
        self.tripped = False

    @property
    def is_open(self) -> bool:
        return self.tripped


class Dreamer:
    """Consolidate short-term notes into durable lessons.

    Runs on demand (``run()`` / ``run_on_demand()``) and on an
    interval in a background thread (``start()`` / ``stop()``).
    """

    def __init__(
        self,
        store: "LessonStore",
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        max_failures: int = DEFAULT_MAX_FAILURES,
    ) -> None:
        self.store = store
        self.interval_seconds = interval_seconds
        self.breaker = CircuitBreaker(max_failures=max_failures)
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.last_run_at: datetime | None = None
        self.last_report: dict | None = None

    # -- core ------------------------------------------------------------
    def consolidate(self, now: datetime | None = None) -> dict:
        """One consolidation pass. Raises on failure (caller records it)."""
        with self._lock:
            result = promote_notes(self.store, now=now)
            return {
                "promoted": len(result["promoted"]),
                "promoted_ids": [lesson.id for lesson in result["promoted"]],
                "notes_consumed": result["consumed"],
                "notes_remaining": result["kept"],
                "errors": [],
                "tripped": False,
            }

    def run(self, now: datetime | None = None) -> dict | None:
        """Run one pass; ``None`` when the circuit breaker is open."""
        if self.breaker.is_open:
            return None
        try:
            report = self.consolidate(now=now)
        except Exception as exc:  # noqa: BLE001 — breaker must see the failure
            self.breaker.record_failure()
            try:
                remaining = self.store.count_notes()
            except Exception:
                remaining = None  # store itself is unreadable; report anyway
            report = {
                "promoted": 0,
                "promoted_ids": [],
                "notes_consumed": 0,
                "notes_remaining": remaining,
                "errors": [f"{type(exc).__name__}: {exc}"],
                "tripped": self.breaker.is_open,
            }
            self.last_report = report
            return report
        self.breaker.record_success()
        self.last_run_at = datetime.now(timezone.utc)
        self.last_report = report
        return report

    def run_on_demand(self, now: datetime | None = None) -> dict | None:
        """Manual trigger (MemOS ``dream_now`` tool)."""
        return self.run(now=now)

    def due(self, now: datetime | None = None) -> bool:
        """True when the interval has elapsed since the last run."""
        if self.last_run_at is None:
            return True
        now = now or datetime.now(timezone.utc)
        return (now - self.last_run_at).total_seconds() >= self.interval_seconds

    def reset_breaker(self) -> None:
        self.breaker.reset()

    # -- background thread ------------------------------------------------
    def start(self) -> None:
        """Start the interval loop in a daemon thread (no-op if running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="athena-dreamer")
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop_event.wait(self.interval_seconds):
            if self.due():
                self.run()
