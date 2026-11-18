"""ATHENA Python v1 — LLM package (Worker, Ollama, slotted provider).

The brain's model layer. Importing this package needs no Qt, no
audio, no voice imports — see ``test_core_imports_need_no_qt_or_audio``.
"""

from __future__ import annotations

from athena.llm.base import LLMProvider
from athena.llm.modes import Mode, ModeSwitch
from athena.llm.ollama_client import OllamaClient, OllamaError
from athena.llm.slotted_provider import OllamaSlot, SlottedProvider, WorkerSlot
from athena.llm.worker_client import (
    AthenaApiError,
    KeyValidationResult,
    WorkerClient,
    parse_sse,
)
from athena.reasoning.chain_of_thought import strip_thought_block

__all__ = [
    "AthenaApiError",
    "KeyValidationResult",
    "LLMProvider",
    "Mode",
    "ModeSwitch",
    "OllamaClient",
    "OllamaError",
    "OllamaSlot",
    "SlottedProvider",
    "WorkerClient",
    "WorkerSlot",
    "parse_sse",
    "strip_thought_block",
]
