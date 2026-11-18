"""ATHENA Python v1 — wake greeting (port of C# N8 GreetingPolicy).

The greeting ("Welcome, boss.") is spoken ONLY when the window was
popped up by the wake word and the user left the greeting on. A
normal launch (including autostart at login) or a tray restore
never greets, whatever the setting says.

This module never triggers itself: the CALLER decides when the
window was shown by a wake word and calls :func:`greet` /
:func:`greet_if_wake_word`. The greeting is local-only UI/speech —
it never reaches the model, the conversation history, or memories.

Returns whether speech succeeded so the UI can show the text
fallback under the wordmark when TTS is unavailable.
"""

from __future__ import annotations

import enum
import logging
from typing import Protocol

from ..config.policies import GREETING_TEXT
from .settings import VoiceSettings

__all__ = [
    "GREETING_TEXT",
    "ShowReason",
    "greet",
    "greet_if_wake_word",
    "should_greet",
]

logger = logging.getLogger("athena.voice.greeting")


class ShowReason(enum.Enum):
    """Why the window is being shown — the greeting keys off this."""

    LAUNCH = "launch"  # normal launch, including autostart at login
    TRAY_RESTORE = "tray_restore"  # tray menu / tray double-click
    WAKE_WORD = "wake_word"  # popped up because "athena" was heard


class _Speaker(Protocol):
    def speak(self, text: str) -> bool: ...


def should_greet(reason: ShowReason, greet_on_wake_word: bool) -> bool:
    """True only for a wake-word pop-up with the greeting switched on."""
    return bool(greet_on_wake_word) and reason is ShowReason.WAKE_WORD


def greet(tts: _Speaker | None, settings: VoiceSettings | None = None) -> bool:
    """Speak the wake greeting. Returns True when speech succeeded.

    Returns False (never raises) when the greeting is switched off
    in settings, when no TTS engine is given, or when speech fails —
    the caller shows ``GREETING_TEXT`` as text under the wordmark.
    This function does NOT decide *when* to greet; the caller must
    only call it for a wake-word pop-up (see :func:`greet_if_wake_word`).
    """
    if settings is not None and not settings.greet_on_wake_word:
        return False
    if tts is None:
        return False
    try:
        return bool(tts.speak(GREETING_TEXT))
    except Exception as exc:
        logger.warning("Wake greeting failed: %s", exc)
        return False


def greet_if_wake_word(
    tts: _Speaker | None,
    reason: ShowReason,
    settings: VoiceSettings | None = None,
) -> bool:
    """Greet only when ``reason`` is a wake-word pop-up (and enabled).

    Convenience wrapper combining :func:`should_greet` and
    :func:`greet`: launch and tray-restore reasons return False
    without speaking. Returns True only when speech succeeded.
    """
    enabled = True if settings is None else settings.greet_on_wake_word
    if not should_greet(reason, enabled):
        return False
    return greet(tts, settings)
