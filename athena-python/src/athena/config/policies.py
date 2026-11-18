"""ATHENA Python v1 — pure policies (no I/O, no audio, no SAPI).

Direct ports of the C# Core policies so the Python app and the
C# app enforce the same rules:

* :class:`WakeWordPolicy` — port of ``Athena.Core.WakeWordTrigger``
  (N7): exact word "athena", confidence >= 0.7, 3-second cooldown.
* :class:`ListeningControl` — port of ``Athena.Core.ListeningControl``
  (N8): the persisted on/off switch plus the temporary pause.

These are the single source of truth for wake-word gating. Do not
reimplement the thresholds anywhere else — import them here.
"""

from __future__ import annotations

import string
from datetime import datetime, timezone

__all__ = ["WakeWordPolicy", "ListeningControl", "GREETING_TEXT"]


#: The exact greeting spoken on a wake-word pop-up (N8). Local-only:
#: it never reaches the model, chat history, or memories.
GREETING_TEXT = "Welcome, boss."


class WakeWordPolicy:
    """Gate for the wake word "athena" (N7).

    A result triggers only when all three rules pass: the text is
    exactly the single word "athena" after normalisation (trimmed,
    case-insensitive, leading/trailing punctuation stripped —
    "Athena!" passes, "athena please" and "" fail), the confidence
    meets :attr:`MIN_CONFIDENCE`, and at least
    :attr:`COOLDOWN_SECONDS` have passed since the last accepted
    trigger. An accepted trigger resets the cooldown; a rejected
    one does not.
    """

    MIN_CONFIDENCE = 0.7
    COOLDOWN_SECONDS = 3.0
    WAKE_WORD = "athena"

    def __init__(self) -> None:
        self._last_accepted: datetime | None = None

    @property
    def last_accepted_at(self) -> datetime | None:
        """When the last trigger was accepted, if any."""
        return self._last_accepted

    def try_trigger(
        self,
        text: str | None,
        confidence: float,
        now: datetime | None = None,
    ) -> bool:
        """Return True only when the result passes all three rules."""
        if confidence is None or confidence != confidence:  # NaN
            return False
        if confidence < self.MIN_CONFIDENCE:
            return False
        if not self._is_wake_word(text):
            return False
        now = now or datetime.now(timezone.utc)
        if (
            self._last_accepted is not None
            and (now - self._last_accepted).total_seconds() < self.COOLDOWN_SECONDS
        ):
            return False
        self._last_accepted = now
        return True

    @classmethod
    def _is_wake_word(cls, text: str | None) -> bool:
        if not text or not text.strip():
            return False
        t = text.strip()
        start, end = 0, len(t)
        while start < end and (t[start].isspace() or t[start] in string.punctuation):
            start += 1
        while end > start and (t[end - 1].isspace() or t[end - 1] in string.punctuation):
            end -= 1
        word = t[start:end]
        return len(word) == len(cls.WAKE_WORD) and word.lower() == cls.WAKE_WORD


class ListeningControl:
    """N8 tray listening controls: persisted on/off + temporary pause.

    Two different kinds of "off":

    * Disabled (``enabled == False``) is the persisted setting
      (``VoiceSettings.WakeWordEnabled``, voice.json). It survives
      restarts and is the same value the Settings toggle writes.
    * Paused is temporary and in-memory only. It never touches the
      persisted setting, a fresh instance starts unpaused, and
      switching listening back on clears it ("listen now").

    The listener should run exactly when :attr:`is_listening` is true.
    """

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled
        self._paused = False

    @property
    def enabled(self) -> bool:
        """The persisted on/off switch. True by default."""
        return self._enabled

    @property
    def paused(self) -> bool:
        """Temporary pause: in-memory only, never persisted."""
        return self._paused

    @property
    def is_listening(self) -> bool:
        """True when the listener should actually be running."""
        return self._enabled and not self._paused

    def set_enabled(self, enabled: bool) -> None:
        """Set the persisted switch. Switching back on clears any pause."""
        self._enabled = enabled
        if enabled:
            self._paused = False

    def pause(self) -> None:
        """Pause temporarily. No-op when disabled."""
        if self._enabled:
            self._paused = True

    def resume(self) -> None:
        """Resume after a temporary pause. Never touches the setting."""
        self._paused = False

    def toggle_pause(self) -> None:
        """Flip the temporary pause. No-op when disabled."""
        if not self._enabled:
            return
        self._paused = not self._paused
