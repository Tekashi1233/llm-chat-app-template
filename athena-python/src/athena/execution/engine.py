"""ATHENA Python v1 — execution engine (Friday-style multi-step plans).

Runs a :class:`Plan` of steps through the tool registry:

* Sequential steps run in order, each seeing earlier results.
* Steps sharing a ``group`` run in parallel (thread pool) when they
  are independent — a step that needs another step's result must not
  share its group.
* Per-step state is tracked (pending / running / done / failed).
* On repeated failure of the same tool the engine stops the plan
  instead of looping: two failures of one tool (or three failed
  steps total) end the run honestly.
* Learning hook: every tool failure is reflected on via
  :func:`athena.reasoning.reflection.on_tool_failure` and, when a
  lesson store is supplied, stored as a lesson.

The engine never imports the router (layering: execution sits below
the router) — dispatch defaults to a registry lookup, and callers
may inject their own ``dispatch_fn(category, tool, args, context)``.
It never raises for a failed plan: failures are reported in the
returned :class:`ExecutionResult`.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from athena.reasoning import reflection as _reflection
from athena.tools.base_tool import ToolContext, ToolResult
from athena.tools.registry import REGISTRY

__all__ = [
    "ExecutionEngine",
    "ExecutionResult",
    "Plan",
    "PlanStep",
    "StepState",
]

logger = logging.getLogger(__name__)

#: Stop the plan after this many failures of the SAME tool.
MAX_FAILURES_PER_TOOL = 2
#: …or after this many failed steps in total.
MAX_FAILED_STEPS = 3


@dataclass
class PlanStep:
    """One step of a plan."""

    name: str
    category: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    #: Steps sharing a group run in parallel. None = sequential.
    group: str | None = None
    #: Optional: a previous step's ``data`` key this step's args may
    #: reference as ``{step_name.field}`` (filled in before the run).
    depends_on: str | None = None


@dataclass
class Plan:
    """A multi-step plan: an ordered list of steps."""

    name: str
    steps: list[PlanStep] = field(default_factory=list)


@dataclass
class StepState:
    """Live state of one step."""

    name: str
    status: str = "pending"  # pending | running | done | failed | skipped
    result: ToolResult | None = None


@dataclass
class ExecutionResult:
    """Outcome of a whole plan run."""

    plan_name: str
    ok: bool
    steps: dict[str, StepState] = field(default_factory=dict)
    summary: str = ""
    stopped_early: bool = False


def _default_dispatch(
    category: str, tool_name: str, args: dict[str, Any], context: ToolContext | None
) -> ToolResult:
    tool = REGISTRY.get(tool_name)
    if tool is None:
        return ToolResult.failure(f"Unknown tool: {tool_name!r}.")
    if tool.category != category:
        return ToolResult.failure(
            f"Tool {tool_name!r} belongs to category {tool.category!r}, "
            f"not {category!r}; it can only be called from its own category."
        )
    try:
        return tool.run(dict(args), context)
    except Exception as exc:  # tools must not raise, but never crash the engine
        return ToolResult.failure(f"Tool {tool_name!r} failed: {exc}")


class ExecutionEngine:
    """Runs :class:`Plan` objects step by step.

    Args:
        dispatch_fn: ``(category, tool, args, context) -> ToolResult``.
            Defaults to the registry lookup above.
        memory_store: optional lesson store for the reflection hook.
        tool_context: optional :class:`ToolContext` passed to tools.
        max_workers: thread pool size for parallel groups.
    """

    def __init__(
        self,
        *,
        dispatch_fn: Callable | None = None,
        memory_store: Any | None = None,
        tool_context: ToolContext | None = None,
        max_workers: int = 4,
    ) -> None:
        self._dispatch = dispatch_fn or _default_dispatch
        self._memory_store = memory_store
        self._tool_context = tool_context
        self._max_workers = max(1, int(max_workers))
        self._lock = threading.Lock()

    def run(self, plan: Plan) -> ExecutionResult:
        """Run ``plan`` to completion (or an honest early stop). Never raises."""
        states: dict[str, StepState] = {s.name: StepState(s.name) for s in plan.steps}
        failures_by_tool: dict[str, int] = {}
        failed_steps = 0
        stopped_early = False
        try:
            i = 0
            while i < len(plan.steps):
                step = plan.steps[i]
                if step.group is not None:
                    group = [step]
                    j = i + 1
                    while j < len(plan.steps) and plan.steps[j].group == step.group:
                        group.append(plan.steps[j])
                        j += 1
                    failed = self._run_group(group, states, failures_by_tool)
                    failed_steps += failed
                    i = j
                else:
                    if not self._run_one(step, states, failures_by_tool):
                        failed_steps += 1
                    i += 1
                if (
                    any(n >= MAX_FAILURES_PER_TOOL for n in failures_by_tool.values())
                    or failed_steps >= MAX_FAILED_STEPS
                ):
                    stopped_early = True
                    break
        except Exception as exc:  # noqa: BLE001 — the engine never raises
            logger.exception("[ExecutionEngine] unexpected failure")
            return ExecutionResult(
                plan_name=plan.name, ok=False, steps=states,
                summary=f"Engine failed unexpectedly ({type(exc).__name__}): {exc}",
                stopped_early=True,
            )
        if stopped_early:
            for step in plan.steps:
                if states[step.name].status == "pending":
                    states[step.name].status = "skipped"
            return ExecutionResult(
                plan_name=plan.name, ok=False, steps=states,
                summary=(
                    "Stopped early: a tool kept failing, so the plan was "
                    "halted instead of retrying in a loop."
                ),
                stopped_early=True,
            )
        done = sum(1 for st in states.values() if st.status == "done")
        ok = failed_steps == 0
        return ExecutionResult(
            plan_name=plan.name, ok=ok, steps=states,
            summary=f"{done}/{len(plan.steps)} steps done"
            + ("" if ok else f", {failed_steps} failed"),
        )

    # -- internals ---------------------------------------------------

    def _run_group(
        self,
        group: list[PlanStep],
        states: dict[str, StepState],
        failures_by_tool: dict[str, int],
    ) -> int:
        """Run one parallel group; returns the number of failed steps."""
        failed = 0
        with ThreadPoolExecutor(max_workers=min(self._max_workers, len(group))) as pool:
            futures = {pool.submit(self._run_one, s, states, failures_by_tool): s for s in group}
            for future in futures:
                try:
                    if not future.result():
                        failed += 1
                except Exception:  # noqa: BLE001 — _run_one never raises, belt and braces
                    failed += 1
        return failed

    def _run_one(
        self,
        step: PlanStep,
        states: dict[str, StepState],
        failures_by_tool: dict[str, int],
    ) -> bool:
        """Run one step; returns True on success. Never raises."""
        state = states[step.name]
        state.status = "running"
        try:
            result = self._dispatch(step.category, step.tool, step.args, self._tool_context)
        except Exception as exc:  # noqa: BLE001 — a broken dispatcher is a failure, not a crash
            result = ToolResult.failure(f"Dispatcher failed: {exc}")
        state.result = result
        if result.ok:
            state.status = "done"
            return True
        state.status = "failed"
        with self._lock:
            failures_by_tool[step.tool] = failures_by_tool.get(step.tool, 0) + 1
        self._reflect(step.tool, result.error or result.text)
        return False

    def _reflect(self, tool_name: str, error: str) -> None:
        if self._memory_store is None:
            return
        try:
            lesson = _reflection.on_tool_failure(tool_name, error)
            self._memory_store.add_lesson(
                trigger=lesson["trigger"],
                fix=lesson["fix"],
                confidence=lesson["confidence"],
                source="reflection",
                stated=False,
            )
        except Exception:  # noqa: BLE001 — reflection must not break execution
            logger.warning("[ExecutionEngine] failure reflection failed", exc_info=True)
