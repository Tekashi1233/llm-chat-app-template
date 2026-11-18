"""ATHENA Python v1 — tool base types.

Every local capability ATHENA can do on the laptop is a :class:`BaseTool`
subclass. Tools never show UI themselves and never reach the network
except through their own documented call (weather / web search). Any
tool that writes a user file takes its approval from the
:class:`ToolContext` confirm callback — the UI supplies the on-screen
Allow/Deny popup (60s auto-deny lives there, in the UI); a tool that
gets no confirmer, or a ``False`` answer, must not write anything.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

__all__ = [
    "ConfirmCallback",
    "ToolContext",
    "ToolResult",
    "BaseTool",
]

#: ``confirm(summary, details) -> True`` means the owner tapped Allow
#: on the on-screen popup. ``False`` (including the UI's 60-second
#: auto-deny) means Deny. The callback must never be called by a tool
#: for a read-only action.
ConfirmCallback = Callable[[str, dict[str, Any]], bool]


@dataclass
class ToolContext:
    """Everything a tool run needs from the UI/brain, and nothing else.

    ``confirm`` is supplied by the UI for turns that may write files.
    When it is ``None``, every write tool refuses — a tool must never
    invent its own approval. ``confirm_timeout_seconds`` documents the
    UI's auto-deny window (60s, matching the C# Companion popup); the
    UI enforces it, tools only honour the callback's answer.
    """

    confirm: ConfirmCallback | None = None
    confirm_timeout_seconds: float = 60.0
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """One tool run's outcome. ``text`` is the human-readable answer
    the brain may quote back; ``data`` is structured detail."""

    ok: bool
    text: str = ""
    data: dict[str, Any] | None = None
    error: str | None = None

    @classmethod
    def success(cls, text: str, data: dict[str, Any] | None = None) -> ToolResult:
        return cls(ok=True, text=text, data=data)

    @classmethod
    def failure(cls, error: str, text: str | None = None) -> ToolResult:
        return cls(ok=False, text=text or error, error=error)


class BaseTool:
    """One local capability.

    Subclasses set the class attributes and implement :meth:`run`.
    ``scope`` is ``"general"`` (any conversation may use it) or
    ``"brain"`` (only the brain/internal path may call it — e.g. tools
    that touch memory or notes the model should not invoke directly).
    """

    name: str = ""
    description: str = ""
    category: str = "general"
    scope: str = "general"
    #: JSON-Schema ``properties`` for the tool's arguments.
    parameters: dict[str, Any] = {}
    required: list[str] = []

    def to_schema(self) -> dict[str, Any]:
        """LLM-facing JSON schema for this tool (OpenAI function style)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": dict(self.parameters),
                    "required": list(self.required),
                },
            },
        }

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        """Execute the tool. Must never raise for bad input — return a
        failed :class:`ToolResult` with an honest reason instead."""
        raise NotImplementedError

    # -- helpers -----------------------------------------------------

    def _missing_args(self, args: dict[str, Any]) -> list[str]:
        return [name for name in self.required if name not in args or args[name] in (None, "")]

    def _require_confirm(
        self,
        context: ToolContext | None,
        summary: str,
        details: dict[str, Any],
    ) -> ToolResult | None:
        """Ask the owner's on-screen popup. Returns ``None`` when the
        owner allowed the write; otherwise a failed :class:`ToolResult`
        explaining the refusal. No confirmer, no write — ever."""
        if context is None or context.confirm is None:
            return ToolResult.failure(
                "No on-screen confirmer supplied — file writes need the "
                "owner's Allow tap, so this write was refused."
            )
        try:
            allowed = context.confirm(summary, details)
        except Exception as exc:  # a broken confirmer is a denial, not a crash
            return ToolResult.failure(f"Confirmation failed ({exc}); write refused.")
        if not allowed:
            return ToolResult.failure(
                "Owner denied the write (or the 60-second approval window expired)."
            )
        return None
