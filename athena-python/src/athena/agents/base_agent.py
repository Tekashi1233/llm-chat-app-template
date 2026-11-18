"""ATHENA Python v1 — agent interface.

The brain never knows which interface is active (desktop UI,
terminal chat, …): every interface hands user text to an agent
through this one method and gets plain text back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

__all__ = ["BaseAgent"]


class BaseAgent(ABC):
    """One turn of conversation: user text in, reply text out."""

    @property
    def name(self) -> str:
        """Human-readable agent name (e.g. "ATHENA")."""
        return "ATHENA"

    @abstractmethod
    def run_turn(self, user_text: str, *, context: dict[str, Any] | None = None) -> str:
        """Answer one user turn. Never raises — on failure it returns
        an honest plain-language message instead."""
        ...
