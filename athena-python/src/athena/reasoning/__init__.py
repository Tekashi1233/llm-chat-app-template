"""ATHENA Python v1 — reasoning package.

Reflection on failures/corrections (from Friday's ReflectionAgent
design) and chain-of-thought ``<thought>`` block helpers.
"""

from __future__ import annotations

from athena.reasoning.chain_of_thought import (
    THOUGHT_SYSTEM_PROMPT,
    add_thought_instruction,
    build_thought_instruction,
    strip_thought_block,
)
from athena.reasoning.reflection import (
    check_for_correction,
    extract_and_store,
    extract_lesson,
    is_correction,
    on_tool_failure,
)

__all__ = [
    "THOUGHT_SYSTEM_PROMPT",
    "add_thought_instruction",
    "build_thought_instruction",
    "check_for_correction",
    "extract_and_store",
    "extract_lesson",
    "is_correction",
    "on_tool_failure",
    "strip_thought_block",
]
