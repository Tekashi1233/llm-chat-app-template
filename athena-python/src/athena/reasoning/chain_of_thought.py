"""ATHENA Python v1 — chain-of-thought helpers.

The local (Ollama) model is asked to reason inside a
``<thought>…</thought>`` block first and then answer; the block is
stripped before display so the user only ever sees the answer.
Moved here from the slotted provider so the reasoning layer — not
the provider — owns the thought format.
"""

from __future__ import annotations

import re

__all__ = [
    "THOUGHT_SYSTEM_PROMPT",
    "add_thought_instruction",
    "build_thought_instruction",
    "strip_thought_block",
]

#: Instruction prepended (as a system message) on the Ollama path so
#: the local model shows its reasoning in a block we can strip.
THOUGHT_SYSTEM_PROMPT = (
    "Think step by step inside a <thought>...</thought> block first, "
    "then give your final answer after the closing tag. "
    "Only the text after </thought> is shown to the user, so put "
    "nothing the user needs inside the thought block."
)

_THOUGHT_RE = re.compile(r"<thought>.*?</thought>", re.DOTALL | re.IGNORECASE)
_UNCLOSED_THOUGHT_RE = re.compile(r"<thought>.*$", re.DOTALL | re.IGNORECASE)


def build_thought_instruction() -> str:
    """The system-prompt text that asks for a ``<thought>`` block."""
    return THOUGHT_SYSTEM_PROMPT


def add_thought_instruction(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return ``messages`` with the thought instruction prepended."""
    return [{"role": "system", "content": THOUGHT_SYSTEM_PROMPT}, *messages]


def strip_thought_block(text: str) -> str:
    """Remove ``<thought>…</thought>`` reasoning blocks from a reply.

    Also handles an unclosed block (model stopped mid-thought) by
    dropping everything from the opening tag on. Pure function.
    """
    cleaned = _THOUGHT_RE.sub("", text)
    cleaned = _UNCLOSED_THOUGHT_RE.sub("", cleaned)
    return cleaned.strip()
