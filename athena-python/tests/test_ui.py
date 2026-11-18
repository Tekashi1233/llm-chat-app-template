"""ATHENA Python v1 — UI tests.

Pure-logic tests run everywhere (no Qt, no Windows):
autostart policy with the fake store, tray menu state logic,
settings persistence round-trip, presence brightness, message
validation, conversation grouping and memory filtering.

Qt widget tests run only when PySide6 is importable, under the
offscreen platform — they prove construction, signals and state
changes, NOT visuals. All visual behaviour is simulated-only on
this Linux VM: nothing here is verified on a real Windows laptop.
"""

from __future__ import annotations

import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

# ---------------------------------------------------------------------------
# Pure logic (no Qt)
# ---------------------------------------------------------------------------


def test_autostart_build_and_parse_round_trip():
    from athena.interfaces.desktop.autostart import AutostartPolicy

    exe = r"C:\Program Files\Athena\Athena.exe"
    value = AutostartPolicy.build_entry_value(exe)
    assert value == '"C:\\Program Files\\Athena\\Athena.exe" --tray'
    assert AutostartPolicy.parse_exe_path(value) == exe
    assert AutostartPolicy.has_tray_argument(value)


def test_autostart_parse_unquoted_and_unparseable():
    from athena.interfaces.desktop.autostart import AutostartPolicy

    assert AutostartPolicy.parse_exe_path(r"C:\Athena\Athena.exe --tray") == (
        r"C:\Athena\Athena.exe"
    )
    assert AutostartPolicy.parse_exe_path("") is None
    assert AutostartPolicy.parse_exe_path(None) is None
    assert AutostartPolicy.parse_exe_path('" --tray') is None
    assert not AutostartPolicy.has_tray_argument(r"C:\Athena\Athena.exe")
    assert AutostartPolicy.has_tray_argument('"C:\\A.exe" --TRAY')


def test_autostart_ensure_enabled_writes_only_when_wrong():
    from athena.interfaces.desktop.autostart import AutostartPolicy, FakeAutostartStore

    store = FakeAutostartStore()
    exe = r"C:\Athena\Athena.exe"
    assert AutostartPolicy.ensure_enabled(store, exe) is True
    assert store.writes == 1
    # Already correct: no blind re-write on the next launch.
    assert AutostartPolicy.ensure_enabled(store, exe) is False
    assert store.writes == 1
    assert AutostartPolicy.is_enabled(store, exe) is True
    # Wrong exe: not enabled, and enabling rewrites it.
    assert AutostartPolicy.is_enabled(store, r"C:\Other\Athena.exe") is False


def test_autostart_ensure_disabled_and_stale_cleanup():
    from athena.interfaces.desktop.autostart import AutostartPolicy, FakeAutostartStore

    store = FakeAutostartStore()
    assert AutostartPolicy.ensure_disabled(store) is False
    AutostartPolicy.ensure_enabled(store, r"C:\Athena\Athena.exe")
    assert AutostartPolicy.ensure_disabled(store) is True
    assert store.get_entry() is None
    assert store.removals == 1

    # Stale: exe no longer exists -> removed.
    store = FakeAutostartStore('"C:\\Gone\\Athena.exe" --tray')
    assert AutostartPolicy.clean_stale_entry(store, lambda _p: False) is True
    assert store.get_entry() is None
    # Live exe -> left alone.
    store = FakeAutostartStore('"C:\\Athena\\Athena.exe" --tray')
    assert AutostartPolicy.clean_stale_entry(store, lambda _p: True) is False
    assert store.get_entry() is not None
    # Unquoted value parses to its first word, so it IS stale
    # when that file is missing — removed, same as the C# code.
    store = FakeAutostartStore("garbage value with no path")
    assert AutostartPolicy.clean_stale_entry(store, lambda _p: False) is True
    # Genuinely unparseable entry (broken quote) -> left alone,
    # it may belong to something else.
    store = FakeAutostartStore('" --tray')
    assert AutostartPolicy.clean_stale_entry(store, lambda _p: False) is False
    assert store.get_entry() == '" --tray'


