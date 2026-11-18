"""ATHENA Python v1 — Settings view.

Port of ``Views/SettingsView.xaml.cs`` (voice + startup sections):
the Voice card (output toggle, voice picker, speed/volume sliders,
wake-word toggle, greet toggle, privacy line) and the Startup card
(autostart toggle). Everything persists to ``voice.json`` /
``settings.json`` on change, in the same files the C# app uses.

Honest unavailable states: when no TTS voices / recogniser exist,
the controls are shown disabled with a plain explanation — the app
never pretends voice works. The wake-word and greet toggles are
shown (disabled) even when listening is unavailable, so the saved
choice stays visible.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...config import APP_DATA_DIR
from . import settings_store, theme
from .settings_store import AppSettings, VoiceSettings

__all__ = ["SettingsView", "PRIVACY_LINE", "WAKE_WORD_NOTE"]

#: Privacy, stated plainly next to the wake-word toggle — not
#: buried in a doc (C# SettingsView, N8).
PRIVACY_LINE = (
    "only wake-word detection runs in the background. It does not "
    "continuously record, does not store conversations, and does not "
    "transmit audio — local keyword spotting on this PC only."
)

WAKE_WORD_NOTE = (
    "Say 'athena' and the ATHENA window pops back up — it never sends "
    "a message on its own. When the greeting is on, ATHENA says "
    "'Welcome, boss.' as she wakes — spoken aloud even when read-aloud "
    "is off, or shown as text under the wordmark when speech isn't "
    "available. The greeting is never a chat message and is never saved. "
    "Keyword spotting can miss the word and can false-trigger on similar "
    "sounds; it needs a working microphone and the app running. All "
    "recognition is local and offline — no audio leaves this laptop — "
    "and it does not work while the laptop is asleep or the app is fully quit."
)


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    frame.setStyleSheet(theme.CARD_QSS)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 10, 14, 10)
    title_label = QLabel(title)
    title_label.setStyleSheet(f"color: {theme.GOLD}; font-weight: 600; font-size: 13px;")
    layout.addWidget(title_label)
    return frame, layout


def _body(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {theme.TEXT}; font-size: 13px;")
    return label


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
    return label


class SettingsView(QWidget):
    """Voice + Startup settings. All collaborators injected; the
    view never imports the voice engines or the registry itself.

    * ``tts`` — the TTS engine (EdgeTTS/SAPI) or None when voice
      output is unavailable. Must expose ``speak(text)`` and
      ``stop()`` when present.
    * ``available_voices`` — display names for the picker (empty
      when none were found).
    * ``recogniser_available`` — whether a speech recogniser + mic
      exist (drives the mic + wake-word availability).
    * ``autostart_store`` — IAutostartStore (Windows store on a
      real PC, fake in tests); None disables the toggle honestly.
    * ``on_wake_word_changed`` — called with the new bool so the
      shell can start/stop the listener live, no restart needed.
    """

    def __init__(
        self,
        voice_settings: VoiceSettings | None = None,
        app_settings: AppSettings | None = None,
        tts: Any | None = None,
        available_voices: list[str] | None = None,
        recogniser_available: bool = False,
        autostart_store: Any | None = None,
        on_wake_word_changed: Callable[[bool], None] | None = None,
        settings_dir: str | Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._voice = voice_settings or VoiceSettings()
        self._app = app_settings or AppSettings()
        self._tts = tts
        self._voices = list(available_voices or [])
        self._recogniser_available = recogniser_available
        self._autostart_store = autostart_store
        self._on_wake_word_changed = on_wake_word_changed
        self._dir = Path(settings_dir) if settings_dir is not None else APP_DATA_DIR

        scroller = QScrollArea(self)
        scroller.setWidgetResizable(True)
        scroller.setStyleSheet("QScrollArea { border: none; }")
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(8, 0, 8, 16)
        layout.setSpacing(12)

        layout.addWidget(self._build_voice_card())
        layout.addWidget(self._build_startup_card())
        layout.addWidget(self._build_about_card())
        layout.addStretch(1)

        scroller.setWidget(content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroller)

    # -- Voice card ---------------------------------------------------------

    def _build_voice_card(self) -> QFrame:
        card, body = _card("Voice")
        tts_available = self._tts is not None and bool(self._voices)

        if not tts_available:
            body.addWidget(
                _body(
                    "Voice output isn't available on this PC — no speech "
                    "synthesis voices were found. Chat still works; only "
                    "the spoken replies and microphone are missing."
                )
            )
        else:
            body.addWidget(_body("Voice"))
            self._voice_combo = QComboBox()
            self._voice_combo.addItems(self._voices)
            current = self._voice.voice_name
            if current and current in self._voices:
                self._voice_combo.setCurrentText(current)
            elif self._voices:
                self._voice_combo.setCurrentIndex(0)
            self._voice_combo.currentTextChanged.connect(self._on_voice_picked)
            body.addWidget(self._voice_combo)

            self._speed_label = _body(f"Speed: {self._voice.rate} (0 is normal)")
            body.addWidget(self._speed_label)
            speed = QSlider(Qt.Orientation.Horizontal)
            speed.setRange(-10, 10)
            speed.setValue(self._voice.rate)
            speed.setTickInterval(1)
            speed.valueChanged.connect(self._on_speed_changed)
            body.addWidget(speed)

            self._volume_label = _body(f"Volume: {self._voice.volume}")
            body.addWidget(self._volume_label)
            volume = QSlider(Qt.Orientation.Horizontal)
            volume.setRange(0, 100)
            volume.setValue(self._voice.volume)
            volume.setTickInterval(5)
            volume.valueChanged.connect(self._on_volume_changed)
            body.addWidget(volume)

            self._output_check = QCheckBox("Read replies aloud")
            self._output_check.setChecked(self._voice.output_enabled)
            self._output_check.toggled.connect(self._on_output_toggled)
            body.addWidget(self._output_check)

            test_button = QPushButton("Test voice")
            test_button.setStyleSheet(theme.GOLD_BUTTON_QSS)
            test_button.clicked.connect(
                lambda: self._speak("Hello, I am ATHENA, your personal assistant.")
            )
            body.addWidget(test_button)

        # Microphone input.
        body.addWidget(_body("Microphone"))
        if not self._recogniser_available:
            body.addWidget(
                _muted(
                    "Microphone input isn't available on this PC — no speech "
                    "recognizer or microphone was found."
                )
            )
        else:
            self._mic_check = QCheckBox("Microphone input enabled")
            self._mic_check.setChecked(self._voice.mic_enabled)
            self._mic_check.toggled.connect(self._on_mic_toggled)
            body.addWidget(self._mic_check)
            body.addWidget(
                _muted(
                    "Tap Mic in Chat to speak. Your words go into the "
                    "message box for you to check — nothing is sent "
                    "automatically, and the microphone is never on unless "
                    "you tap it."
                )
            )

        # Wake word (N7/N8).
        body.addWidget(_body("Wake word"))
        self._wake_check = QCheckBox("Wake up with 'athena'")
        self._wake_check.setChecked(self._voice.wake_word_enabled)
        self._wake_check.setEnabled(self._recogniser_available)
        self._wake_check.toggled.connect(self._on_wake_word_toggled)
        body.addWidget(self._wake_check)

        self._greet_check = QCheckBox("Greet me when I wake you")
        self._greet_check.setChecked(self._voice.greet_on_wake_word)
        self._greet_check.setEnabled(self._recogniser_available)
        self._greet_check.toggled.connect(self._on_greet_toggled)
        body.addWidget(self._greet_check)

        if not self._recogniser_available:
            body.addWidget(
                _muted(
                    "Wake-word listening isn't available on this PC — no "
                    "speech recognizer or microphone was found, so saying "
                    "'athena' can't bring the window back."
                )
            )

        body.addWidget(_body("Privacy: " + PRIVACY_LINE))
        body.addWidget(_muted(WAKE_WORD_NOTE))
        return card

    # -- Startup card -------------------------------------------------------

    def _build_startup_card(self) -> QFrame:
        card, body = _card("Startup")
        self._autostart_check = QCheckBox("Start ATHENA when I log in")
        self._autostart_check.setChecked(self._app.autostart_enabled)
        self._autostart_check.setEnabled(self._autostart_store is not None)
        self._autostart_check.toggled.connect(self._on_autostart_toggled)
        body.addWidget(self._autostart_check)
        body.addWidget(
            _muted(
                "ATHENA starts hidden in the tray when you log in, "
                "listening for 'athena' when the wake word is on — no "
                "need to launch it yourself. Quitting from the tray menu "
                "only closes this session; ATHENA will still start at "
                "your next login. Turn this off to stop that."
            )
        )
        if self._autostart_store is None:
            body.addWidget(
                _muted(
                    "Startup setting isn't available here — the Windows "
                    "registry store is missing, so this toggle is off."
                )
            )
        self._autostart_status = _muted("")
        body.addWidget(self._autostart_status)
        return card

    def _build_about_card(self) -> QFrame:
        card, body = _card("About")
        body.addWidget(
            _body(
                "ATHENA — minimal black identity + voice. Native Windows "
                "app, Python + PySide6. Backend: Cloudflare Worker + "
                "GPT-OSS-20B + D1."
            )
        )
        return card

    # -- slots ---------------------------------------------------------------

    def _save_voice(self) -> None:
        settings_store.save_voice_settings(self._voice, self._dir)
        apply = getattr(self._tts, "apply_settings", None)
        if callable(apply):
            try:
                apply(self._voice)
            except Exception:
                pass

    def _on_voice_picked(self, name: str) -> None:
        self._voice.voice_name = name or None
        self._save_voice()

    def _on_speed_changed(self, value: int) -> None:
        self._voice.rate = value
        self._speed_label.setText(f"Speed: {value} (0 is normal)")
        self._save_voice()

    def _on_volume_changed(self, value: int) -> None:
        self._voice.volume = value
        self._volume_label.setText(f"Volume: {value}")
        self._save_voice()

    def _on_output_toggled(self, on: bool) -> None:
        self._voice.output_enabled = on
        self._save_voice()
        if not on and self._tts is not None:
            stop = getattr(self._tts, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception:
                    pass

    def _on_mic_toggled(self, on: bool) -> None:
        self._voice.mic_enabled = on
        self._save_voice()

    def _on_wake_word_toggled(self, on: bool) -> None:
        self._voice.wake_word_enabled = on
        self._save_voice()
        if self._on_wake_word_changed is not None:
            self._on_wake_word_changed(on)

    def _on_greet_toggled(self, on: bool) -> None:
        self._voice.greet_on_wake_word = on
        self._save_voice()

    def _on_autostart_toggled(self, on: bool) -> None:
        from .autostart import AutostartPolicy

        self._app.autostart_enabled = on
        settings_store.save_app_settings(self._app, self._dir)
        self._autostart_status.setText("")
        if self._autostart_store is None:
            return
        try:
            import os
            import sys

            exe_path = sys.executable  # PyInstaller build: sys.executable is Athena.exe
            if on:
                AutostartPolicy.ensure_enabled(self._autostart_store, exe_path)
            else:
                AutostartPolicy.ensure_disabled(self._autostart_store)
        except Exception as exc:
            self._autostart_status.setText(
                "Couldn't update the Windows startup setting — it will try "
                f"again next launch. {exc}"
            )

    def _speak(self, text: str) -> None:
        if self._tts is None:
            return
        speak = getattr(self._tts, "speak", None)
        if callable(speak):
            try:
                speak(text)
            except Exception:
                pass
