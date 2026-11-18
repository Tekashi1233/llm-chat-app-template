"""Session transcript repair — heal broken tool-use transcripts.

Ported from MemOS ``src/agent/session_transcript_repair.py`` (itself
ported from OpenClaw's session-transcript-repair.ts). Handles:

* Orphaned tool results (a ``Result: ...`` user message, or a
  ``role="tool"`` message, with no preceding tool call) — dropped.
* Orphaned tool calls (an assistant tool call with no following
  result) — patched with a synthetic failure result.
* Malformed JSON in fenced tool-call blocks — repaired in place
  (unquoted keys, single quotes, trailing commas).
* Consecutive same-role messages (except system) — merged.
* Truncated sessions — an assistant message ending with an unclosed
  ````` ``` ````` fence is closed; a trailing tool call with no result
  is patched as an orphaned call (same as MemOS).

Extension over MemOS (documented, not silent): structured tool calls
are recognised too — a message with a ``tool_calls`` list, and a
``role="tool"`` / ``tool_call_id`` message as a result — because
ATHENA's transcripts may carry structured calls, not only MemOS's
text ``Result:`` convention. Text behaviour is unchanged.

``repair_tool_use_result_pairing()`` keeps the MemOS return shape:
``{"messages", "repairs", "repaired", "stats"}``.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

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

_TOOL_CALL_PATTERN = re.compile(
    r"```json\s*\n?\s*\{[^}]*\"name\"\s*:\s*\"[^\"]+?\"[^}]*\}\s*\n?\s*```",
    re.DOTALL,
)

_BARE_TOOL_CALL_PATTERN = re.compile(
    r"\{\s*\"name\"\s*:\s*\"[^\"]+?\"\s*,\s*\"arguments\"\s*:\s*\{[^}]*\}\s*\}",
    re.DOTALL,
)

# Matches the "Result: ..." prefix the agent loop uses for tool outputs.
_TOOL_RESULT_PREFIX = re.compile(r"^Result:\s", re.IGNORECASE)

_SYNTHETIC_FAILURE = (
    "Result: Tool execution was interrupted (session crash recovery). "
    "The tool did not return a result."
)


def _message_has_tool_call(msg: dict[str, Any]) -> bool:
    """Assistant message contains a tool call (text or structured)."""
    if msg.get("tool_calls"):
        return True
    content = msg.get("content", "") or ""
    if not content:
        return False
    return bool(_TOOL_CALL_PATTERN.search(content) or _BARE_TOOL_CALL_PATTERN.search(content))


def _tool_call_ids(msg: dict[str, Any]) -> list[str]:
    """Structured tool-call ids in a message (empty for text calls)."""
    ids: list[str] = []
    for call in msg.get("tool_calls") or []:
        if isinstance(call, dict) and call.get("id"):
            ids.append(str(call["id"]))
    return ids


def _message_is_tool_result(msg: dict[str, Any]) -> bool:
    """Message is a tool result: structured ``role="tool"`` or text ``Result:``."""
    if msg.get("role") == "tool" or msg.get("tool_call_id"):
        return True
    content = msg.get("content", "") or ""
    if not content:
        return False
    return bool(_TOOL_RESULT_PREFIX.match(content))


def _try_fix_json_in_content(content: str) -> str:
    """Repair malformed JSON blocks (unquoted keys, single quotes, trailing commas)."""
    _BROAD_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n\s*```", re.DOTALL)

    def fix_block(text: str) -> str:
        text = re.sub(r"(\{|,)\s*(\w+)\s*:", r'\1 "\2":', text)
        text = re.sub(r":\s*'([^']*)'", r': "\1"', text)
        text = re.sub(r",\s*([\]}])", r"\1", text)
        return text

    def replace_json_block(m: re.Match) -> str:
        inner = m.group(1).strip()
        fixed = fix_block(inner)
        try:
            json.loads(fixed)
            return f"```json\n{fixed}\n```"
        except (ValueError, json.JSONDecodeError):
            return m.group(0)

    return _BROAD_JSON_BLOCK.sub(replace_json_block, content)


def _close_unclosed_fence(content: str) -> tuple[str, bool]:
    """Close a trailing unclosed ``` fence (truncated session)."""
    if content.count("```") % 2 == 1:
        return content.rstrip() + "\n```", True
    return content, False


