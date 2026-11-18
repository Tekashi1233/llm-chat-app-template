"""ATHENA Python v1 — config package (paths, policies, secure store).

Re-exported here so ``athena.config`` keeps working exactly as
before the restructure: ``APP_DATA_DIR`` and friends live in
:mod:`athena.config.settings`, the pure policies in
:mod:`athena.config.policies`, the DPAPI-backed credential store
in :mod:`athena.config.secure_store`.
"""

from __future__ import annotations

from .policies import GREETING_TEXT, ListeningControl, WakeWordPolicy
from .secure_store import NotConfiguredError, SecureStore, SecureStoreError
from .settings import (
    APP_DATA_DIR,
    CONFIG_PATH,
    HISTORY_PATH,
    VOICE_SETTINGS_PATH,
    VOSK_MODEL_DIR,
)

__all__ = [
    "APP_DATA_DIR",
    "CONFIG_PATH",
    "GREETING_TEXT",
    "HISTORY_PATH",
    "ListeningControl",
    "NotConfiguredError",
    "SecureStore",
    "SecureStoreError",
    "VOICE_SETTINGS_PATH",
    "VOSK_MODEL_DIR",
    "WakeWordPolicy",
]
