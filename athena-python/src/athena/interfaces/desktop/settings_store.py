"""ATHENA Python v1 — voice + app settings persistence (pure, no Qt).

Same files the C# app uses, so the two apps share preferences:

* ``voice.json`` — VoiceSettings (N4/N7/N8). Output OFF by default,
  Rate -10..10 (0 = normal), Volume 0..100, wake word ON by default,
  greeting ON by default. No secrets here.
* ``settings.json`` — AppSettings. Autostart ON by default (the user
  explicitly asked for autostart).

Load is forgiving: a missing file returns None (caller uses
defaults), a corrupt file returns None too — a broken JSON file
must never crash startup, matching the C# store behaviour.
Save is best-effort: failures are swallowed, because preferences
are a nicety. Unknown keys in the file are ignored; missing keys
fall back to the documented defaults (so an old voice.json without
``wake_word_enabled`` still reads as ON, as in C#).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ...config import APP_DATA_DIR

__all__ = [
    "VoiceSettings",
    "AppSettings",
    "load_voice_settings",
    "save_voice_settings",
    "load_app_settings",
    "save_app_settings",
    "VOICE_SETTINGS_FILENAME",
    "APP_SETTINGS_FILENAME",
]

VOICE_SETTINGS_FILENAME = "voice.json"
APP_SETTINGS_FILENAME = "settings.json"


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


@dataclass
class VoiceSettings:
    """Voice preferences. Field names are the snake_case form of the
    C# JSON names; load/save translate between the two so the file
    on disk stays byte-compatible with the C# app."""

    output_enabled: bool = False
    voice_name: str | None = None
    rate: int = 0
    volume: int = 100
    mic_enabled: bool = True
    recognition_culture: str = "en-GB"
    wake_word_enabled: bool = True
    greet_on_wake_word: bool = True

    def clamped(self) -> "VoiceSettings":
        """Rate/Volume into their SAPI ranges; repair a blank culture."""
        import copy

        fixed = copy.copy(self)
        fixed.rate = _clamp(self.rate, -10, 10)
        fixed.volume = _clamp(self.volume, 0, 100)
        fixed.recognition_culture = (
            self.recognition_culture.strip() or "en-GB"
            if isinstance(self.recognition_culture, str)
            else "en-GB"
        )
        return fixed

    def to_json_dict(self) -> dict[str, Any]:
        """C# property names, so the C# app reads our file unchanged."""
        return {
            "OutputEnabled": self.output_enabled,
            "VoiceName": self.voice_name,
            "Rate": self.rate,
            "Volume": self.volume,
            "MicEnabled": self.mic_enabled,
            "RecognitionCulture": self.recognition_culture,
            "WakeWordEnabled": self.wake_word_enabled,
            "GreetOnWakeWord": self.greet_on_wake_word,
        }

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "VoiceSettings":
        defaults = cls()
        return cls(
            output_enabled=bool(data.get("OutputEnabled", defaults.output_enabled)),
            voice_name=data.get("VoiceName", defaults.voice_name),
            rate=int(data.get("Rate", defaults.rate)),
            volume=int(data.get("Volume", defaults.volume)),
            mic_enabled=bool(data.get("MicEnabled", defaults.mic_enabled)),
            recognition_culture=str(
                data.get("RecognitionCulture", defaults.recognition_culture)
            ),
            wake_word_enabled=bool(
                data.get("WakeWordEnabled", defaults.wake_word_enabled)
            ),
            greet_on_wake_word=bool(
                data.get("GreetOnWakeWord", defaults.greet_on_wake_word)
            ),
        ).clamped()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AppSettings:
    """App-level preferences (not voice). Autostart lives here, NOT
    in voice.json, because it has nothing to do with voice."""

    autostart_enabled: bool = True

    def to_json_dict(self) -> dict[str, Any]:
        return {"AutostartEnabled": self.autostart_enabled}

    @classmethod
    def from_json_dict(cls, data: dict[str, Any]) -> "AppSettings":
        return cls(
            autostart_enabled=bool(
                data.get("AutostartEnabled", cls().autostart_enabled)
            )
        )


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_json(path: Path, data: dict[str, Any]) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def load_voice_settings(directory: str | Path | None = None) -> VoiceSettings | None:
    """Load voice.json; None when missing or corrupt (use defaults)."""
    directory = Path(directory) if directory is not None else APP_DATA_DIR
    data = _load_json(Path(directory) / VOICE_SETTINGS_FILENAME)
    if data is None:
        return None
    try:
        return VoiceSettings.from_json_dict(data)
    except Exception:
        return None


def save_voice_settings(
    settings: VoiceSettings, directory: str | Path | None = None
) -> bool:
    """Save voice.json (clamped first). Returns False on failure."""
    directory = Path(directory) if directory is not None else APP_DATA_DIR
    return _save_json(
        Path(directory) / VOICE_SETTINGS_FILENAME,
        settings.clamped().to_json_dict(),
    )


def load_app_settings(directory: str | Path | None = None) -> AppSettings | None:
    """Load settings.json; None when missing or corrupt (use defaults)."""
    directory = Path(directory) if directory is not None else APP_DATA_DIR
    data = _load_json(Path(directory) / APP_SETTINGS_FILENAME)
    if data is None:
        return None
    try:
        return AppSettings.from_json_dict(data)
    except Exception:
        return None


def save_app_settings(
    settings: AppSettings, directory: str | Path | None = None
) -> bool:
    """Save settings.json. Returns False on failure."""
    directory = Path(directory) if directory is not None else APP_DATA_DIR
    return _save_json(
        Path(directory) / APP_SETTINGS_FILENAME, settings.to_json_dict()
    )