def repair_tool_use_result_pairing(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """Repair a transcript so tool calls and results are properly paired."""
    stats = {
        "orphaned_results_dropped": 0,
        "orphaned_calls_patched": 0,
        "json_blocks_repaired": 0,
        "consecutive_roles_merged": 0,
        "unclosed_fences_closed": 0,
    }
    repairs: list[str] = []
    if not messages:
        return {"messages": [], "repairs": repairs, "repaired": False, "stats": stats}

    # Pass 1: repair malformed JSON + close unclosed fences (assistant only).
    working: list[dict[str, Any]] = []
    for msg in messages:
        msg_copy = dict(msg)
        if msg_copy.get("role") == "assistant":
            original = msg_copy.get("content", "") or ""
            fixed = _try_fix_json_in_content(original)
            if fixed != original:
                msg_copy["content"] = fixed
                stats["json_blocks_repaired"] += 1
                repairs.append(f"Repaired malformed JSON in assistant message: {original[:60]}...")
            closed, was_open = _close_unclosed_fence(msg_copy.get("content", "") or "")
            if was_open:
                msg_copy["content"] = closed
                stats["unclosed_fences_closed"] += 1
                repairs.append("Closed unclosed code fence in truncated assistant message.")
        working.append(msg_copy)

    # Pass 2: orphaned results / orphaned calls.
    repaired: list[dict[str, Any]] = []
    pending_call_ids: set[str] = set()
    i = 0
    while i < len(working):
        msg = working[i]
        role = msg.get("role", "")

        if _message_is_tool_result(msg):
            call_id = msg.get("tool_call_id")
            paired = False
            if call_id and str(call_id) in pending_call_ids:
                paired = True
                pending_call_ids.discard(str(call_id))
            elif not call_id and repaired and repaired[-1].get("role") == "assistant" and _message_has_tool_call(repaired[-1]):
                paired = True
            if paired:
                repaired.append(msg)
            else:
                stats["orphaned_results_dropped"] += 1
                repairs.append(
                    f"Dropped orphaned tool result (no preceding tool call): "
                    f"{str(msg.get('content', ''))[:80]}..."
                )
            i += 1
            continue

        if role == "assistant" and _message_has_tool_call(msg):
            repaired.append(msg)
            for call_id in _tool_call_ids(msg):
                pending_call_ids.add(call_id)
            has_result_next = (
                i + 1 < len(working) and _message_is_tool_result(working[i + 1])
            )
            if not has_result_next:
                stats["orphaned_calls_patched"] += 1
                repairs.append(
                    f"Injected synthetic tool failure for orphaned tool call: "
                    f"{str(msg.get('content', ''))[:60]}..."
                )
                call_ids = _tool_call_ids(msg)
                if call_ids:
                    repaired.append({
                        "role": "tool",
                        "tool_call_id": call_ids[0],
                        "content": _SYNTHETIC_FAILURE,
                    })
                    pending_call_ids.discard(call_ids[0])
                else:
                    repaired.append({"role": "user", "content": _SYNTHETIC_FAILURE})
            i += 1
            continue

        repaired.append(msg)
        i += 1

    # Pass 3: merge consecutive same-role messages (except system).
    merged: list[dict[str, Any]] = []
    for msg in repaired:
        if merged and msg.get("role") == merged[-1].get("role") and msg.get("role") != "system":
            merged[-1]["content"] = (merged[-1].get("content", "") or "") + "\n" + (msg.get("content", "") or "")
            stats["consecutive_roles_merged"] += 1
            repairs.append(f"Merged consecutive {msg['role']} messages")
        else:
            merged.append(msg)

    is_repaired = any(v > 0 for v in stats.values())
    if is_repaired:
        logger.info(f"Transcript repair complete: {stats} | {len(repairs)} repairs")
    return {"messages": merged, "repairs": repairs, "repaired": is_repaired, "stats": stats}


def extract_identifiers(text: str) -> list[str]:
    """Extract all opaque identifiers from text (preservation checking)."""
    return IDENTIFIER_PATTERN.findall(text)


# Convenience alias.
repair_transcript = repair_tool_use_result_pairing
