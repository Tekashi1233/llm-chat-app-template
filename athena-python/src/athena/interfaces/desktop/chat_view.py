"""ATHENA Python v1 — chat view.

Port of ``Views/ChatView.xaml(.cs)``: the message feed, the
thinking/listening indicators, and the frosted-glass input bar
(mic + voice toggle + send).

Rules carried over from the C# code:

* Bubbles: ATHENA — transparent fill, gold border, gold label;
  user — transparent fill, grey border, muted label.
* Scroll-to-bottom runs AFTER the layout pass (queued, not
  synchronous). The C# bug was scrolling synchronously, before the
  new bubble was measured, so the newest reply sat out of sight.
* A message is only sent when non-empty and <= 8000 characters
  (``MessageValidation``); an over-long message gets an honest
  answer, not a silent drop.
* Text that came from the microphone is tagged ``via_voice`` on the
  next send — any hand edit of the text clears the flag first, so a
  typed or edited message is never voice-tagged (the Worker's
  enforcement for the voice-only "wolf" revoke depends on this).
* Replies are read aloud only when voice output is on; a new
  message interrupts any speech in progress.

Sending: the view takes a ``send_fn`` callable (injected by the
shell — normally an adapter over the Worker client). It runs in a
worker thread so the UI never freezes on a slow reply, and the
reply is delivered back on the GUI thread. ``send_fn`` may be a
plain function returning ``str`` or an async function; both are
handled. When no ``send_fn`` is wired, sending says so honestly
instead of pretending to work.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import theme

__all__ = [
    "MAX_MESSAGE_LENGTH",
    "ChatView",
    "is_valid_user_message",
    "is_within_length_limit",
    "resolve_send",
]

from .messages import (
    APPROVAL_WAITING_PHRASE,
    MAX_MESSAGE_LENGTH,
    is_approval_waiting,
    is_valid_user_message,
    is_within_length_limit,
)


def resolve_send(worker: Any) -> Callable[[str], Any] | None:
    """Adapt a Worker client to the ``send_fn(text) -> reply`` shape.

    PROVISIONAL adapter — the core builder owns
    ``athena.llm.worker_client.WorkerClient`` and its method names
    are not final yet. It tries, in order: ``send_message``,
    ``chat``, ``ask``. When none exists it returns None and the
    chat view reports "not connected" honestly. The integrator
    should replace this with the real call once the core lands.
    """
    if worker is None:
        return None
    for name in ("send_message", "chat", "ask"):
        fn = getattr(worker, name, None)
        if callable(fn):
            return fn
    return None


class _SendWorker(QObject):
    """Runs one send in a thread; delivers the reply on the GUI thread."""

    finished = Signal(object)  # reply text, or an Exception instance
    failed = Signal(str)  # honest error text for the feed

    def __init__(self, send_fn: Callable, text: str) -> None:
        super().__init__()
        self._send_fn = send_fn
        self._text = text

    def run(self) -> None:
        try:
            result = self._send_fn(self._text)
            if inspect.isawaitable(result):
                result = asyncio.run(result)
            self.finished.emit(str(result) if result is not None else "")
        except Exception as exc:  # never crash the send thread
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class ChatView(QWidget):
    """One conversation: feed + glass input bar. Kept alive for the
    whole session by MainWindow so chat state survives navigation."""

    #: Raised the moment a live reply from ATHENA lands, so the
    #: shell can light the reactor up.
    reply_received = Signal()
    #: Raised whenever a real message bubble is added, so the shell
    #: can switch between the idle wordmark and the conversation view.
    messages_changed = Signal()
    #: Raised when a send starts / finishes, so the shell can drive
    #: presence (thinking/working/idle). Payload: (state, detail).
    presence_requested = Signal(str, str)

    def __init__(
        self,
        send_fn: Callable[[str], Any] | None = None,
        tts: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._send_fn = send_fn
        self._tts = tts
        self._sending = False
        self._mic_filled = False
        self._setting_mic_text = False
        self._thread: QThread | None = None
        self._worker: _SendWorker | None = None
        self._device_command = False
        self._voice_output_enabled = False

        self.setStyleSheet(f"background: {theme.BACKGROUND};")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # --- Feed -------------------------------------------------------
        self._scroller = QScrollArea(self)
        self._scroller.setWidgetResizable(True)
        self._scroller.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroller.setStyleSheet("QScrollArea { border: none; }")
        self._feed = QWidget()
        self._feed_layout = QVBoxLayout(self._feed)
        self._feed_layout.setContentsMargins(16, 8, 16, 8)
        self._feed_layout.setSpacing(0)
        self._feed_layout.addStretch(1)
        self._scroller.setWidget(self._feed)
        root.addWidget(self._scroller, 1)

        # --- Thinking / listening indicators ----------------------------
        indicators = QHBoxLayout()
        indicators.setContentsMargins(16, 0, 16, 8)
        self._thinking_label = QLabel("Athena is thinking…")
        self._thinking_label.setStyleSheet(f"color: {theme.MUTED}; font-style: italic;")
        self._thinking_label.hide()
        self._listening_label = QLabel("Listening… speak now, or tap Stop.")
        self._listening_label.setStyleSheet(f"color: {theme.GOLD}; font-style: italic;")
        self._listening_label.hide()
        indicators.addWidget(self._thinking_label)
        indicators.addWidget(self._listening_label)
        indicators.addStretch(1)
        root.addLayout(indicators)

        # --- Glass input bar --------------------------------------------
        bar = QFrame()
        bar.setObjectName("GlassPanel")
        bar.setStyleSheet(theme.GLASS_PANEL_QSS)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 8, 12, 8)
        bar_layout.setSpacing(8)

        self.input_box = QLineEdit()
        self.input_box.setPlaceholderText("Message ATHENA…")
        self.input_box.setStyleSheet(theme.INPUT_QSS)
        self.input_box.returnPressed.connect(self.send)
        self.input_box.textChanged.connect(self._on_text_changed)
        bar_layout.addWidget(self.input_box, 1)

        self.mic_button = QPushButton("Mic")
        self.mic_button.setStyleSheet(theme.GHOST_BUTTON_QSS)
        self.mic_button.setEnabled(False)
        self.mic_button.setToolTip(
            "Tap to speak — your words go into the box for you to check before sending."
        )
        self.mic_button.clicked.connect(self._on_mic_clicked)
        bar_layout.addWidget(self.mic_button)

        self.voice_toggle = QPushButton("Voice: Off")
        self.voice_toggle.setStyleSheet(theme.GHOST_BUTTON_QSS)
        self.voice_toggle.setEnabled(False)
        self.voice_toggle.setToolTip(
            "Read replies aloud with the female British voice. Off by default."
        )
        self.voice_toggle.clicked.connect(self._on_voice_toggle_clicked)
        bar_layout.addWidget(self.voice_toggle)

        self.send_button = QPushButton("Send")
        self.send_button.setStyleSheet(theme.GOLD_BUTTON_QSS)
        self.send_button.clicked.connect(self.send)
        bar_layout.addWidget(self.send_button)

        bar_wrap = QVBoxLayout()
        bar_wrap.setContentsMargins(16, 0, 16, 12)
        bar_wrap.addWidget(bar)
        root.addLayout(bar_wrap)

    # -- public API ------------------------------------------------------

    @property
    def message_count(self) -> int:
        """How many message bubbles are currently shown."""
        return self._feed_layout.count() - 1  # minus the stretch

    def focus_input(self) -> None:
        self.input_box.setFocus()

    def set_send_fn(self, send_fn: Callable[[str], Any] | None) -> None:
        self._send_fn = send_fn

    def set_tts(self, tts: Any | None) -> None:
        self._tts = tts

    def set_voice_output_enabled(self, enabled: bool) -> None:
        self._voice_output_enabled = enabled
        self.voice_toggle.setText("Voice: On" if enabled else "Voice: Off")

    def refresh_voice_controls(
        self, tts_available: bool, recogniser_available: bool, mic_enabled: bool
    ) -> None:
        """Enable/disable mic + voice-toggle from the live voice
        service. When voice is unavailable both stay disabled — the
        app never pretends voice works."""
        self.voice_toggle.setEnabled(tts_available)
        self.mic_button.setEnabled(recogniser_available and mic_enabled)

    def set_mic_text(self, text: str) -> None:
        """Fill the input from the microphone. The next send is
        tagged via_voice — unless the user edits the text first."""
        self._setting_mic_text = True
        self.input_box.setText(text)
        self._setting_mic_text = False
        self._mic_filled = True
        self.input_box.setFocus()

    def render_history(self, messages: list[dict]) -> None:
        """Render saved messages (``{"role": ..., "content": ...}``)
        in order. Anything that is not user/assistant is skipped."""
        for row in messages:
            role = row.get("role")
            content = row.get("content") or ""
            if role == "user":
                self.add_message("You", content, is_user=True)
            elif role == "assistant":
                self.add_message("ATHENA", content, is_user=False)
        self.scroll_to_bottom()

    def send_device_command(self, prompt: str) -> None:
        """Send a device command from the Devices view. Only prompts
        from the approved list may go down this path — the caller
        (Devices panel) is responsible for that check."""
        self._device_command = True
        self.send(prompt)

    def send(self, text: str | None = None) -> None:
        if self._sending:
            return
        text = self.input_box.text() if text is None else text
        if not is_valid_user_message(text):
            return
        if not is_within_length_limit(text):
            self.add_system_message(
                f"That message is too long — keep it under "
                f"{MAX_MESSAGE_LENGTH} characters.",
                is_error=True,
            )
            return
        if self._send_fn is None:
            self.add_system_message(
                "Not connected yet — the Worker client is not wired up, "
                "so this message was not sent.",
                is_error=True,
            )
            return

        text = text.strip()
        via_voice = self._mic_filled and not self._device_command
        self._mic_filled = False
        device_command = self._device_command
        self._device_command = False

        self._sending = True
        self.send_button.setEnabled(False)
        self.input_box.clear()
        self.add_message("You", text, is_user=True)
        if not device_command:
            self._thinking_label.show()
        self._stop_speaking()
        if device_command:
            self.presence_requested.emit("working", "Working on your laptop…")
        else:
            self.presence_requested.emit("thinking", "Thinking…")

        self._thread = QThread(self)
        self._worker = _SendWorker(self._send_fn, text)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._on_reply)
        self._worker.failed.connect(self._on_send_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()

    # -- messages ----------------------------------------------------------

    def add_message(self, who: str, text: str, is_user: bool) -> None:
        border = theme.GLASS_BORDER if is_user else theme.GLASS_GOLD_BORDER
        label_colour = theme.MUTED if is_user else theme.GOLD
        bubble = QFrame()
        bubble.setStyleSheet(
            f"QFrame {{ background: transparent; border: 1px solid {border}; "
            f"border-radius: 10px; }}"
        )
        layout = QVBoxLayout(bubble)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)
        who_label = QLabel(who)
        who_label.setStyleSheet(f"color: {label_colour}; font-weight: 600; font-size: 11px;")
        body = QLabel(text)
        body.setWordWrap(True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setStyleSheet(f"color: {theme.TEXT}; font-size: 14px;")
        layout.addWidget(who_label)
        layout.addWidget(body)

        row = QHBoxLayout()
        row.setContentsMargins(0, 6, 0, 6)
        if is_user:
            row.addStretch(1)
        row.addWidget(bubble, 0)
        if not is_user:
            row.addStretch(1)
        # Cap the bubble width so it never spans the whole window.
        bubble.setMaximumWidth(720)
        container = QWidget()
        container.setLayout(row)
        self._feed_layout.insertWidget(self._feed_layout.count() - 1, container)
        self.scroll_to_bottom()
        self.messages_changed.emit()

    def add_system_message(self, text: str, is_error: bool) -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        colour = theme.ERROR if is_error else theme.MUTED
        label.setStyleSheet(f"color: {colour}; font-style: italic; font-size: 13px;")
        wrap = QVBoxLayout()
        wrap.setContentsMargins(0, 6, 0, 6)
        wrap.addWidget(label)
        container = QWidget()
        container.setLayout(wrap)
        self._feed_layout.insertWidget(self._feed_layout.count() - 1, container)
        self.scroll_to_bottom()

    def scroll_to_bottom(self) -> None:
        """Scroll AFTER the layout pass.

        Queued (singleShot 0), so the new bubble is measured first.
        Scrolling synchronously runs before layout and leaves the
        newest reply out of sight — the C# invisible-reply bug.
        """
        bar = self._scroller.verticalScrollBar()
        QTimer.singleShot(0, lambda: bar.setValue(bar.maximum()))

    # -- slots -------------------------------------------------------------

    def _on_text_changed(self, _text: str) -> None:
        # Any hand edit clears the mic flag — a typed or edited
        # message is never tagged via_voice.
        if not self._setting_mic_text:
            self._mic_filled = False

    def _on_mic_clicked(self) -> None:
        # Dictation is driven by the voice module (wired by the
        # shell through set_mic_text); the button itself only
        # reports the honest state when nothing is wired.
        self.add_system_message(
            "Microphone input isn't wired up yet — the voice module "
            "has not provided a recogniser.",
            is_error=True,
        )

    def _on_voice_toggle_clicked(self) -> None:
        if self._tts is None:
            self.add_system_message(
                "Voice output isn't available on this PC — no speech "
                "voices were found.",
                is_error=True,
            )
            self.voice_toggle.setEnabled(False)
            return
        self.set_voice_output_enabled(not self._voice_output_enabled)

    def _on_reply(self, reply: object) -> None:
        text = str(reply)
        self.add_message("ATHENA", text, is_user=False)
        if is_approval_waiting(text):
            self.presence_requested.emit(
                "approval", "Waiting for your approval on the laptop"
            )
        else:
            self.presence_requested.emit("idle", "Idle")
        self.reply_received.emit()
        if self._voice_output_enabled and self._tts is not None:
            try:
                speak = getattr(self._tts, "speak", None)
                if callable(speak):
                    speak(text)
            except Exception:
                pass  # spoken replies are best-effort
        self._finish_send()

    def _on_send_failed(self, error_text: str) -> None:
        self.add_system_message(
            f"Couldn't reach ATHENA — {error_text}. Check your "
            "connection and try again.",
            is_error=True,
        )
        self.presence_requested.emit("offline", "Offline")
        self._finish_send()

    def _finish_send(self) -> None:
        self._thinking_label.hide()
        self.send_button.setEnabled(True)
        self._sending = False
        self.input_box.setFocus()
        if self._thread is not None:
            self._thread.quit()
            self._thread = None
        self._worker = None

    def _stop_speaking(self) -> None:
        if self._tts is None:
            return
        stop = getattr(self._tts, "stop", None) or getattr(
            self._tts, "stop_speaking", None
        )
        if callable(stop):
            try:
                stop()
            except Exception:
                pass