def test_startup_options_tray_parsing():
    from athena.interfaces.desktop.autostart import parse_startup_options

    assert parse_startup_options([]).start_hidden is False
    assert parse_startup_options(["--tray"]).start_hidden is True
    assert parse_startup_options(["--TRAY"]).start_hidden is True
    assert parse_startup_options(["--other", "--tray"]).start_hidden is True
    assert parse_startup_options(["--trayx"]).start_hidden is False


def test_tray_menu_state_logic():
    from athena.config.policies import ListeningControl
    from athena.interfaces.desktop.tray import TrayMenuState

    control = ListeningControl(enabled=True)
    state = TrayMenuState.from_listening(control)
    assert state.listening_label == "Listening: On"
    assert state.listening_checked is True
    assert state.pause_label == "Pause listening"
    assert state.pause_checked is False
    assert state.pause_enabled is True

    control.pause()
    state = TrayMenuState.from_listening(control)
    assert state.pause_label == "Resume listening"
    assert state.pause_checked is True

    control.set_enabled(False)
    state = TrayMenuState.from_listening(control)
    assert state.listening_label == "Listening: Off"
    assert state.listening_checked is False
    assert state.pause_enabled is False
    # Disabling leaves Paused untouched (C# ListeningControl rule:
    # moot while disabled); re-enabling clears it ("listen now").
    assert state.pause_checked is True
    control.set_enabled(True)
    state = TrayMenuState.from_listening(control)
    assert state.pause_checked is False
    assert state.pause_label == "Pause listening"


def test_settings_persistence_round_trip(tmp_path):
    from athena.interfaces.desktop.settings_store import (
        AppSettings,
        VoiceSettings,
        load_app_settings,
        load_voice_settings,
        save_app_settings,
        save_voice_settings,
    )

    voice = VoiceSettings(
        output_enabled=True,
        voice_name="Test Voice",
        rate=-3,
        volume=80,
        mic_enabled=False,
        recognition_culture="en-GB",
        wake_word_enabled=False,
        greet_on_wake_word=True,
    )
    assert save_voice_settings(voice, tmp_path) is True
    loaded = load_voice_settings(tmp_path)
    assert loaded == voice

    # Missing file -> None (caller uses defaults); corrupt -> None.
    assert load_voice_settings(tmp_path / "nope") is None
    (tmp_path / "voice.json").write_text("{ not json", encoding="utf-8")
    assert load_voice_settings(tmp_path) is None

    app = AppSettings(autostart_enabled=False)
    assert save_app_settings(app, tmp_path) is True
    assert load_app_settings(tmp_path) == app


def test_settings_clamping_and_old_file_defaults(tmp_path):
    from athena.interfaces.desktop.settings_store import VoiceSettings, load_voice_settings

    # Old voice.json without the N7/N8 fields: wake + greet stay ON.
    (tmp_path / "voice.json").write_text(
        json.dumps({"OutputEnabled": False, "Rate": 0, "Volume": 100}),
        encoding="utf-8",
    )
    loaded = load_voice_settings(tmp_path)
    assert loaded is not None
    assert loaded.wake_word_enabled is True
    assert loaded.greet_on_wake_word is True

    # Out-of-range values are clamped on load.
    clamped = VoiceSettings(rate=99, volume=-5, recognition_culture="  ").clamped()
    assert clamped.rate == 10
    assert clamped.volume == 0
    assert clamped.recognition_culture == "en-GB"


def test_presence_brightness_table():
    from athena.interfaces.desktop.presence import brightness_for

    idle = brightness_for("idle")
    assert (idle.glow, idle.core) == (0.25, 0.55)
    assert brightness_for("offline").glow < brightness_for("idle").glow
    assert brightness_for("error").red > brightness_for("idle").red
    assert brightness_for("listening").glow > brightness_for("idle").glow
    assert brightness_for("thinking").pulse is True
    assert brightness_for("speaking").pulse is True
    assert brightness_for("working").pulse is False
    # Approval shares Working's lighting; unknown states fall back to idle.
    assert brightness_for("approval").glow == brightness_for("working").glow
    assert brightness_for("nonsense").glow == idle.glow
    assert brightness_for("Thinking").pulse is True  # C# enum names accepted


