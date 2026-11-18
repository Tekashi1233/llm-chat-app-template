"""Reflection — extract a lesson from a failure or a user correction.

Design from Friday's README §"Self-Critique & Self-Learning
(ReflectionAgent — Layer 5)" — its code was never in that repo, so
this is a design port, not a code copy:

* On tool failure: diagnose WHY it failed and write a behavioural
  lesson (``on_tool_failure``).
* On user correction: a zero-cost REGEX GATE (``check_for_correction``)
  runs before any LLM call. Only when it matches does extraction run.
  The gate is pure regex — no LLM, no network, no I/O — so the
  no-match path costs nothing.
* Lessons are ``{trigger, fix, confidence}`` dicts, stored in the
  local lessons store (Layer 5 equivalent), never in D1.

Difference from Friday's design: Friday fired a diagnostic LLM call
(temperature=0) after the gate matched to extract the lesson. No LLM
is wired into this module yet, so extraction here is deterministic
pattern-matching over the user's own words (``should ...`` /
``instead ...`` / ``I wanted ...``). An LLM refinement step can
replace the ``fix`` later without changing the gate or the dict shape.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from athena.memory.lessons import Lesson, LessonStore

#: Correction phrases — the zero-cost regex gate. Additions must stay
#: cheap literal/regex matches; nothing here may call an LLM or I/O.
_CORRECTION_PATTERNS = [
    r"that'?s wrong",
    r"that is wrong",
    r"that'?s not right",
    r"that'?s incorrect",
    r"that'?s not correct",
    r"not what i asked",
    r"not what i meant",
    r"not what i wanted",
    r"that'?s not what i",
    r"you'?re wrong",
    r"you are wrong",
    r"wrong answer",
    r"you misunderstood",
    r"that didn'?t work",
    r"don'?t do that",
    r"stop doing that",
    r"no,?\s+that'?s not",
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in _CORRECTION_PATTERNS]

#: "What I actually wanted" extractors, tried in order.
_FIX_PATTERNS = [
    re.compile(r"\bshould\s+(?:have\s+)?(.+?)(?:[.!?]|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"\binstead\s+(?:of\s+\S+\s+)?(.+?)(?:[.!?]|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bi\s+(?:wanted|asked for|meant)\s+(.+?)(?:[.!?]|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"\bdo\s+(.+?)\s+instead\b", re.IGNORECASE | re.DOTALL),
]

_DEFAULT_FIX = (
    "Re-read the request and answer what was actually asked; "
    "do not repeat the corrected behaviour."
)


def check_for_correction(text: str | None) -> Optional[str]:
    """Regex gate: return the matched correction phrase, else None.

    Zero-cost: pure regex over the input, no LLM / network / I/O.
    """
    if not text:
        return None
    for pattern in _COMPILED:
        match = pattern.search(text)
        if match:
            return match.group(0)
    return None


def is_correction(text: str | None) -> bool:
    """True when the regex gate matches ``text``."""
    return check_for_correction(text) is not None


def _extract_fix(text: str) -> tuple[str, float]:
    """Best-effort fix from the user's own words + a confidence."""
    for pattern in _FIX_PATTERNS:
        match = pattern.search(text)
        if match:
            fix = " ".join(match.group(1).split()).strip()
            if len(fix) >= 3:
                return fix, 0.6
    return _DEFAULT_FIX, 0.5


def extract_lesson(
    user_message: str,
    context: str | None = None,
) -> Optional[dict]:
    """Extract ``{trigger, fix, confidence}`` from a user correction.

    Returns None when the regex gate does not match — the zero-cost
    path. ``trigger`` is the correction itself (what went wrong);
    ``fix`` is what the user said they wanted instead, or a safe
    default. ``context`` (e.g. the assistant reply being corrected)
    is prepended to the trigger when given.
    """
    if check_for_correction(user_message) is None:
        return None
    fix, confidence = _extract_fix(user_message)
    trigger = " ".join(user_message.split()).strip()[:500]
    if context:
        trigger = f"When told: {context[:200]} — user corrected: {trigger}"
    return {"trigger": trigger, "fix": fix, "confidence": confidence}


def on_tool_failure(
    tool_name: str,
    error: str,
    context: str | None = None,
) -> dict:
    """Lesson dict for an exhausted tool failure (reflection on failure)."""
    trigger = f"Tool '{tool_name}' failed: {error}".strip()
    if context:
        trigger += f" (context: {context[:200]})"
    return {
        "trigger": trigger[:500],
        "fix": (
            f"Retry '{tool_name}' once with the same arguments; if it fails "
            "again, report the failure instead of retrying in a loop."
        ),
        "confidence": 0.5,
    }


def extract_and_store(
    store: "LessonStore", user_message: str, context: str | None = None
) -> Optional["Lesson"]:
    """Gate + extract + store in one call; None when no correction."""
    lesson_dict = extract_lesson(user_message, context=context)
    if lesson_dict is None:
        return None
    return store.add_lesson(
        trigger=lesson_dict["trigger"],
        fix=lesson_dict["fix"],
        confidence=lesson_dict["confidence"],
        source="reflection",
        stated=False,
    )
