"""ATHENA Python v1 — main window (the shell) + ``build_app()``.

Port of ``MainWindow.xaml(.cs)``: minimal black identity — pure
black background, ATHENA as an arc reactor in the centre with the
wordmark beneath it, frosted-glass title bar and chat surfaces,
and the six capability sections behind the "···" menu overlay.
No dashboard, no sidebar, no floating Island.

N7/N8 behaviour carried over:

* Closing the window hides it to the tray instead of quitting;
  real quit is only from the tray menu.
* The wake word ("athena") pops the window up and focuses the
  input; it never sends anything.
* The tray menu and the Settings toggle are two handles on the
  same persisted listening switch (``ListeningControl``) and can
  never disagree; pausing is temporary and never persisted.
* ``--tray`` starts hidden with the tray icon live. Whether the
  wake-word listener actually starts follows the persisted voice
  setting, not ``--tray``.
* The greeting "Welcome, boss." is spoken ONLY on a wake-word
  pop-up (never on tray restore, normal launch or login), through
  the TTS engine even when reply voice output is OFF; when speech
  is unavailable it shows as text under the wordmark for 5 seconds.
  It never reaches the model, chat history or memories.
* Autostart is reconciled at launch (stale entries cleaned, then
  registered/removed per the setting), best-effort — a registry
  failure never breaks startup.

The reactor lighting follows the presence machine: only states
real events produce ever light up. All glow changes are slow
fades — no flashing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...config import APP_DATA_DIR
from ...config.policies import GREETING_TEXT, ListeningControl
from . import settings_store, theme
from .autostart import AutostartPolicy, WindowsAutostartStore, parse_startup_options
from .chat_view import ChatView, resolve_send
from .reactor import ArcReactor
from .settings_store import AppSettings, VoiceSettings
from .settings_view import SettingsView
from .tray import AthenaTrayIcon
from .views import (
    ActivityPanel,
    ConversationsPanel,
    DevicesPanel,
    MemoryVaultPanel,
    ToolsPanel,
)

__all__ = ["MainWindow", "PresenceMachine", "build_app"]

SECTIONS = ("Conversations", "Memory Vault", "Tools", "Devices", "Activity", "Settings")

#: Presence transitions (C# PresenceStateMachine): which source may
#: produce which state. Offline may come from anyone; anything else
#: returns False and leaves the state untouched.
_ALLOWED: dict[str, set[str]] = {
    "idle": {"chat", "voice", "device", "system"},  # except Thinking->Idle needs Chat (checked below)
    "thinking": {"chat"},
    "listening": {"voice"},
    "speaking": {"voice"},
    "working": {"device"},
    "approval": {"device"},
    "offline": {"chat", "voice", "device", "system"},
    "error": {"chat", "system"},
}


class PresenceMachine:
    """Pure presence state machine (C# PresenceStateMachine port).

    * Idle: from anything except Thinking (only Chat completes a
      Thinking turn; System must not clear it mid-request).
    * Thinking: only from Chat. Listening/Speaking: only Voice.
      Working/Approval: only from Device. Offline: anyone.
      Error: Chat or System.
    """

    def __init__(self) -> None:
        self.state = "idle"
        self.detail = "Idle"

    def try_set(self, next_state: str, source: str, detail: str) -> bool:
        next_state = next_state.lower()
        source = source.lower()
        if next_state == self.state:
            self.detail = detail
            return True
        if next_state not in _ALLOWED:
            return False
        if next_state == "idle" and self.state == "thinking" and source != "chat":
            return False
        if source not in _ALLOWED[next_state]:
            return False
        self.state = next_state
        self.detail = detail
        return True


class MainWindow(QWidget):
    """The black shell. All collaborators are injected; the window
    never imports the Worker client, voice engines or tool registry
    itself — ``build_app()`` passes them in."""

    def __init__(
        self,
        worker: Any | None = None,
        tts: Any | None = None,
        wake: Any | None = None,
        tools: Any | None = None,
        memory: Any | None = None,
        dictation: Any | None = None,
        start_hidden: bool = False,
        settings_dir: str | Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker = worker
        self._tts = tts
        self._wake = wake
        self._tools = tools
        self._memory = memory  # local lessons store; D1 (via worker) stays memory of record
        self._dictation = dictation
        self._start_hidden = start_hidden
        self._dir = Path(settings_dir) if settings_dir is not None else APP_DATA_DIR
        self._really_quitting = False
        self._conversing = False
        self._connection_status = "Starting…"
        self._presence = PresenceMachine()
        self._listening = ListeningControl()
        self._initialised = False
        self._drag_pos: Any = None
        self._greeting_timer: QTimer | None = None

        # --- settings (same files the C# app uses) ----------------------
        self.voice_settings: VoiceSettings = (
            settings_store.load_voice_settings(self._dir) or VoiceSettings()
        )
        self.app_settings: AppSettings = (
            settings_store.load_app_settings(self._dir) or AppSettings()
        )
        self._listening.set_enabled(self.voice_settings.wake_word_enabled)

        self.setWindowTitle("ATHENA")
        self.setMinimumSize(900, 560)
        self.resize(1100, 700)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setStyleSheet(f"background: {theme.BACKGROUND}; color: {theme.TEXT};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_title_bar())

        # --- Wordmark zone: reactor + wordmark + status ------------------
        self._wordmark_zone = QWidget()
        self._wordmark_zone.setStyleSheet(f"background: {theme.BACKGROUND};")
        zone_layout = QVBoxLayout(self._wordmark_zone)
        zone_layout.setContentsMargins(0, 0, 0, 0)
        zone_layout.setSpacing(0)
        zone_layout.addStretch(1)

        self._reactor = ArcReactor(260)
        self._reactor.mousePressEvent = lambda _e: self._return_to_idle()
        reactor_row = QHBoxLayout()
        reactor_row.addStretch(1)
        reactor_row.addWidget(self._reactor)
        reactor_row.addStretch(1)
        zone_layout.addLayout(reactor_row)

        self._wordmark = QLabel("A T H E N A")
        self._wordmark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._wordmark.setFont(self._wordmark_font(theme.WORDMARK_IDLE_PT))
        self._wordmark.setStyleSheet(f"color: {theme.TEXT};")
        self._wordmark.setToolTip("ATHENA")
        self._wordmark.mousePressEvent = lambda _e: self._return_to_idle()
        zone_layout.addWidget(self._wordmark)

        self._status_label = QLabel(self._connection_status)
        self._status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._status_label.setStyleSheet(f"color: {theme.MUTED}; font-size: 11px;")
        zone_layout.addWidget(self._status_label)

        # N8: wake greeting as text, shown only when the wake word
        # fires and speech is unavailable. Hidden otherwise.
        self._greeting_label = QLabel(GREETING_TEXT)
        self._greeting_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._greeting_label.setStyleSheet(f"color: {theme.TEXT}; font-size: 13px;")
        self._greeting_label.hide()
        zone_layout.addWidget(self._greeting_label)

        zone_layout.addStretch(1)
        root.addWidget(self._wordmark_zone, 1)

        # --- Menu button (top right of the wordmark zone) ---------------
        self._menu_button = QPushButton("···")
        self._menu_button.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {theme.MUTED}; "
            f"font-size: 20px; border: none; padding: 12px 16px; }}"
        )
        self._menu_button.setToolTip(
            "Menu — conversations, memory, tools, devices, activity, settings"
        )
        self._menu_button.clicked.connect(self._show_menu)
        self._menu_button.setParent(self._wordmark_zone)
        self._menu_button.move(self.width() - 70, 8)
        self._menu_button.raise_()

        # --- Conversation: the single ChatView, kept alive --------------
        # The ChatView is ALWAYS visible: its glass input bar is the
        # only place to type, and hiding the whole view at idle hid
        # the input with it (v1 bug — the first message could never
        # be typed). Only the message FEED hides at idle; the input
        # bar stays at the bottom. The root stretch for the ChatView
        # is 0 at idle (bar height only) and 1 in conversation (the
        # feed takes its share of the window).
        self.chat_view = ChatView(send_fn=resolve_send(worker), tts=tts)
        self.chat_view.set_dictation(dictation)
        self.chat_view.messages_changed.connect(self._update_layout_from_messages)
        self.chat_view.reply_received.connect(self._reactor.flash)
        self.chat_view.presence_requested.connect(
            lambda state, detail: self.set_presence(state, "chat", detail)
        )
        self.chat_view.dictation_started.connect(self._pause_wake_for_dictation)
        self.chat_view.dictation_stopped.connect(self._resume_wake_after_dictation)
        root.addWidget(self.chat_view, 1)
        self._root_layout = root
        self._chat_index = root.indexOf(self.chat_view)
        self._apply_idle_geometry()

        # --- Overlay: menu + the six sections ---------------------------
        self._overlay = QWidget(self)
        self._overlay.setStyleSheet("background: rgba(0, 0, 0, 0.8);")
        self._overlay.hide()
        overlay_layout = QHBoxLayout(self._overlay)
        overlay_layout.setContentsMargins(0, 0, 0, 0)
        overlay_layout.setSpacing(0)
        overlay_layout.addStretch(1)
        self._overlay_panel = QFrame()
        self._overlay_panel.setObjectName("GlassPanel")
        self._overlay_panel.setStyleSheet(
            theme.GLASS_PANEL_QSS
            + f"QFrame#GlassPanel {{ border: 1px solid {theme.GOLD}; border-radius: 0px; }}"
        )
        self._overlay_panel.setFixedWidth(420)
        panel_layout = QVBoxLayout(self._overlay_panel)
        panel_layout.setContentsMargins(16, 12, 16, 16)
        header = QHBoxLayout()
        self._overlay_back = QPushButton("‹ Menu")
        self._overlay_back.setStyleSheet(theme.GHOST_BUTTON_QSS)
        self._overlay_back.clicked.connect(self._show_menu)
        self._overlay_back.hide()
        header.addWidget(self._overlay_back)
        self._overlay_title = QLabel("Menu")
        self._overlay_title.setStyleSheet(
            f"color: {theme.GOLD}; font-weight: bold; font-size: 18px;"
        )
        header.addWidget(self._overlay_title, 1)
        close_button = QPushButton("✕")
        close_button.setStyleSheet(theme.GHOST_BUTTON_QSS)
        close_button.clicked.connect(self._close_overlay)
        header.addWidget(close_button)
        panel_layout.addLayout(header)
        self._overlay_content = QVBoxLayout()
        panel_layout.addLayout(self._overlay_content, 1)
        overlay_layout.addWidget(self._overlay_panel)

        # --- Tray ----------------------------------------------------------
        self._tray: AthenaTrayIcon | None = None
        self._ensure_tray()

        # --- Wake word ------------------------------------------------------
        self._wire_wake_word()

        if start_hidden:
            self.hide()
            if self._tray is not None:
                self._tray.show_startup_balloon()
        self._initialise_once()

    # -- construction helpers -------------------------------------------------

    def _wordmark_font(self, point_size: int):
        from PySide6.QtGui import QFont

        font = QFont(theme.WORDMARK_FONT, point_size)
        font.setBold(True)
        return font

    def _build_title_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("GlassPanel")
        bar.setStyleSheet(
            theme.GLASS_PANEL_QSS
            + "QFrame#GlassPanel { border-radius: 0px; border-left: none; "
            "border-right: none; border-top: none; }"
        )
        bar.setFixedHeight(34)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 0, 0, 0)
        layout.setSpacing(0)
        title = QLabel("A T H E N A")
        title.setFont(self._wordmark_font(theme.TITLE_FONT_PT))
        title.setStyleSheet(f"color: {theme.TEXT};")
        layout.addWidget(title)
        layout.addStretch(1)
        for label, tooltip, slot, name in (
            ("—", "Minimize", self.showMinimized, ""),
            ("▢", "Maximize", self._toggle_maximise, ""),
            ("✕", "Close", self.close, "CloseButton"),
        ):
            button = QPushButton(label)
            button.setObjectName(name)
            button.setStyleSheet(theme.CAPTION_BUTTON_QSS)
            button.setToolTip(tooltip)
            button.clicked.connect(slot)
            layout.addWidget(button)
        # Drag + double-click maximise on the bar itself.
        bar.mousePressEvent = self._title_mouse_press
        bar.mouseMoveEvent = self._title_mouse_move
        bar.mouseDoubleClickEvent = lambda _e: self._toggle_maximise()
        return bar

    # -- title bar behaviour ---------------------------------------------------

    def _title_mouse_press(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def _title_mouse_move(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
            event.accept()

    def _toggle_maximise(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt naming
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        super().resizeEvent(event)
        self._overlay.setGeometry(0, 0, self.width(), self.height())
        if hasattr(self, "_menu_button"):
            self._menu_button.move(self.width() - 70, 8)

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if event.key() == Qt.Key.Key_Escape and self._overlay.isVisible():
            self._close_overlay()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        # N7: closing hides to the tray instead of quitting. Real
        # quit is only from the tray menu. Minimising stays
        # minimising — only Close hides to tray.
        if not self._really_quitting:
            self._ensure_tray()
            if self._tray is not None:
                event.ignore()
                self.hide()
                self._tray.show_first_hide_balloon()
                return
            self._really_quitting = True
        self._shutdown()
        event.accept()

    # -- presence / status -------------------------------------------------------

    def set_presence(self, state: str, source: str, detail: str) -> None:
        if not self._presence.try_set(state, source, detail):
            return
        self._apply_presence()

    def _apply_presence(self) -> None:
        status = self._presence
        if status.state == "idle":
            self._status_label.setText(self._connection_status)
        else:
            self._status_label.setText(status.detail)
        self._reactor.set_presence(status.state)

    def set_status(self, text: str) -> None:
        """Discreet status line under the wordmark. Remembered for
        when the presence returns to Idle."""
        self._connection_status = text
        if self._presence.state == "idle":
            self._status_label.setText(text)

    # -- layout: idle wordmark vs conversation ------------------------------------

    def _update_layout_from_messages(self) -> None:
        if self.chat_view.message_count > 0:
            self._set_conversation_layout()
        else:
            self._set_idle_layout()

    def _apply_idle_geometry(self) -> None:
        """Idle: feed hidden, input bar only. The ChatView takes
        its minimum height (the bar) at the bottom of the window;
        the wordmark zone keeps the rest."""
        self.chat_view.set_feed_visible(False)
        if getattr(self, "_root_layout", None) is not None:
            self._root_layout.setStretch(self._chat_index, 0)

    def _apply_conversation_geometry(self) -> None:
        """Conversation: feed shown, ChatView shares the window
        with the (now small) wordmark zone, as in v1."""
        self.chat_view.set_feed_visible(True)
        if getattr(self, "_root_layout", None) is not None:
            self._root_layout.setStretch(self._chat_index, 1)

    def _set_idle_layout(self) -> None:
        if not self._conversing:
            return
        self._conversing = False
        self._apply_idle_geometry()
        self._reactor.set_size(260)
        self._wordmark.setFont(self._wordmark_font(theme.WORDMARK_IDLE_PT))
        self._wordmark.setToolTip("ATHENA")
        self._wordmark.setCursor(Qt.CursorShape.ArrowCursor)

    def _set_conversation_layout(self) -> None:
        if self._conversing:
            return
        self._conversing = True
        self._reactor.set_size(84)
        self._wordmark.setFont(self._wordmark_font(theme.WORDMARK_CHAT_PT))
        self._wordmark.setToolTip("Click to return to the idle screen")
        self._wordmark.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_conversation_geometry()
        self.chat_view.focus_input()

    def _return_to_idle(self) -> None:
        if not self._conversing:
            return
        # New conversation: clear the feed and go back to idle.
        while self.chat_view._feed_layout.count() > 1:
            item = self.chat_view._feed_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._set_idle_layout()
        self.chat_view.focus_input()

    # -- overlay menu + sections ----------------------------------------------------

    def _show_menu(self) -> None:
        self._overlay_title.setText("Menu")
        self._overlay_back.hide()
        self._clear_overlay_content()
        for section in SECTIONS:
            button = QPushButton(section)
            button.setStyleSheet(theme.GHOST_BUTTON_QSS)
            button.setFont(self._wordmark_font(15))
            button.clicked.connect(
                lambda _c, name=section: self._show_section(name)
            )
            self._overlay_content.addWidget(button)
        self._overlay_content.addStretch(1)
        self._overlay.setGeometry(0, 0, self.width(), self.height())
        self._overlay.show()
        self._overlay.raise_()

    def _show_section(self, section: str) -> None:
        self._overlay_title.setText(section)
        self._overlay_back.show()
        self._clear_overlay_content()
        view: QWidget
        if section == "Conversations":
            view = ConversationsPanel(
                worker=self._worker,
                on_open=lambda _cid: self._close_overlay(),
            )
        elif section == "Memory Vault":
            view = MemoryVaultPanel(worker=self._worker)
        elif section == "Tools":
            view = ToolsPanel(worker=self._worker, tools=self._tools)
        elif section == "Devices":
            view = DevicesPanel(
                worker=self._worker,
                send_device_command=self._send_device_command,
            )
        elif section == "Activity":
            view = ActivityPanel(worker=self._worker)
        elif section == "Settings":
            view = self._build_settings_view()
        else:  # pragma: no cover - defensive
            view = ToolsPanel(worker=self._worker, tools=self._tools)
        self._overlay_content.addWidget(view, 1)

    def _build_settings_view(self) -> SettingsView:
        tts = self._tts
        voices: list[str] = []
        list_voices = getattr(tts, "list_voices", None) if tts is not None else None
        if callable(list_voices):
            try:
                voices = [str(v) for v in list_voices()]
            except Exception:
                voices = []
        recogniser_available = False
        if self._wake is not None:
            recogniser_available = bool(
                getattr(self._wake, "is_available", True)
            )
        store = None
        if os.name == "nt":
            try:
                store = WindowsAutostartStore()
            except Exception:
                store = None
        return SettingsView(
            voice_settings=self.voice_settings,
            app_settings=self.app_settings,
            tts=tts,
            available_voices=voices,
            recogniser_available=recogniser_available,
            autostart_store=store,
            on_wake_word_changed=self._on_wake_word_setting_changed,
            settings_dir=self._dir,
        )

    def _clear_overlay_content(self) -> None:
        while self._overlay_content.count():
            item = self._overlay_content.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _close_overlay(self) -> None:
        self._overlay.hide()

    def _send_device_command(self, prompt: str) -> None:
        """A device command tapped in Devices: close the overlay so
        the user sees the chat, then send it through chat."""
        self._close_overlay()
        self.chat_view.send_device_command(prompt)

    # -- tray ------------------------------------------------------------------------

    def _ensure_tray(self) -> None:
        if self._tray is not None:
            return
        try:
            self._tray = AthenaTrayIcon(
                listening=self._listening,
                on_show=lambda: self.show_window("tray"),
                on_toggle_listening=self._toggle_listening_from_tray,
                on_toggle_pause=self._toggle_pause_from_tray,
                on_quit=self._quit_from_tray,
                parent=self,
            )
            self._tray.show()
        except Exception:
            # No tray icon available: closing falls through to a
            # real quit (see closeEvent).
            self._tray = None

    def _toggle_listening_from_tray(self) -> None:
        """Tray "Listening: On/Off" — flips the persisted switch
        (same setting as Settings)."""
        on = not self._listening.enabled
        self.voice_settings.wake_word_enabled = on
        settings_store.save_voice_settings(self.voice_settings, self._dir)
        self._on_wake_word_setting_changed(on)
        if self._tray is not None:
            self._tray.refresh_labels()

    def _toggle_pause_from_tray(self) -> None:
        """Tray "Pause/Resume listening" — temporary pause only;
        never persists, never touches the setting."""
        self._listening.toggle_pause()
        if self._listening.is_listening:
            self._start_wake_word()
        else:
            self._stop_wake_word()
        if self._tray is not None:
            self._tray.refresh_labels()

    def _quit_from_tray(self) -> None:
        self._really_quitting = True
        self.close()

    def _shutdown(self) -> None:
        try:
            stop = getattr(self._tts, "stop", None)
            if callable(stop):
                stop()
        except Exception:
            pass
        try:
            stop_wake = getattr(self._wake, "stop", None)
            if callable(stop_wake):
                stop_wake()
        except Exception:
            pass
        if self._tray is not None:
            self._tray.hide()
            self._tray = None

    # -- wake word + greeting -----------------------------------------------------------

    def _wire_wake_word(self) -> None:
        """Connect the wake-word listener's signal, if it has one.
        The wake module owns its own signal name; this tries the
        two shapes the voice builder may expose: a ``wake_word_heard``
        Qt signal, or a ``set_callback(fn)`` registration."""
        if self._wake is None:
            return
        signal = getattr(self._wake, "wake_word_heard", None)
        if signal is not None and hasattr(signal, "connect"):
            try:
                signal.connect(lambda: self.show_window("wake_word"))
                return
            except Exception:
                pass
        register = getattr(self._wake, "set_callback", None)
        if callable(register):
            try:
                register(lambda: self.show_window("wake_word"))
            except Exception:
                pass

    def _start_wake_word(self) -> None:
        if self._wake is None or not self._listening.is_listening:
            return
        start = getattr(self._wake, "start", None)
        if callable(start):
            try:
                if start() is False:
                    # One retry a second later if the mic is still
                    # busy winding down from dictation.
                    QTimer.singleShot(1000, self._start_wake_word)
            except Exception:
                pass

    def _stop_wake_word(self) -> None:
        if self._wake is None:
            return
        stop = getattr(self._wake, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception:
                pass

    def _pause_wake_for_dictation(self) -> None:
        """Dictation started: pause the wake-word listener so the
        user's own dictation cannot re-trigger it (same as C# N7).
        Pause is temporary and in-memory only — it never touches
        the persisted listening setting."""
        if self._wake is None:
            return
        pause = getattr(self._wake, "pause", None)
        if callable(pause):
            try:
                pause()
            except Exception:
                pass

    def _resume_wake_after_dictation(self) -> None:
        """Dictation stopped: resume the listener, but only when
        listening is still switched on (a tray/Settings off during
        dictation stays off)."""
        if self._wake is None:
            return
        resume = getattr(self._wake, "resume", None)
        if callable(resume):
            try:
                resume()
            except Exception:
                pass
        self._start_wake_word()

    def _on_wake_word_setting_changed(self, _on: bool) -> None:
        """The Settings toggle flipped — start or stop the listener
        live, no restart needed."""
        self._listening.set_enabled(self.voice_settings.wake_word_enabled)
        if self._listening.is_listening:
            self._start_wake_word()
        else:
            self._stop_wake_word()
        if self._tray is not None:
            self._tray.refresh_labels()

    def show_window(self, reason: str) -> None:
        """Show + focus. ``reason`` is "wake_word", "tray" or
        "launch". Only the wake-word path greets."""
        if not self.isVisible():
            self.show()
        if self.isMinimized():
            self.showNormal()
        self.activateWindow()
        self.raise_()
        if reason == "wake_word":
            self.set_presence("listening", "voice", "Listening…")
            self._greet_if_enabled()
        self.chat_view.focus_input()

    def _greet_if_enabled(self) -> None:
        """Greet on a wake-word pop-up only. Spoken through the TTS
        engine even when reply voice output is OFF (it is a
        greeting, not a reply); shown as text when speech is
        unavailable. Local-only: never a chat message, never saved."""
        if not self.voice_settings.greet_on_wake_word:
            return
        spoken = False
        if self._tts is not None:
            speak = getattr(self._tts, "speak", None)
            if callable(speak):
                try:
                    spoken = bool(speak(GREETING_TEXT))
                except Exception:
                    spoken = False
        if not spoken:
            self._greeting_label.show()
            if self._greeting_timer is not None:
                self._greeting_timer.stop()
            self._greeting_timer = QTimer(self)
            self._greeting_timer.setSingleShot(True)
            self._greeting_timer.setInterval(5000)
            self._greeting_timer.timeout.connect(self._greeting_label.hide)
            self._greeting_timer.start()

    # -- startup -------------------------------------------------------------------------

    def _initialise_once(self) -> None:
        if self._initialised:
            return
        self._initialised = True
        self._apply_autostart_setting()
        # Voice controls reflect what is actually available.
        tts_available = self._tts is not None
        # The mic button is the dictation button: its recogniser is
        # the dictation engine, not the wake-word listener. A wired
        # engine with a missing model still enables it — tapping
        # the mic starts the one-time model download.
        recogniser_available = self._dictation is not None
        self.chat_view.refresh_voice_controls(
            tts_available=tts_available,
            recogniser_available=recogniser_available,
            mic_enabled=self.voice_settings.mic_enabled,
        )
        self.chat_view.set_voice_output_enabled(
            self.voice_settings.output_enabled
        )
        if self._listening.is_listening:
            self._start_wake_word()
        if self._worker is None:
            self.set_status("Not connected — Worker client not wired up yet")
        else:
            self.set_status("Ready")

    def _apply_autostart_setting(self) -> None:
        """Reconcile the Run key with the autostart setting. Best
        effort — a registry failure never breaks startup."""
        if os.name != "nt":
            return
        try:
            store = WindowsAutostartStore()
            AutostartPolicy.clean_stale_entry(store, os.path.exists)
            exe_path = sys.executable
            if self.app_settings.autostart_enabled:
                AutostartPolicy.ensure_enabled(store, exe_path)
            else:
                AutostartPolicy.ensure_disabled(store)
        except Exception:
            pass


def build_app(
    worker: Any | None = None,
    tts: Any | None = None,
    wake: Any | None = None,
    tools: Any | None = None,
    memory: Any | None = None,
    dictation: Any | None = None,
    start_hidden: bool = False,
    argv: list[str] | None = None,
    settings_dir: str | Path | None = None,
) -> tuple[QApplication, MainWindow]:
    """Build the ATHENA app. The integrator's entry point (``main.py``
    is NOT written by the UI builder).

    Args:
        worker: ``athena.llm.worker_client.WorkerClient`` (or None
            — chat then reports "not connected" honestly).
        tts: voice TTS engine (``athena.voice.tts.EdgeTTS`` / SAPI
            fallback) or None when voice output is unavailable.
        wake: wake-word listener (``athena.voice.wake_word``) or
            None. Must expose ``start()``/``stop()`` and either a
            ``wake_word_heard`` Qt signal or ``set_callback(fn)``.
        tools: tool registry (``athena.tools.registry``) or None —
            the Tools panel falls back to the static catalogue.
        memory: local lessons store (kept for later use; D1 via the
            worker stays the memory of record).
        dictation: dictation engine (``athena.voice.stt.VoskDictation``)
            or None — the mic button stays disabled without one.
        start_hidden: ``--tray`` launch — start hidden in the tray.
            When False, ``argv`` is scanned for ``--tray``.
        argv: command-line args to scan for ``--tray`` (defaults to
            ``sys.argv[1:]``).
        settings_dir: override for the settings directory (tests).

    Returns:
        ``(app, window)`` — the configured ``QApplication`` and the
        ``MainWindow``. The caller shows the window (unless
        ``start_hidden``) and runs ``app.exec()``.
    """
    app = QApplication.instance() or QApplication(sys.argv[:1])
    if argv is None:
        argv = sys.argv[1:]
    if not start_hidden:
        start_hidden = parse_startup_options(argv).start_hidden
    window = MainWindow(
        worker=worker,
        tts=tts,
        wake=wake,
        tools=tools,
        memory=memory,
        dictation=dictation,
        start_hidden=start_hidden,
        settings_dir=settings_dir,
    )
    return app, window