def test_message_validation():
    from athena.interfaces.desktop.messages import (
        MAX_MESSAGE_LENGTH,
        is_valid_user_message,
        is_within_length_limit,
    )

    assert MAX_MESSAGE_LENGTH == 8000
    assert is_valid_user_message("hello") is True
    assert is_valid_user_message("   ") is False
    assert is_valid_user_message("") is False
    assert is_valid_user_message(None) is False
    assert is_within_length_limit("x" * 8000) is True
    assert is_within_length_limit("x" * 8001) is False


def test_format_age():
    from athena.interfaces.desktop.views_data import format_age

    assert format_age(1) == "1 second ago"
    assert format_age(45) == "45 seconds ago"
    assert format_age(60) == "1 minute ago"
    assert format_age(120) == "2 minutes ago"
    assert format_age(3600) == "1 hour ago"
    assert format_age(7200) == "2 hours ago"


def test_group_conversations_titles_come_from_first_user_message():
    from athena.interfaces.desktop.views_data import group_conversations

    rows = [
        {"conversation_id": "a", "role": "user", "content": "hello there", "created_at_ms": 1000},
        {"conversation_id": "a", "role": "assistant", "content": "hi", "created_at_ms": 2000},
        {"conversation_id": "b", "role": "user", "content": "second chat", "created_at_ms": 3000},
    ]
    groups = group_conversations(rows)
    assert len(groups) == 2
    by_id = {g.id: g for g in groups}
    assert by_id["a"].title == "hello there"
    assert by_id["a"].message_count == 2
    assert by_id["b"].title == "second chat"
    # Newest first.
    assert groups[0].id == "b"


def test_filter_memories_missing_fields_do_not_crash():
    from athena.interfaces.desktop.views_data import filter_memories, snippet

    memories = [
        {"topic": "favourite colour", "fact": "teal", "kind": "preference", "status": "active"},
        {"topic": None, "fact": None, "kind": "fact", "status": "active"},
        {"topic": "project", "fact": "RankForge", "kind": "project", "status": "superseded"},
    ]
    assert len(filter_memories(memories)) == 3
    assert len(filter_memories(memories, query="teal")) == 1
    assert len(filter_memories(memories, kind="project")) == 1
    assert len(filter_memories(memories, status="active")) == 2
    assert len(filter_memories(memories, query="zzz")) == 0
    assert snippet(None) == ""
    assert snippet("x" * 200).endswith("…")
    assert len(snippet("x" * 200)) == 90


def test_device_quick_actions_approval():
    from athena.interfaces.desktop.views_data import DEVICE_QUICK_ACTIONS, is_approved_device_prompt

    assert len(DEVICE_QUICK_ACTIONS) == 4
    assert is_approved_device_prompt("Ping google.com") is True
    assert is_approved_device_prompt("rm -rf /") is False
    assert is_approved_device_prompt("ping google.com") is False  # case-sensitive, exact


# ---------------------------------------------------------------------------
# Qt widget tests (only when PySide6 is importable; offscreen, simulated)
# ---------------------------------------------------------------------------

# Qt widget tests are skipped (not failed) when PySide6 cannot be
# imported — e.g. a missing system lib on this VM. The pure tests
# above must still run in that case, so the Qt import is lazy.
try:
    from PySide6.QtWidgets import QApplication

    _QT_AVAILABLE = True
except ImportError as _qt_error:  # pragma: no cover - environment-dependent
    QApplication = None  # type: ignore[assignment]
    _QT_AVAILABLE = False
    _QT_ERROR = str(_qt_error)

needs_qt = pytest.mark.skipif(
    not _QT_AVAILABLE, reason=f"PySide6 not importable: {_QT_ERROR if not _QT_AVAILABLE else ''}"
)


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication(sys.argv[:1])
    yield app

@needs_qt
def test_reactor_set_presence_and_flash(qapp):
    from athena.interfaces.desktop.presence import brightness_for
    from athena.interfaces.desktop.reactor import ArcReactor

    reactor = ArcReactor(260)
    reactor.set_presence("idle")
    assert reactor.presence == "idle"
    assert reactor.brightness == (
        brightness_for("idle").glow,
        brightness_for("idle").core,
        brightness_for("idle").blue,
        brightness_for("idle").red,
    )
    reactor.set_presence("offline")
    assert reactor.brightness[0] == brightness_for("offline").glow
    reactor.set_presence("unknown-state")
    assert reactor.presence == "unknown-state"
    assert reactor.brightness[0] == brightness_for("idle").glow
    reactor.flash()
    assert reactor.brightness[0] == 0.95
    assert reactor.brightness[1] == 1.0
    reactor.set_size(84)
    assert reactor.width() == 84


