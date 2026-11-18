"""ATHENA Python v1 — message rules (pure, no Qt).

Port of C# ``MessageValidation`` and ``DeviceReplySignals``.
Kept in its own module so the rules stay testable (and importable)
on a machine without PySide6 — ``chat_view`` re-exports them.
"""

from __future__ import annotations

__all__ = [
    "MAX_MESSAGE_LENGTH",
    "APPROVAL_WAITING_PHRASE",
    "is_valid_user_message",
    "is_within_length_limit",
    "is_approval_waiting",
]

#: Longest user message the app will send (C# MessageValidation).
MAX_MESSAGE_LENGTH = 8000

#: Reply text that means a confirmation popup is waiting on the
#: laptop screen (C# DeviceReplySignals — exact Worker sentence).
APPROVAL_WAITING_PHRASE = "confirmation popup is waiting"


def is_valid_user_message(text: str | None) -> bool:
    """Only a non-empty user message is ever sent."""
    return bool(text and text.strip())


def is_within_length_limit(text: str | None) -> bool:
    return text is None or len(text) <= MAX_MESSAGE_LENGTH


def is_approval_waiting(reply: str | None) -> bool:
    """True when the reply says a confirmation popup is waiting."""
    return bool(reply) and APPROVAL_WAITING_PHRASE in reply
