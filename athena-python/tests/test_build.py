"""Build tests: the frozen exe must actually contain the voice stack.

Regression (2026-11-18, real laptop): a build made with
``--hidden-import vosk --hidden-import edge_tts`` shipped an exe with
ZERO vosk / edge_tts / sounddevice entries (verified with
``pyi-archive_viewer`` on the built binary) — the mic then reported
"the speech recognition engine could not start" even after the Vosk
model had downloaded. ``--hidden-import`` only pulls the top-level
module and silently drops the native libraries + data files; the
voice packages need ``--collect-all``.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD_BAT = ROOT / "build.bat"

# Packages the frozen exe must bundle whole: modules + data + binaries.
VOICE_PACKAGES = ("vosk", "edge_tts", "sounddevice")

# Windows DPAPI key-decryptor modules (top-level extension modules,
# plus the win32 package). Without these the app silently behaves as
# "no site key stored" and chat falls back to Ollama.
KEY_PACKAGES = ("win32crypt", "pywintypes")


def _build_bat_text() -> str:
    assert BUILD_BAT.is_file(), "build.bat is missing"
    return BUILD_BAT.read_text(encoding="utf-8")


def test_build_bat_collects_voice_packages_whole() -> None:
    text = _build_bat_text()
    for package in VOICE_PACKAGES:
        assert f"--collect-all {package}" in text, (
            f"build.bat must --collect-all {package}: "
            "--hidden-import silently drops its native libraries"
        )


def test_build_bat_has_no_bare_hidden_import_for_voice() -> None:
    text = _build_bat_text()
    for package in VOICE_PACKAGES:
        assert f"--hidden-import {package}" not in text, (
            f"build.bat must not use --hidden-import {package}"
        )


def test_build_bat_bundles_windows_key_decryptor() -> None:
    text = _build_bat_text()
    assert "--collect-all win32" in text
    for module in KEY_PACKAGES:
        assert f"--hidden-import {module}" in text, (
            f"build.bat must bundle {module}: without it the app cannot "
            "decrypt the site key and silently falls back to Ollama"
        )
