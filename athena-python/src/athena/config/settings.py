"""ATHENA Python v1 — app paths and settings locations.

``APP_DATA_DIR`` (and the paths derived from it) point at the same
place the C# app uses — ``%APPDATA%\\Athena`` on Windows,
``~/.local/share/Athena`` elsewhere — so voice models, history and
settings do not move.
"""

import os
from pathlib import Path

if os.name == "nt":
    _BASE = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
else:
    _BASE = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share"))

#: Per-user ATHENA data dir: %APPDATA%\\Athena on Windows.
APP_DATA_DIR = _BASE / "Athena"

#: Where Vosk speech models live (one subdir per model).
VOSK_MODEL_DIR = APP_DATA_DIR / "models"

#: Append-only JSONL log of local tool runs.
HISTORY_PATH = APP_DATA_DIR / "history.log"

#: Voice preferences (voice.json), same file the C# app uses.
VOICE_SETTINGS_PATH = APP_DATA_DIR / "voice.json"

#: Local settings (config.json), same file the C# app uses:
#: DPAPI-protected site key / owner token + conversation id.
CONFIG_PATH = APP_DATA_DIR / "config.json"