@needs_qt
def test_chat_view_bubbles_and_message_count(qapp):
    from athena.interfaces.desktop.chat_view import ChatView

    view = ChatView()
    assert view.message_count == 0
    view.add_message("You", "hello", is_user=True)
    view.add_message("ATHENA", "hi there", is_user=False)
    assert view.message_count == 2
    view.add_system_message("a note", is_error=False)
    # message_count counts every feed row, system lines included
    # (same as C# MessageCount over MessagesPanel children).
    assert view.message_count == 3
    view.render_history(
        [{"role": "user", "content": "old"}, {"role": "assistant", "content": "older"}]
    )
    assert view.message_count == 5


@needs_qt
def test_chat_view_send_without_worker_is_honest(qapp):
    from athena.interfaces.desktop.chat_view import ChatView

    view = ChatView(send_fn=None)
    view.input_box.setText("hello")
    view.send()
    # Not connected: no user bubble is added — one honest system
    # line instead, and no crash.
    assert view.message_count == 1


@needs_qt
def test_chat_view_send_round_trip(qapp):
    from athena.interfaces.desktop.chat_view import ChatView

    received: list[str] = []

    def fake_send(text: str) -> str:
        received.append(text)
        return "fake reply"

    view = ChatView(send_fn=fake_send)
    replies: list[bool] = []
    view.reply_received.connect(lambda: replies.append(True))
    view.input_box.setText("ping")
    view.send()
    assert view.message_count == 1  # user bubble only, reply is async
    # Let the worker thread deliver the reply on the GUI thread.
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(2000, loop.quit)
    loop.exec()
    assert received == ["ping"]
    assert view.message_count == 2
    assert replies == [True]


@needs_qt
def test_chat_view_mic_flag_cleared_by_hand_edit(qapp):
    from athena.interfaces.desktop.chat_view import ChatView

    view = ChatView(send_fn=lambda _t: "ok")
    view.set_mic_text("spoken words")
    assert view._mic_filled is True
    view.input_box.setText("spoken words edited")
    assert view._mic_filled is False


@needs_qt
def test_settings_view_unavailable_states(qapp, tmp_path):
    from athena.interfaces.desktop.settings_store import VoiceSettings
    from athena.interfaces.desktop.settings_view import PRIVACY_LINE, SettingsView

    # No TTS, no recogniser: controls shown disabled, honestly.
    view = SettingsView(
        voice_settings=VoiceSettings(),
        tts=None,
        available_voices=[],
        recogniser_available=False,
        autostart_store=None,
        settings_dir=tmp_path,
    )
    assert view._wake_check.isEnabled() is False
    assert view._greet_check.isEnabled() is False
    assert view._autostart_check.isEnabled() is False
    # The privacy line is rendered in the card.
    from PySide6.QtWidgets import QLabel

    assert any(
        PRIVACY_LINE in label.text() for label in view.findChildren(QLabel)
    )
    labels = view.findChildren(type(view._wake_check))
    assert labels  # checkboxes exist even when disabled


@needs_qt
def test_settings_view_toggle_persists(qapp, tmp_path):
    from athena.interfaces.desktop.settings_store import VoiceSettings, load_voice_settings
    from athena.interfaces.desktop.settings_view import SettingsView
    from athena.interfaces.desktop.autostart import FakeAutostartStore

    view = SettingsView(
        voice_settings=VoiceSettings(wake_word_enabled=True),
        tts=None,
        available_voices=[],
        recogniser_available=True,
        autostart_store=FakeAutostartStore(),
        settings_dir=tmp_path,
    )
    assert view._wake_check.isEnabled() is True
    view._wake_check.setChecked(False)
    saved = load_voice_settings(tmp_path)
    assert saved is not None
    assert saved.wake_word_enabled is False


