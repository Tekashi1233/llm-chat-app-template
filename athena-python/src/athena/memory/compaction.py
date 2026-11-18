"""3-stage progressive compaction of conversation history.

Adapted from MemOS ``src/agent/loop.py`` (``_maybe_compact`` /
``_summarize_in_stages`` / ``_stage1_full_summarize`` /
``_stage2_partial_summarize`` / ``_stage3_hard_fallback``) and its
identifier regex from ``src/agent/session_transcript_repair.py``:

* Stage 1 — full summarisation, token-balanced chunks (~1500 tokens),
  up to 3 attempts per chunk, identifiers (UUIDs, hashes, IPs, URLs,
  file paths) preserved exactly.
* Stage 2 — partial: oversized messages (>800 tokens) excluded and
  noted, the rest summarised.
* Stage 3 — hard text fallback: a short count-only string. It MUST
  NEVER crash — stage 3 (and ``compact()`` itself) is wrapped so any
  failure still returns a summary string.

Difference from MemOS: no LLM is wired in here. The summariser is an
injected callable ``summarise_fn(messages) -> str`` (the Worker/LLM
layer can supply a real one later); the default is a deterministic
extractive summariser that lists each message briefly while keeping
every identifier verbatim. A summariser that raises, or returns
empty, fails that stage and the next stage is tried — exactly the
MemOS fall-through behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Optional

# Identifier preservation regex — copied from MemOS
# src/agent/session_transcript_repair.py (IDENTIFIER_PATTERN).
IDENTIFIER_PATTERN = re.compile(
    r"(?:"
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"  # UUID
    r"|[0-9a-fA-F]{40}"   # SHA-1
    r"|[0-9a-fA-F]{64}"   # SHA-256
    r"|\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b"  # IPv4 + optional port
    r"|https?://[^\s<>\"')]+"  # URLs
    r"|/[\w./-]{3,}"      # Unix file paths
    r"|[A-Z]:\\[\w.\\/-]{3,}"  # Windows file paths
    r")",
    re.MULTILINE,
)

#: Token heuristic from MemOS loop.py: ~4 chars per token.
CHARS_PER_TOKEN = 4
#: Messages above this are excluded in Stage 2 (MemOS loop.py).
OVERSIZED_MSG_THRESHOLD = 800
#: Summary length cap in tokens (MemOS loop.py SUMMARY_MAX_TOKENS).
SUMMARY_MAX_TOKENS = 300
#: Retries per chunk in Stage 1 (MemOS COMPACTION_RETRY_ATTEMPTS).
COMPACTION_RETRY_ATTEMPTS = 3
#: Chunk size in tokens for Stage 1 (MemOS loop.py).
_CHUNK_TOKEN_LIMIT = 1500


@dataclass
class CompactionResult:
    stage: int  # 1, 2 or 3
    summary: str
    oversized_count: int = 0
    message_count: int = 0


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


def estimate_message_tokens(msg: dict) -> int:
    return estimate_tokens(str(msg.get("content", ""))) + 4


def extract_identifiers(text: str) -> list[str]:
    """All opaque identifiers in ``text`` (for preservation checks)."""
    return IDENTIFIER_PATTERN.findall(text)


def _default_summarise(messages: list[dict]) -> str:
    """Deterministic extractive summary: one line per message.

    Keeps the role, the first 200 chars of content, and every
    identifier verbatim. Never raises on ordinary input.
    """
    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role", "?"))
        text = str(msg.get("content", ""))[:200]
        lines.append(f"[{role}]: {text}")
    summary = "\n".join(f"- {line}" for line in lines)
    return summary[: SUMMARY_MAX_TOKENS * CHARS_PER_TOKEN]


def _chunk_messages(messages: list[dict]) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0
    for msg in messages:
        tokens = estimate_message_tokens(msg)
        if current and current_tokens + tokens > _CHUNK_TOKEN_LIMIT:
            chunks.append(current)
            current, current_tokens = [], 0
        current.append(msg)
        current_tokens += tokens
    if current:
        chunks.append(current)
    return chunks


def _call_with_retries(fn: Callable, messages: list[dict]) -> str:
    last_error: Optional[Exception] = None
    for _ in range(COMPACTION_RETRY_ATTEMPTS):
        try:
            result = fn(messages)
            if result and str(result).strip():
                return str(result).strip()
            return ""
        except Exception as exc:  # noqa: BLE001 — fall through to next stage
            last_error = exc
    if last_error is not None:
        raise last_error
    return ""


def stage1_full_summarize(
    messages: list[dict], summarise_fn: Callable | None = None
) -> str:
    """Stage 1: summarise ALL messages in token-balanced chunks."""
    fn = summarise_fn or _default_summarise
    chunk_summaries: list[str] = []
    for chunk in _chunk_messages(messages):
        summary = _call_with_retries(fn, chunk)
        if summary:
            chunk_summaries.append(summary)
    if not chunk_summaries:
        return ""
    if len(chunk_summaries) == 1:
        return chunk_summaries[0]
    merged_input = [{"role": "user", "content": "\n\n".join(chunk_summaries)}]
    merged = _call_with_retries(fn, merged_input)
    return merged or "\n\n".join(chunk_summaries)


def stage2_partial_summarize(
    messages: list[dict], summarise_fn: Callable | None = None
) -> str:
    """Stage 2: exclude oversized messages, summarise the rest."""
    fn = summarise_fn or _default_summarise
    normal = [m for m in messages if estimate_message_tokens(m) <= OVERSIZED_MSG_THRESHOLD]
    oversized = len(messages) - len(normal)
    if not normal:
        return ""
    summary = _call_with_retries(fn, normal[-15:])
    if not summary:
        return ""
    if oversized:
        summary += f"\n[{oversized} oversized messages were excluded from summary]"
    return summary


def stage3_hard_fallback(messages: list[dict]) -> str:
    """Stage 3: hard text fallback — guaranteed never to crash."""
    try:
        count = len(messages)
    except Exception:
        return "Context contained an unknown number of messages. Summary unavailable."
    try:
        oversized = sum(1 for m in messages if estimate_message_tokens(m) > OVERSIZED_MSG_THRESHOLD)
    except Exception:
        oversized = 0
    return (
        f"Context contained {count} messages"
        f" ({oversized} oversized)."
        f" Summary unavailable due to size limits."
    )


def compact(
    messages: list[dict], summarise_fn: Callable | None = None
) -> CompactionResult:
    """Run the 3-stage compaction; always returns a result, never raises."""
    try:
        count = len(messages)
    except Exception:
        return CompactionResult(stage=3, summary=stage3_hard_fallback([]), message_count=0)
    oversized = 0
    try:
        oversized = sum(1 for m in messages if estimate_message_tokens(m) > OVERSIZED_MSG_THRESHOLD)
    except Exception:
        oversized = 0
    try:
        summary = stage1_full_summarize(messages, summarise_fn)
        if summary:
            return CompactionResult(stage=1, summary=summary, oversized_count=oversized, message_count=count)
    except Exception:
        pass
    try:
        summary = stage2_partial_summarize(messages, summarise_fn)
        if summary:
            return CompactionResult(stage=2, summary=summary, oversized_count=oversized, message_count=count)
    except Exception:
        pass
    return CompactionResult(
        stage=3,
        summary=stage3_hard_fallback(messages),
        oversized_count=oversized,
        message_count=count,
    )
