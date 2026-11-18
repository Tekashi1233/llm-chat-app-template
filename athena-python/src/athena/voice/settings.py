"""ATHENA Python v1 — voice preferences (port of C# N4 VoiceSettings).

Persisted as plain JSON in ``voice.json`` under ``APP_DATA_DIR``
(the same file the C# app uses — no secrets here). Output is OFF
by default, matching the website. Rate is the SAPI range -10..10
(0 = normal); Volume is 0..100.

Interop note: the C# app writes PascalCase keys (``OutputEnabled``,
``VoiceName``, ``Rate``, ``Volume``, ``MicEnabled``,
``RecognitionCulture``, ``WakeWordEnabled``, ``GreetOnWakeWord``).
:func:`load_settings` accepts both PascalCase and snake_case keys,
and :func:`save_settings` writes PascalCase so the file stays
readable by both apps. A missing, corrupt, or wrong-typed file
never raises — it yields :func:`VoiceSettings.defaults`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from ..config import VOICE_SETTINGS_PATH

__all__ = [
    "DEFAULT_VOICE_NAME",
    "VoiceSettings",
    "load_settings",
    "save_settings",
]

logger = logging.getLogger("athena.voice.settings")

#: Primary TTS voice: Edge TTS neural en-GB female. Female British
#: identity is fixed — do not change this to another voice without
#: the user's say-so. (Jarvis used en-CA-LiamNeural: MALE Canadian.
#: Only its Edge TTS *engine* approach was copied, never the voice.)
DEFAULT_VOICE_NAME = "en-GB-SoniaNeural"

#: SAPI offline fallback voice (C# VoiceSelection): Hazel first.
HAZEL_VOICE_NAME = "Microsoft Hazel Desktop"


def _clamp_rate(rate: object) -> int:
    try:
        return max(-10, min(10, int(rate)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _clamp_volume(volume: object) -> int:
    try:
        return max(0, min(100, int(volume)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 100


def _as_bool(value: object, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def _as_str(value: object, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


@dataclass
class VoiceSettings:
    """Voice preferences. Mirrors C# ``Athena.Core.VoiceSettings``.

    ``voice_name`` is the Edge TTS voice for online synthesis
    (default en-GB-SoniaNeural, female British). The SAPI offline
    fallback ignores it and prefers Hazel instead (see tts.py).
    """

    output_enabled: bool = False
    voice_name: str = DEFAULT_VOICE_NAME
    rate: int = 0
    volume: int = 100
    mic_enabled: bool = True
    recognition_culture: str = "en-GB"
    wake_word_enabled: bool = True
    greet_on_wake_word: bool = True

    def clamped(self) -> "VoiceSettings":
        """Return a copy with rate/volume in range and culture repaired."""
        return VoiceSettings(
            output_enabled=self.output_enabled,
            voice_name=self.voice_name,
            rate=_clamp_rate(self.rate),
            volume=_clamp_volume(self.volume),
            mic_enabled=self.mic_enabled,
            recognition_culture=self.recognition_culture.strip() or "en-GB",
            wake_word_enabled=self.wake_word_enabled,
            greet_on_wake_word=self.greet_on_wake_word,
        )

    def to_dict(self) -> dict:
        """PascalCase dict, matching the C# app's voice.json shape."""
        s = self.clamped()
        return {
            "OutputEnabled": s.output_enabled,
            "VoiceName": s.voice_name,
            "Rate": s.rate,
            "Volume": s.volume,
            "MicEnabled": s.mic_enabled,
            "RecognitionCulture": s.recognition_culture,
            "WakeWordEnabled": s.wake_word_enabled,
            "GreetOnWakeWord": s.greet_on_wake_word,
        }

    @classmethod
    def from_dict(cls, data: object) -> "VoiceSettings":
        """Build from a dict, accepting PascalCase or snake_case keys.

        Unknown keys are ignored; missing or wrong-typed values fall
        back to the defaults — never raises.
        """
        if not isinstance(data, dict):
            return cls()
        get = lambda *keys: next(
            (data[k] for k in keys if k in data), None
        )
        return cls(
            output_enabled=_as_bool(
                get("OutputEnabled", "output_enabled"), False
            ),
            voice_name=_as_str(
                get("VoiceName", "voice_name"), DEFAULT_VOICE_NAME
            ),
            rate=_clamp_rate(get("Rate", "rate")),
            volume=_clamp_volume(get("Volume", "volume")),
            mic_enabled=_as_bool(get("MicEnabled", "mic_enabled"), True),
            recognition_culture=_as_str(
                get("RecognitionCulture", "recognition_culture"), "en-GB"
            ),
            wake_word_enabled=_as_bool(
                get("WakeWordEnabled", "wake_word_enabled"), True
            ),
            greet_on_wake_word=_as_bool(
                get("GreetOnWakeWord", "greet_on_wake_word"), True
            ),
        ).clamped()

    @classmethod
    def defaults(cls) -> "VoiceSettings":
        """Factory for the defaults (output OFF, wake word ON)."""
        return cls()


def load_settings(path: str | Path = VOICE_SETTINGS_PATH) -> VoiceSettings:
    """Load voice.json. Missing/corrupt/wrong-typed file -> defaults.

    Never raises.
    """
    try:
        raw = Path(path).read_text(encoding="utf-8")
        return VoiceSettings.from_dict(json.loads(raw))
    except FileNotFoundError:
        return VoiceSettings.defaults()
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        logger.warning("voice settings unreadable (%s): %s", path, exc)
        return VoiceSettings.defaults()


def save_settings(
    settings: VoiceSettings, path: str | Path = VOICE_SETTINGS_PATH
) -> bool:
    """Save voice.json (PascalCase, C#-compatible). Never raises.

    Returns True on success, False if the file could not be written
    (voice preferences are a nicety; never break the app over them).
    Writes via a temp file + replace so a crash cannot leave a
    half-written voice.json behind.
    """
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(settings.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        tmp.replace(path)
        return True
    except OSError as exc:
        logger.warning("voice settings not saved (%s): %s", path, exc)
        return False
