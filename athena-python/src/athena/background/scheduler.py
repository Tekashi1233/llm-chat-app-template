"""ATHENA Python v1 — background interval scheduler.

Stdlib ``threading`` only — no APScheduler, no paid dependency.
Jobs run on a daemon thread at a fixed interval; a job that raises
is logged, never kills the scheduler. ``tick_once()`` runs every
due job immediately (the deterministic path tests use).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

__all__ = ["IntervalScheduler", "ScheduledJob"]

logger = logging.getLogger(__name__)


@dataclass
class ScheduledJob:
    """One registered interval job."""

    name: str
    interval_seconds: float
    fn: Callable[[], None]
    last_run_at: float | None = None
    run_count: int = 0
    last_error: str | None = None

    def due(self, now: float) -> bool:
        """True when the job has never run or its interval elapsed."""
        return self.last_run_at is None or (now - self.last_run_at) >= self.interval_seconds


class IntervalScheduler:
    """Tiny interval scheduler on a daemon thread.

    Args:
        tick_seconds: how often the loop checks for due jobs.
    """

    def __init__(self, tick_seconds: float = 1.0) -> None:
        self._tick = max(0.05, float(tick_seconds))
        self._jobs: dict[str, ScheduledJob] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def schedule(self, name: str, interval_seconds: float, fn: Callable[[], None]) -> ScheduledJob:
        """Register (or replace) a job. Returns the job."""
        job = ScheduledJob(name=name, interval_seconds=float(interval_seconds), fn=fn)
        with self._lock:
            self._jobs[name] = job
        return job

    def unschedule(self, name: str) -> bool:
        """Remove a job. Returns False when it was not registered."""
        with self._lock:
            return self._jobs.pop(name, None) is not None

    def jobs(self) -> list[ScheduledJob]:
        """Registered jobs, in registration order."""
        with self._lock:
            return list(self._jobs.values())

    def tick_once(self, now: float | None = None) -> list[str]:
        """Run every due job once, now. Returns the names that ran."""
        now = time.time() if now is None else now
        ran: list[str] = []
        with self._lock:
            due = [job for job in self._jobs.values() if job.due(now)]
        for job in due:
            job.last_run_at = now
            job.run_count += 1
            try:
                job.fn()
            except Exception as exc:  # noqa: BLE001 — one bad job never kills the rest
                job.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("[Scheduler] job %r failed: %s", job.name, job.last_error)
            ran.append(job.name)
        return ran

    def start(self) -> None:
        """Start the background loop (no-op when already running)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="athena-scheduler", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background loop."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        """True while the background loop is alive."""
        return self._thread is not None and self._thread.is_alive()

    def _loop(self) -> None:
        while not self._stop.wait(self._tick):
            self.tick_once()
