"""ATHENA Python v1 — the agent turn loop.

One turn, Friday-style:

1. Check the user text for a correction (zero-cost regex gate) and
   store the lesson when one is found.
2. Route the intent to a category. When a category matches, show
   the provider that category's tool schemas and let it pick a tool:
   the provider answers with ``{"tool": ..., "args": {...}}`` (JSON,
   optionally fenced) or ``FINAL: <answer>`` when no tool is needed.
3. Run the chosen tool through the category router, up to
   ``max_tool_calls`` calls per turn, then ask the provider for a
   final answer grounded in the tool results.
4. On a tool failure, reflect (``on_tool_failure`` lesson) and — on
   the second failure of the same tool — stop and say so honestly
   instead of retrying in a loop.

The loop never raises: any unexpected error becomes an honest
plain-language reply. It never imports the UI or voice — speaking
is the interface's job.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from athena.agents.base_agent import BaseAgent
from athena.reasoning import reflection as _reflection
from athena.router import category_router

__all__ = ["AgentLoop", "parse_tool_call"]

logger = logging.getLogger(__name__)

#: Reply when the provider cannot be reached at all.
_PROVIDER_DOWN = (
    "Sorry, I couldn't reach the brain right now — both the cloud "
    "service and the on-device model are unavailable. Try again in a moment."
)

_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)


def _balanced_object(text: str, start: int) -> str | None:
    """The balanced ``{...}`` object starting at ``start`` (or None).

    Brace-counts while respecting JSON strings and escapes, so nested
    ``args`` objects survive intact.
    """
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


def parse_tool_call(reply: str) -> dict[str, Any] | None:
    """Extract ``{"tool": name, "args": {...}}`` from a provider reply.

    Accepts a bare JSON object or a fenced `````json`` block. Returns
    None when the reply carries no tool call (it is a final answer).
    """
    if not reply:
        return None
    fenced = _FENCED_JSON_RE.search(reply)
    if fenced is not None:
        # Non-greedy fence capture stops at the first "}" — re-scan
        # the fenced body for the full balanced object instead.
        body = fenced.group(1)
        start = body.find("{")
        candidate = _balanced_object(body, start) if start != -1 else None
    else:
        start = reply.find('{"tool"')
        if start == -1:
            start = reply.find("{")
            if start == -1:
                return None
            candidate = _balanced_object(reply, start)
        else:
            candidate = _balanced_object(reply, start)
        if candidate is None:
            return None
    try:
        parsed = json.loads(candidate)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict) or not parsed.get("tool"):
        return None
    args = parsed.get("args") or {}
    if not isinstance(args, dict):
        return None
    return {"tool": str(parsed["tool"]), "args": args}


def _call_provider(provider: Any, messages: list[dict[str, str]]) -> str:
    """Call a provider and normalise its reply to plain text.

    :class:`athena.llm.slotted_provider.SlottedProvider` returns a
    ``(text, slot_used, fallback_note)`` tuple; plain providers
    return a string. Both are accepted; anything else becomes "".
    Never raises.
    """
    try:
        result = provider.generate(messages)
    except Exception as exc:  # noqa: BLE001 — the loop never raises
        logger.warning("[AgentLoop] provider failed: %s: %s", type(exc).__name__, exc)
        return ""
    if isinstance(result, tuple):
        result = result[0] if result else ""
    return result if isinstance(result, str) else ""


class AgentLoop(BaseAgent):
    """The turn loop.

    Args:
        provider: an :class:`athena.llm.base.LLMProvider` (or any
            object with ``generate(messages) -> str`` — a slotted
            provider's tuple reply is unpacked automatically).
        router: category router module (defaults to
            :mod:`athena.router.category_router`). Injected for tests.
        memory_store: optional :class:`athena.memory.lessons.LessonStore`
            for storing reflection lessons.
        live_context: optional callable returning a dict of live facts
            (date/time/status) merged into the turn's system prompt.
        max_tool_calls: cap on tool calls per turn (default 5).
        tool_context: optional :class:`athena.tools.base_tool.ToolContext`
            (e.g. the UI's on-screen Allow confirmer) passed to tools.
    """

    def __init__(
        self,
        provider: Any,
        *,
        router: Any = None,
        memory_store: Any | None = None,
        live_context: Any | None = None,
        max_tool_calls: int = 5,
        tool_context: Any | None = None,
    ) -> None:
        self._provider = provider
        self._router = router if router is not None else category_router
        self._memory_store = memory_store
        self._live_context = live_context
        self._max_tool_calls = max(0, int(max_tool_calls))
        self._tool_context = tool_context

    @property
    def name(self) -> str:
        return "ATHENA"

    # -- public ------------------------------------------------------

    def run_turn(self, user_text: str, *, context: dict[str, Any] | None = None) -> str:
        """Answer one user turn. Never raises."""
        try:
            return self._run_turn(user_text, context=context)
        except Exception as exc:  # noqa: BLE001 — never raise
            logger.exception("[AgentLoop] unexpected turn failure")
            return (
                "Sorry, something went wrong while answering that "
                f"({type(exc).__name__}) — please try again."
            )

    # -- internals ---------------------------------------------------

    def _run_turn(self, user_text: str, *, context: dict[str, Any] | None = None) -> str:
        text = (user_text or "").strip()
        if not text:
            return "Say something and I'll do my best to help."
        self._reflect_on_correction(text)
        category = self._router.route(text)
        messages = self._base_messages(text, context)
        if category == "general":
            reply = _call_provider(self._provider, messages)
            return reply.strip() or _PROVIDER_DOWN

        tool_results: list[str] = []
        failures: dict[str, int] = {}
        for _ in range(self._max_tool_calls):
            tool_messages = [*messages, self._tool_prompt(category, tool_results)]
            reply = _call_provider(self._provider, tool_messages)
            if not reply.strip():
                break
            call = parse_tool_call(reply)
            if call is None:
                return reply.strip()
            tool_name = call["tool"]
            result = self._router.dispatch(
                category, tool_name, call["args"], self._tool_context
            )
            if result.ok:
                tool_results.append(f"Tool {tool_name!r} returned: {result.text}")
            else:
                failures[tool_name] = failures.get(tool_name, 0) + 1
                self._reflect_on_tool_failure(tool_name, result.error or result.text)
                tool_results.append(
                    f"Tool {tool_name!r} failed: {result.error or result.text}"
                )
                if failures[tool_name] >= 2:
                    return (
                        f"I tried {tool_name!r} twice and it kept failing "
                        f"({result.error or result.text}), so I stopped "
                        "instead of retrying in a loop. "
                        + self._final_answer(messages, tool_results)
                    )
        return self._final_answer(messages, tool_results)

    def _final_answer(
        self, messages: list[dict[str, str]], tool_results: list[str]
    ) -> str:
        """Ask the provider to compose the final answer from tool results."""
        if not tool_results:
            reply = _call_provider(self._provider, messages)
            return reply.strip() or _PROVIDER_DOWN
        grounded = [
            *messages,
            {
                "role": "system",
                "content": (
                    "Answer the user's request using ONLY these tool "
                    "results. If a tool failed, say so honestly.\n"
                    + "\n".join(tool_results)
                ),
            },
        ]
        reply = _call_provider(self._provider, grounded)
        if reply.strip():
            return reply.strip()
        # Provider down on the final call: report the raw results.
        return "\n".join(tool_results)

    def _base_messages(
        self, text: str, context: dict[str, Any] | None
    ) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        facts: dict[str, Any] = {}
        if callable(self._live_context):
            try:
                facts = dict(self._live_context() or {})
            except Exception:  # noqa: BLE001 — live context is best-effort
                facts = {}
        if context:
            facts.update(context)
        if facts:
            lines = "\n".join(f"- {k}: {v}" for k, v in facts.items())
            messages.append({"role": "system", "content": f"Live context:\n{lines}"})
        messages.append({"role": "user", "content": text})
        return messages

    def _tool_prompt(self, category: str, tool_results: list[str]) -> dict[str, str]:
        schemas = self._router.category_schemas(category)
        lines = ["You have these tools in this category (JSON schemas):"]
        for schema in schemas:
            fn = schema.get("function", {})
            lines.append(f"- {fn.get('name')}: {fn.get('description', '')}")
        if tool_results:
            lines.append("Results so far:")
            lines.extend(f"- {r}" for r in tool_results)
        lines.append(
            "Reply with exactly one JSON object "
            '{"tool": "<name>", "args": {...}} to call a tool, '
            "or with your final answer (plain text, no JSON) when done."
        )
        return {"role": "system", "content": "\n".join(lines)}

    def _reflect_on_correction(self, text: str) -> None:
        if self._memory_store is None:
            return
        try:
            _reflection.extract_and_store(self._memory_store, text)
        except Exception:  # noqa: BLE001 — reflection must not break a turn
            logger.warning("[AgentLoop] correction reflection failed", exc_info=True)

    def _reflect_on_tool_failure(self, tool_name: str, error: str) -> None:
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
        except Exception:  # noqa: BLE001 — reflection must not break a turn
            logger.warning("[AgentLoop] failure reflection failed", exc_info=True)
