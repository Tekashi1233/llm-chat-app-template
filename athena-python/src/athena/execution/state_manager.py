"""ATHENA Python v1 — execution state manager.

Tracks running executions (plan name -> status) so the UI can show
what the engine is doing. Small and in-memory only: state is
forgotten when the process ends.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

__all__ = ["ExecutionState", "ExecutionStateManager"]


@dataclass
class ExecutionState:
    """One tracked execution."""

    execution_id: str
    plan_name: str
    status: str = "running"  # running | done | failed | stopped
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


class ExecutionStateManager:
    """Thread-safe registry of execution states."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._executions: dict[str, ExecutionState] = {}

    def start(self, execution_id: str, plan_name: str) -> ExecutionState:
        """Record a new running execution (overwrites a stale id)."""
        state = ExecutionState(execution_id=execution_id, plan_name=plan_name)
        with self._lock:
            self._executions[execution_id] = state
        return state

    def finish(self, execution_id: str, status: str) -> ExecutionState | None:
        """Mark an execution finished. Returns None for unknown ids."""
        with self._lock:
            state = self._executions.get(execution_id)
            if state is None:
                return None
            state.status = status
            state.finished_at = time.time()
            return state

    def get(self, execution_id: str) -> ExecutionState | None:
        """State for one execution, or None."""
        with self._lock:
            return self._executions.get(execution_id)

    def running(self) -> list[ExecutionState]:
        """All executions still in flight."""
        with self._lock:
            return [s for s in self._executions.values() if s.status == "running"]

    def all(self) -> list[ExecutionState]:
        """Every tracked execution, oldest first."""
        with self._lock:
            return sorted(self._executions.values(), key=lambda s: s.started_at)