@needs_qt
def test_main_window_build_app_idle_and_conversation(qapp, tmp_path):
    from athena.interfaces.desktop.main_window import build_app

    app, window = build_app(settings_dir=tmp_path)
    window.show()
    _pump(0.1)
    # Idle: the ChatView is VISIBLE (its input bar is the only place
    # to type) but the message FEED is hidden. v1 hid the whole view
    # at idle, which hid the input with it — the first message could
    # never be typed.
    assert window.chat_view.message_count == 0
    assert not window.chat_view.isHidden()
    assert window.chat_view.feed_visible is False
    assert window.chat_view.input_box.isVisible()
    assert window.chat_view.input_box.isEnabled()
    window._set_conversation_layout()
    assert window.chat_view.feed_visible is True
    window._set_idle_layout()
    assert window.chat_view.feed_visible is False
    assert not window.chat_view.isHidden()
    window._really_quitting = True
    window.close()


@needs_qt
def test_main_window_start_hidden(qapp, tmp_path):
    from athena.interfaces.desktop.main_window import build_app

    app, window = build_app(start_hidden=True, settings_dir=tmp_path)
    assert window.isHidden()
    window._really_quitting = True
    window.close()


@needs_qt
def test_main_window_wake_greeting_text_fallback(qapp, tmp_path):
    from athena.interfaces.desktop.main_window import build_app

    app, window = build_app(settings_dir=tmp_path)
    # No TTS: the greeting shows as text under the wordmark.
    window._greet_if_enabled()
    assert not window._greeting_label.isHidden()
    window._really_quitting = True
    window.close()


@needs_qt
def test_presence_machine_rules():
    from athena.interfaces.desktop.main_window import PresenceMachine

    machine = PresenceMachine()
    # Thinking only from Chat.
    assert machine.try_set("thinking", "system", "x") is False
    assert machine.try_set("thinking", "chat", "Thinking…") is True
    # System must not clear Thinking mid-request.
    assert machine.try_set("idle", "system", "Idle") is False
    assert machine.try_set("idle", "chat", "Idle") is True
    # Listening only from Voice; Working only from Device.
    assert machine.try_set("listening", "chat", "x") is False
    assert machine.try_set("listening", "voice", "Listening…") is True
    assert machine.try_set("working", "voice", "x") is False
    assert machine.try_set("working", "device", "Working…") is True
    # Offline from anyone; Error from Chat or System only.
    assert machine.try_set("offline", "voice", "Offline") is True
    assert machine.try_set("error", "device", "x") is False
    assert machine.try_set("error", "system", "Boom") is True


@needs_qt
def test_tray_icon_labels_refresh(qapp):
    from athena.config.policies import ListeningControl
    from athena.interfaces.desktop.tray import AthenaTrayIcon

    control = ListeningControl(enabled=True)
    tray = AthenaTrayIcon(listening=control)
    tray.refresh_labels()
    assert tray._listening_action.text() == "Listening: On"
    assert tray._listening_action.isChecked() is True
    control.pause()
    tray.refresh_labels()
    assert tray._pause_action.text() == "Resume listening"
    control.set_enabled(False)
    tray.refresh_labels()
    assert tray._listening_action.text() == "Listening: Off"
    assert tray._pause_action.isEnabled() is False
    tray.hide()

# ---------------------------------------------------------------------------
# Idle-input bug + mic dictation wiring (v1.1 fix).
#
# v1 hid the whole ChatView at idle, which hid the only input box:
# the first message could never be typed, and the voice toggle was
# unreachable too. The mic button only ever posted "not wired".
# These tests pin the fix: input bar visible at idle, dictation
# fills the input without sending, voice toggle reachable at idle.
# ---------------------------------------------------------------------------


def _pump(seconds: float = 0.1) -> None:
    """Run the Qt event loop briefly so queued signals deliver."""
    from PySide6.QtCore import QEventLoop, QTimer

    loop = QEventLoop()
    QTimer.singleShot(int(seconds * 1000), loop.quit)
    loop.exec()


def _wait_until(predicate, timeout_s: float = 3.0) -> bool:
    """Pump the event loop until ``predicate`` holds or we time out."""
    import time

    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        _pump(0.05)
    return predicate()


class FakeWorker:
    """Duck-type worker for build_app: send_message -> canned reply."""

    def __init__(self, reply: str = "fake reply") -> None:
        self.received: list[str] = []
        self._reply = reply

    def send_message(self, text: str) -> str:
        self.received.append(text)
        return self._reply


