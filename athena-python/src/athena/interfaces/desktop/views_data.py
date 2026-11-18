"""ATHENA Python v1 — section-panel data (pure, no Qt).

The constants and data-shaping helpers behind the six "···"
menu panels (``views.py``): honest "not enabled yet" strings, the
device quick-action list, the tool catalogue, age formatting,
conversation grouping and memory filtering.

Kept in its own module so all of it stays testable (and
importable) on a machine without PySide6 — ``views.py``
re-exports the names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "NOT_ENABLED_DEVICE",
    "NOT_ENABLED_MEMORIES",
    "NOT_ENABLED_ACTIVITY",
    "DEVICE_QUICK_ACTIONS",
    "UNPAIR_STEPS",
    "TOOL_CATALOG",
    "ConversationSummary",
    "format_age",
    "format_relative_time",
    "snippet",
    "group_conversations",
    "filter_memories",
    "is_approved_device_prompt",
]

# --- Honest "not enabled yet" strings (C# ViewHelpers) -----------------
NOT_ENABLED_DEVICE = (
    "Device status isn't enabled yet — it needs the N2 read endpoints."
)
NOT_ENABLED_MEMORIES = (
    "Memory Vault isn't enabled yet — it needs the N2 read endpoints."
)
NOT_ENABLED_ACTIVITY = (
    "Activity isn't enabled yet — it needs the N2 read endpoints."
)
RETRY_HINT_REFRESH = "Press Refresh to try again."
RETRY_HINT_REOPEN = "Open this section again from the menu (···) to retry."
NO_MEMORY_SELECTED = "Select a memory to see the details."

# --- Device quick actions (C# DeviceQuickActions) -----------------------
#: (label, chat prompt). Only a prompt from this list may go down
#: the device-command path.
DEVICE_QUICK_ACTIONS: tuple[tuple[str, str], ...] = (
    ("Ping google.com", "Ping google.com"),
    ("Network info", "Show network info for the laptop"),
    ("List installed programs", "List the programs installed on the laptop"),
    ("Open Notepad", "Open Notepad on the laptop"),
)


def is_approved_device_prompt(prompt: str) -> bool:
    return any(prompt == p for _label, p in DEVICE_QUICK_ACTIONS)


#: Exact steps shown by the Unpair card. The revoke itself runs
#: from the Companion tray app on the laptop — never from here.
UNPAIR_STEPS = (
    "To finish unpairing: on the laptop, right-click the Companion "
    "tray icon → “Unpair this laptop” → confirm. The laptop is then "
    "revoked and shows the pairing screen again."
)

# --- Tool catalogue (C# ToolCatalog — static documentation) --------------
#: (name, description, category). Whether a tool is actually
#: registered right now comes from /api/model-info.
TOOL_CATALOG: tuple[tuple[str, str, str], ...] = (
    ("get_weather", "Current weather for any city or place in the world.", "Knowledge"),
    ("search_web", "Look up a topic, person, place or fact on Wikipedia, with a short summary and a link.", "Knowledge"),
    ("calculate", "Evaluate a maths expression exactly, instead of working it out by hand.", "Knowledge"),
    ("get_datetime", "Current date and time, in any timezone you ask for.", "Knowledge"),
    ("get_device_status", "Check whether the paired Windows laptop is on, and how it is doing: CPU, memory, disks and network.", "Laptop"),
    ("open_app", "Open an approved app on the paired laptop, e.g. Notepad or Chrome.", "Laptop"),
    ("search_files", "Find files by name in an approved folder on the paired laptop.", "Laptop"),
    ("create_file", "Create a new text file in an approved folder on the paired laptop. Asks for approval on screen first.", "Laptop"),
    ("edit_file", "Add to or replace text in a file on the paired laptop. Asks for approval on screen first.", "Laptop"),
    ("read_file", "Read a text file in an approved folder on the paired laptop.", "Laptop"),
    ("run_command", "Run an approved command on the paired laptop, e.g. ping, and get its output back.", "Laptop"),
    ("get_action_history", "What ATHENA has done on the paired laptop lately: apps opened, files changed, commands run, refusals.", "Laptop"),
    ("remember_fact", "Remember something durable about you for later conversations: a fact, a preference, a project fact or a plan.", "Memory"),
)


def _get(item: Any, key: str, default: Any = None) -> Any:
    """Read ``key`` from a dict or an object, uniformly."""
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def format_age(seconds: int | float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} second{'s' if seconds != 1 else ''} ago"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    hours = seconds // 3600
    return f"{hours} hour{'s' if hours != 1 else ''} ago"


def format_relative_time(when: datetime | int | float | str | None) -> str:
    """Human relative time for a datetime / epoch ms / ISO string."""
    if when is None:
        return "unknown"
    if isinstance(when, (int, float)):
        # epoch: ms when large, seconds otherwise
        ts = when / 1000.0 if when > 10**12 else float(when)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    elif isinstance(when, str):
        try:
            dt = datetime.fromisoformat(when.replace("Z", "+00:00"))
        except ValueError:
            return when
    else:
        dt = when
    now = datetime.now(timezone.utc)
    delta = (now - dt).total_seconds()
    if delta < 0:
        return "just now"
    if delta < 60:
        return "just now"
    if delta < 3600:
        return format_age(delta)
    if delta < 86400:
        hours = int(delta // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(delta // 86400)
    return f"{days} day{'s' if days != 1 else ''} ago"


def snippet(text: str | None, limit: int = 90) -> str:
    """One-line snippet; a missing fact shows empty, never crashes."""
    one_line = (text or "").strip().replace("\n", " ").replace("\r", " ")
    one_line = re.sub(r"\s+", " ", one_line)
    if len(one_line) <= limit:
        return one_line
    return one_line[: limit - 1].rstrip() + "…"


@dataclass
class ConversationSummary:
    id: str
    title: str
    last_message_at: Any
    message_count: int
    last_snippet: str


def group_conversations(messages: list[dict]) -> list[ConversationSummary]:
    """Group backup messages by conversation. Titles come from the
    first user message; nothing is invented. Newest first."""
    grouped: dict[str, list[dict]] = {}
    for row in messages:
        cid = str(_get(row, "conversation_id") or _get(row, "conversationId") or "default")
        grouped.setdefault(cid, []).append(row)
    summaries: list[ConversationSummary] = []
    for cid, rows in grouped.items():
        rows.sort(key=lambda r: _get(r, "created_at_ms", 0) or 0)
        first_user = next((r for r in rows if _get(r, "role") == "user"), None)
        title = snippet(_get(first_user, "content", "") if first_user else "", 60) or "Conversation"
        last = rows[-1]
        summaries.append(
            ConversationSummary(
                id=cid,
                title=title,
                last_message_at=_get(last, "created_at_ms")
                or _get(last, "created_at"),
                message_count=len(rows),
                last_snippet=snippet(_get(last, "content", "")),
            )
        )
    summaries.sort(key=lambda s: s.last_message_at or 0, reverse=True)
    return summaries


def filter_memories(
    memories: list[dict],
    query: str = "",
    kind: str = "All",
    status: str = "All",
) -> list[dict]:
    """Memory Vault filter. A row with a missing topic/fact simply
    does not match — it never crashes the filter."""
    q = query.strip().lower()
    out = []
    for m in memories:
        if kind != "All" and _get(m, "kind") != kind:
            continue
        if status != "All" and _get(m, "status") != status:
            continue
        if q:
            topic = (_get(m, "topic") or "").lower()
            fact = (_get(m, "fact") or "").lower()
            if q not in topic and q not in fact:
                continue
        out.append(m)
    return out
