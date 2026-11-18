"""ATHENA Python v1 — category-dispatch routing.

The model is never shown 60+ raw tool schemas at once. It is shown the
category list first (see :func:`category_overview`), picks one category,
and only then sees that category's tool schemas
(:func:`category_schemas`). :func:`route` is the local keyword fallback
for intents the model did not already route; :func:`dispatch` is the
single entry point the brain calls, and it refuses a tool that does not
belong to the category it was dispatched under.

Categories (ported from the jarvis-exp category-dispatch design, cut
down to the ten ATHENA v1 actually implements):

* system — device status, approved commands
* apps — open an approved app
* files — search / read / create / edit files in approved folders
* web — look things up online
* time — date and time
* calc — maths
* media — YouTube and browser-tab control
* screen — what is on screen right now (on-demand only)
* weather — current weather
* notes — ATHENA's own local notes
"""

from __future__ import annotations

import re
from typing import Any

from athena.tools.base_tool import ToolContext, ToolResult

__all__ = [
    "CATEGORIES",
    "CATEGORY_DESCRIPTIONS",
    "category_overview",
    "category_schemas",
    "route",
    "dispatch",
]


def _registry():
    """The tool registry, imported lazily.

    ``athena.tools`` must not import this router at module level (and
    vice versa) — the layout rule forbids tools -> router imports —
    so the registry is resolved on first use, when every module is
    already loaded, instead of at import time.
    """
    from athena.tools.registry import REGISTRY

    return REGISTRY


#: Category -> (one-line description shown to the model, keywords).
CATEGORIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "system": (
        "Device status and approved read-only system commands.",
        ("battery", "cpu", "memory", "disk", "network", "ping", "ipconfig",
         "programs", "installed", "laptop", "device status", "command"),
    ),
    "apps": (
        "Open an approved app on the laptop.",
        ("open", "launch", "start", "notepad", "calculator", "paint",
         "explorer", "chrome", "edge", "browser", "app"),
    ),
    "files": (
        "Find, read, create or edit files in approved folders.",
        ("file", "files", "folder", "document", "read", "create", "edit",
         "write", "search", "find", "desktop", "documents", "downloads"),
    ),
    "web": (
        "Look up a topic, person, place or fact online.",
        # NOTE: "what is" was here and outscored real categories
        # ("what is the weather" routed to web, not weather) — it is
        # too generic to be a keyword. "who is" stays: it is specific
        # to looking a person up.
        ("search", "look up", "wikipedia", "who is", "tell me about"),
    ),
    "time": (
        "Current date and time, in any timezone.",
        ("time", "date", "today", "clock", "timezone", "what day"),
    ),
    "calc": (
        "Evaluate a maths expression exactly.",
        ("calculate", "calc", "plus", "minus", "times", "divided", "percent",
         "sqrt", "square root", "add", "subtract", "multiply", "divide"),
    ),
    "media": (
        "YouTube and browser-tab control.",
        ("youtube", "video", "play", "pause", "tab", "music", "watch"),
    ),
    "screen": (
        "What is on screen right now — active window and on-demand OCR.",
        ("screen", "window", "active", "what am i looking at", "what's on",
         "read the screen", "ocr"),
    ),
    "weather": (
        "Current weather for any city or place.",
        ("weather", "temperature", "rain", "sunny", "forecast", "humid",
         "wind", "cloudy", "hot", "cold outside"),
    ),
    "notes": (
        "ATHENA's own local notes — save one, list them.",
        ("note", "notes", "remember this note", "jot down", "my notes"),
    ),
}

CATEGORY_DESCRIPTIONS: dict[str, str] = {
    name: desc for name, (desc, _) in CATEGORIES.items()
}

_WORD_RE = re.compile(r"[a-z0-9]+(?:'[a-z]+)?")


def category_overview() -> list[dict[str, str]]:
    """The category list the model sees first: name + description only."""
    return [
        {"name": name, "description": CATEGORY_DESCRIPTIONS[name]}
        for name in sorted(CATEGORIES)
    ]


def category_schemas(category: str) -> list[dict[str, Any]]:
    """Only the chosen category's tool schemas — never the whole list."""
    return [tool.to_schema() for tool in _registry().tools_by_category(category)]


def route(intent_text: str) -> str:
    """Keyword-score an intent to its best category.

    Returns ``"general"`` when nothing matches — the caller then shows
    the category overview and lets the model choose. Ties are broken
    by the order categories are defined above.
    """
    text = (intent_text or "").lower()
    best: str | None = None
    best_score = 0
    for name, (_, keywords) in CATEGORIES.items():
        score = 0
        for keyword in keywords:
            if " " in keyword:
                if keyword in text:
                    score += 2
            elif keyword in _WORD_RE.findall(text):
                score += 1
        if score > best_score:
            best, best_score = name, score
    return best or "general"


def dispatch(
    category: str,
    tool_name: str,
    args: dict[str, Any],
    context: ToolContext | None = None,
) -> ToolResult:
    """Run one tool inside its category.

    Refuses (honestly, without running anything) when the tool does not
    exist or does not belong to the given category — that is the whole
    point of category dispatch: a tool is only callable from its own
    category's schemas.
    """
    tool = _registry().get(tool_name)
    if tool is None:
        return ToolResult.failure(f"Unknown tool: {tool_name!r}.")
    if tool.category != category:
        return ToolResult.failure(
            f"Tool {tool_name!r} belongs to category {tool.category!r}, "
            f"not {category!r}; it can only be called from its own category."
        )
    try:
        return tool.run(dict(args), context)
    except Exception as exc:  # tools must not raise, but never crash the brain
        return ToolResult.failure(f"Tool {tool_name!r} failed: {exc}")