class FakeTTS:
    """Duck-type TTS: records what it was asked to speak."""

    def __init__(self) -> None:
        self.spoken: list[str] = []
        self.stopped = 0

    def speak(self, text: str) -> bool:
        self.spoken.append(text)
        return True

    def stop(self) -> None:
        self.stopped += 1

    def list_voices(self) -> list[str]:
        return []


class FakeWake:
    """Duck-type wake-word listener: records pause/resume calls."""

    def __init__(self) -> None:
        self.started = False
        self.paused = 0
        self.resumed = 0

    def start(self) -> bool:
        self.started = True
        return True

    def stop(self) -> None:
        self.started = False

    def pause(self) -> None:
        self.paused += 1

    def resume(self) -> None:
        self.resumed += 1


class FakeDictation:
    """Scripted dictation: yields the given RecognitionEvents, then
    the stream ends. ``available``/``model_present`` model the
    engine state; the fake ensurer in the download tests flips
    them to True."""

    def __init__(self, events, available: bool = True, model_present: bool = True) -> None:
        from athena.voice.stt import RecognitionEvent

        self._events = [RecognitionEvent(**e) if isinstance(e, dict) else e for e in events]
        self.is_available = available
        self.model_present = model_present
        self.last_error: str | None = None
        self.stopped = False
        self.started = 0

    def transcribe(self):
        self.started += 1
        yield from self._events

    def stop(self) -> None:
        self.stopped = True


