"""ATHENA Python v1 — LLM provider interface.

The one interface every brain layer talks to. A provider takes a
list of ``{"role": ..., "content": ...}`` messages and returns a
plain-text reply. It never raises for a bad reply — concrete
providers (see :mod:`athena.llm.slotted_provider`) return honest
error text instead. Slot details (Worker vs Ollama, fallback rules)
live behind this interface; the agent loop never knows which slot
served a request.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["LLMProvider"]


@runtime_checkable
class LLMProvider(Protocol):
    """Anything that can answer one turn of chat."""

    @property
    def name(self) -> str:
        """Human-readable provider name (e.g. "ATHENA cloud (Worker)")."""
        ...

    @property
    def is_available(self) -> bool:
        """Cheap local check — True when the provider can serve now."""
        ...

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float = 60.0,
    ) -> str:
        """One reply for ``messages``. Never raises for a bad reply."""
        ...