@needs_qt
def test_main_window_idle_input_bar_visible_and_first_message_flow(qapp, tmp_path):
    """Regression (v1 bug): on first launch there was nowhere to
    type — the input bar lived inside the hidden ChatView. Now the
    bar is visible and enabled at idle, sits at the bottom of the
    window, and typing + Enter sends the first message, shows the
    feed and shrinks the reactor."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from athena.interfaces.desktop.main_window import build_app
    from athena.interfaces.desktop import theme

    worker = FakeWorker()
    app, window = build_app(worker=worker, settings_dir=tmp_path)
    window.show()
    _pump(0.2)

    # Idle: feed hidden, input bar visible, enabled, at the bottom.
    assert window.chat_view.message_count == 0
    assert window.chat_view.feed_visible is False
    assert window.chat_view.input_box.isVisible()
    assert window.chat_view.input_box.isEnabled()
    assert window.chat_view.send_button.isEnabled()
    box = window.chat_view.input_box
    # The glass input bar sits at the window bottom: the ChatView's
    # bottom edge is flush with the window's (the 12px bottom margin
    # of the bar is inside the view).
    assert window.height() - window.chat_view.geometry().bottom() <= 2

    # Typing + Enter sends the first message and the feed appears.
    box.setText("hello athena")
    QTest.keyClick(box, Qt.Key.Key_Return)
    assert _wait_until(lambda: window.chat_view.message_count == 2)
    assert worker.received == ["hello athena"]
    assert window.chat_view.feed_visible is True
    assert window._reactor.width() == 84
    assert window._wordmark.font().pointSize() == theme.WORDMARK_CHAT_PT
    window._really_quitting = True
    window.close()


@needs_qt
def test_chat_view_mic_dictation_fills_input_without_sending(qapp):
    """Mic button starts a real dictation session; recognised text
    fills the input via set_mic_text and nothing is sent."""
    from athena.interfaces.desktop.chat_view import ChatView

    fake = FakeDictation(
        [{"text": "hel", "is_final": False}, {"text": "hello world", "is_final": True}]
    )
    view = ChatView(send_fn=lambda _t: "ok")
    started: list[bool] = []
    stopped: list[bool] = []
    view.dictation_started.connect(lambda: started.append(True))
    view.dictation_stopped.connect(lambda: stopped.append(True))
    view.refresh_voice_controls(True, True, True)
    view.set_dictation(fake)
    assert view.mic_button.isEnabled()

    view.mic_button.click()
    assert view.mic_button.text() == "Stop"
    assert _wait_until(lambda: stopped == [True])
    assert view.input_box.text() == "hello world"
    # Filled from the mic — but nothing was sent.
    assert view.message_count == 0
    assert view._mic_filled is True
    assert started == [True]
    assert view.mic_button.text() == "Mic"


@needs_qt
def test_chat_view_mic_downloads_model_when_missing(qapp):
    """Model missing: tapping the mic downloads it once (in a
    thread, UI responsive), then dictation starts and fills the
    input. A failed download is reported honestly, not stuck."""
    from athena.interfaces.desktop.chat_view import ChatView

    fake = FakeDictation(
        [{"text": "open notepad", "is_final": True}],
        available=False,
        model_present=False,
    )
    calls: list[bool] = []

    def fake_ensurer(download: bool = False):
        calls.append(download)
        fake.is_available = True
        fake.model_present = True
        return "/fake/model"

    view = ChatView(send_fn=lambda _t: "ok")
    view.refresh_voice_controls(True, True, True)
    view.set_dictation(fake, model_ensurer=fake_ensurer)
    view.mic_button.click()
    assert _wait_until(lambda: view.input_box.text() == "open notepad")
    assert calls == [True]
    assert view.message_count == 0

    # Failure path: ensurer returns None -> honest message, mic back.
    fake2 = FakeDictation([], available=False, model_present=False)
    view2 = ChatView(send_fn=lambda _t: "ok")
    view2.refresh_voice_controls(True, True, True)
    view2.set_dictation(fake2, model_ensurer=lambda download=False: None)
    view2.mic_button.click()
    assert _wait_until(lambda: view2.message_count == 1)
    assert view2._downloading_model is False
    assert view2.mic_button.text() == "Mic"


@needs_qt
def test_main_window_voice_toggle_enabled_at_idle_with_tts(qapp, tmp_path):
    """Voice toggle is visible and enabled at idle whenever TTS is
    available; turning it on makes replies speak. The Settings
    voice section also opens from the idle screen."""
    from athena.interfaces.desktop.main_window import build_app
    from athena.interfaces.desktop.settings_view import SettingsView

    tts = FakeTTS()
    worker = FakeWorker()
    app, window = build_app(worker=worker, tts=tts, settings_dir=tmp_path)
    window.show()
    _pump(0.2)

    toggle = window.chat_view.voice_toggle
    assert toggle.isVisible()
    assert toggle.isEnabled()
    assert toggle.text() == "Voice: Off"
    toggle.click()
    assert toggle.text() == "Voice: On"

    window.chat_view.input_box.setText("hello")
    window.chat_view.send()
    assert _wait_until(lambda: tts.spoken == ["fake reply"])
    assert worker.received == ["hello"]

    # Settings -> Voice opens from the idle screen (no message needed).
    # _show_section alone does not raise the overlay — the menu does.
    window._show_menu()
    window._show_section("Settings")
    assert window._overlay.isVisible()
    assert window.findChild(SettingsView) is not None
    window._really_quitting = True
    window.close()


@needs_qt
def test_main_window_mic_pauses_wake_word(qapp, tmp_path):
    """While dictating, the wake-word listener pauses (so dictation
    cannot re-trigger it) and resumes after. Same as C# N7."""
    from athena.interfaces.desktop.main_window import build_app

    fake = FakeDictation([{"text": "hello world", "is_final": True}])
    wake = FakeWake()
    app, window = build_app(
        worker=FakeWorker(), wake=wake, dictation=fake, settings_dir=tmp_path
    )
    window.show()
    _pump(0.2)
    assert window.chat_view.mic_button.isEnabled()

    window.chat_view.mic_button.click()
    assert _wait_until(lambda: wake.paused == 1)
    assert _wait_until(lambda: wake.resumed == 1)
    assert window.chat_view.input_box.text() == "hello world"
    assert window.chat_view.message_count == 0
    window._really_quitting = True
    window.close()


@needs_qt
def test_chat_view_mic_without_dictation_is_honest(qapp):
    """No dictation engine wired: the mic button is disabled, and
    a direct tap reports honestly instead of pretending to listen."""
    from athena.interfaces.desktop.chat_view import ChatView

    view = ChatView(send_fn=lambda _t: "ok")
    view.refresh_voice_controls(True, True, True)
    assert view.mic_button.isEnabled() is False
    view._on_mic_clicked()
    assert view.message_count == 1  # one honest system line
