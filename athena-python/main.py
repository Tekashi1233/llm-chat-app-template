"""ATHENA Python v1 — entry point.

Desktop app (PySide6) by default; ``--terminal`` runs the CLI chat
for testing; ``--tray`` starts hidden in the system tray (the
Windows autostart path).

Wiring (the integrator's job — the pieces live in the packages):

* Brain: :class:`athena.agents.agent.AgentLoop` over
  :class:`athena.llm.slotted_provider.SlottedProvider` — Slot 1 is
  the Cloudflare Worker (same site key / D1 memory as the C# app),
  Slot 2 is local Ollama. D1 stays the memory of record; the local
  SQLite store holds reflection lessons only.
* Chat: a small :class:`ChatAdapter` gives the desktop UI one
  ``worker`` object — ``send_message`` goes through the agent loop,
  the ``get_*`` reads go to the Worker client.
* Voice: :class:`FallbackTTS` — Edge TTS neural en-GB female first,
  SAPI Hazel when Edge cannot play. :class:`WakeWordBridge` adapts
  the Vosk listener to the Qt signal the main window expects.
* File writes: :class:`AllowConfirmer` shows the on-screen Allow /
  Deny popup (60s auto-deny) on the GUI thread even when the tool
  call comes from a chat worker thread. No confirmer, no write.

Everything here is $0: Edge TTS (free public endpoint, no key),
Vosk (offline), SAPI (built into Windows), Ollama (local),
Workers AI free tier, PySide6 (LGPL), Tesseract (free binary,
needed for on-demand OCR only).

Known v1 gaps (see ATHENA-python-release-checklist.md): no
first-run key-entry screen (the key is read from the C# app's
``%APPDATA%\\Athena\\config.json`` — existing users are fine);
voice ``wolf`` revoke is not wired (tray Unpair is the fallback,
same as the C# app under the no-password decision); speech is
fire-and-forget, so the greeting text fallback only shows when
speech cannot start at all.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# --- sys.path: dev runs need src/ on the path; a PyInstaller bundle
# already has the package collected, so this is a no-op there.
if not getattr(sys, "frozen", False):
    _SRC = Path(__file__).resolve().parent / "src"
    if str(_SRC) not in sys.path:
        sys.path.insert(0, str(_SRC))

logger = logging.getLogger(__name__)

__all__ = [
    "AllowConfirmer",
    "Brain",
    "ChatAdapter",
    "FallbackTTS",
    "QtAudioPlayer",
    "WakeWordBridge",
    "build_brain",
    "main",
    "parse_args",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Command-line options: --tray (hidden start), --terminal (CLI)."""
    parser = argparse.ArgumentParser(prog="athena", description="ATHENA — personal AI assistant")
    parser.add_argument("--tray", action="store_true", help="start hidden in the system tray")
    parser.add_argument("--terminal", action="store_true", help="run the terminal chat instead of the desktop UI")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Brain: Worker (+Ollama fallback) + agent loop + lessons + background jobs
# ---------------------------------------------------------------------------


@dataclass
class Brain:
    """Everything the interfaces need, built once at startup."""

    agent: Any
    client: Any | None  # WorkerClient or None (no key configured)
    lessons: Any  # LessonStore
    provider: Any  # SlottedProvider
    scheduler: Any | None = None  # IntervalScheduler or None


def build_brain(tool_context: Any | None = None) -> Brain:
    """Build the agent loop. Never raises for a missing key or a
    missing Ollama — the slotted provider degrades honestly."""
    from athena.agents.agent import AgentLoop
    from athena.awareness.live_context import today_info
    from athena.background.decay_watcher import DecayWatcher
    from athena.background.reminders import check_reminders
    from athena.background.scheduler import IntervalScheduler
    from athena.config.secure_store import SecureStore
    from athena.llm.ollama_client import OllamaClient
    from athena.llm.slotted_provider import OllamaSlot, SlottedProvider, WorkerSlot
    from athena.llm.worker_client import WorkerClient
    from athena.memory.calendar import CalendarStore
    from athena.memory.dreaming import Dreamer
    from athena.memory.lessons import LessonStore
    from athena.tools.base_tool import ToolContext

    store = SecureStore()
    client: Any = None
    try:
        client = WorkerClient(api_key=store.require_key())
    except Exception:
        try:
            client = WorkerClient(auth_token=store.require_token())
        except Exception:
            client = None  # no key/token: Ollama-only until one is stored
    slot1 = None
    if client is not None:
        try:
            slot1 = WorkerSlot(client, store.get_or_create_conversation_id())
        except Exception:
            slot1 = None
    provider = SlottedProvider(slot1, OllamaSlot(OllamaClient()))
    lessons = LessonStore()
    agent = AgentLoop(
        provider,
        memory_store=lessons,
        live_context=lambda: today_info(),
        tool_context=tool_context if tool_context is not None else ToolContext(confirm=None),
    )

    scheduler: Any = None
    try:
        scheduler = IntervalScheduler(tick_seconds=30.0)
        dreamer = Dreamer(lessons)
        watcher = DecayWatcher(lessons)
        calendar = CalendarStore(store=lessons)
        scheduler.schedule("dreaming", 30 * 60, lambda: dreamer.run())
        scheduler.schedule("decay-sweep", 6 * 60 * 60, lambda: watcher.run())
        scheduler.schedule("reminders", 60, lambda: check_reminders(calendar))
        scheduler.start()
    except Exception as exc:  # background jobs must never break startup
        logger.warning("[main] background scheduler failed to start: %s", exc)
        scheduler = None
    return Brain(agent=agent, client=client, lessons=lessons, provider=provider, scheduler=scheduler)


# ---------------------------------------------------------------------------
# ChatAdapter: the one "worker" object the desktop UI talks to
# ---------------------------------------------------------------------------


class ChatAdapter:
    """Duck-type worker for the desktop UI.

    ``send_message`` goes through the agent loop (local tools,
    reflection, Ollama fallback); the ``get_*`` reads go to the
    Worker client. ``get_backup`` is deliberately absent — the
    Worker client has no backup call, and the views show an honest
    "not enabled yet" for it.
    """

    def __init__(self, agent: Any, client: Any | None = None) -> None:
        self._agent = agent
        self._client = client

    def send_message(self, text: str, conversation_id: str | None = None, via_voice: bool = False) -> str:
        return self._agent.run_turn(text)

    def get_memories(self, conversation_id: str) -> dict | None:
        return self._client.get_memories(conversation_id) if self._client else None

    def get_model_info(self) -> dict:
        return self._client.get_model_info() if self._client else {}

    def get_device_status(self) -> dict | None:
        return self._client.get_device_status() if self._client else None

    def get_device_activity(self, limit: int = 50) -> dict | None:
        return self._client.get_device_activity(limit) if self._client else None


# ---------------------------------------------------------------------------
# Voice: fallback TTS + wake-word bridge
# ---------------------------------------------------------------------------


class QtAudioPlayer:
    """Play an mp3 file with QtMultimedia (async — returns once
    playback has started). Raises at construction when QtMultimedia
    is unavailable, so the caller can fall back to SAPI instead of
    failing silently.
    """

    def __init__(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer

        self._QUrl = QUrl
        self._player = QMediaPlayer()
        self._audio = QAudioOutput()
        self._player.setAudioOutput(self._audio)

    def __call__(self, path: Any) -> bool:
        try:
            self._player.setSource(self._QUrl.fromLocalFile(str(path)))
            self._player.play()
            return True
        except Exception:
            return False


class FallbackTTS:
    """Edge TTS first, SAPI Hazel when Edge cannot play.

    ``speak`` is fire-and-forget on a worker thread (the UI must
    never block on synthesis) and returns True when speech started.
    A new speak interrupts the previous one. ``stop`` cancels
    in-flight speech. Never raises.
    """

    def __init__(self, settings: Any = None, player: Any = None) -> None:
        from athena.voice.settings import load_settings
        from athena.voice.tts import EdgeTTS, SapiTTS

        self._settings = settings if settings is not None else load_settings()
        self._player = player
        self._edge = EdgeTTS(
            voice_name=self._settings.voice_name,
            rate=self._settings.rate,
            volume=self._settings.volume,
            player=player,
        )
        self._sapi = SapiTTS(
            voice_name=None, rate=self._settings.rate, volume=self._settings.volume
        )
        self._lock = threading.Lock()
        self._generation = 0

    @property
    def is_available(self) -> bool:
        try:
            return bool(self._sapi.is_available) or self._player is not None
        except Exception:
            return self._player is not None

    def speak(self, text: str) -> bool:
        with self._lock:
            self._generation += 1
            generation = self._generation
        thread = threading.Thread(
            target=self._speak_in_thread, args=(text, generation), daemon=True, name="athena-tts"
        )
        thread.start()
        return True

    def _speak_in_thread(self, text: str, generation: int) -> None:
        if self._player is not None:
            try:
                if self._edge.speak(text):
                    return
            except Exception:
                pass
        if generation != self._generation:
            return  # superseded by a newer speak
        try:
            self._sapi.speak(text)
        except Exception:
            pass

    def stop(self) -> None:
        with self._lock:
            self._generation += 1
        for engine in (self._edge, self._sapi):
            try:
                engine.stop()
            except Exception:
                pass


class WakeWordBridge:
    """Adapt the Vosk listener to the Qt signal the main window
    expects. The listener calls ``on_wake`` on its own thread; the
    Qt signal marshals it to the GUI thread. ``start``/``stop`` are
    driven by the main window's own ListeningControl, so the bridge
    does not need to share it.
    """

    def __init__(self, listener: Any) -> None:
        from PySide6.QtCore import QObject, Signal

        class _Bridge(QObject):
            wake_word_heard = Signal()

        self._bridge = _Bridge()
        self.wake_word_heard = self._bridge.wake_word_heard
        self._listener = listener
        # Rebind the listener's callback to the signal (the listener
        # was built with a placeholder; its on_wake is a plain attr).
        self._listener.on_wake = lambda: self._bridge.wake_word_heard.emit()

    def start(self) -> bool:
        try:
            return bool(self._listener.start())
        except Exception:
            return False

    def stop(self) -> None:
        try:
            self._listener.stop()
        except Exception:
            pass

    def pause(self) -> None:
        try:
            self._listener.pause()
        except Exception:
            pass

    def resume(self) -> None:
        try:
            self._listener.resume()
        except Exception:
            pass

    @property
    def is_available(self) -> bool:
        return bool(getattr(self._listener, "is_available", True))


# ---------------------------------------------------------------------------
# AllowConfirmer: on-screen Allow / Deny popup, GUI-thread marshalled
# ---------------------------------------------------------------------------


class AllowConfirmer:
    """Show the file-write Allow/Deny popup on the GUI thread, even
    when the tool call comes from a chat worker thread. 60s
    auto-deny, matching the C# Companion popup. When no GUI handler
    is attached (e.g. terminal mode), every write is refused — a
    tool must never invent its own approval.
    """

    def __init__(self) -> None:
        from PySide6.QtCore import QObject, Signal

        class _Asker(QObject):
            ask_signal = Signal(object)  # dict payload: summary/details/event/result

        self._asker = _Asker()
        self._asker.ask_signal.connect(self._show_dialog)
        self._parent: Any = None

    def attach(self, parent: Any) -> None:
        """Attach the GUI parent (the main window). Call once, after
        the window exists and before any chat turn can run."""
        self._parent = parent

    def __call__(self, summary: str, details: str = "") -> bool:
        event = threading.Event()
        result: dict[str, bool] = {"allow": False}
        payload = {"summary": summary, "details": details, "event": event, "result": result}
        if self._parent is None:
            return False
        self._asker.ask_signal.emit(payload)
        event.wait(timeout=65.0)  # 60s dialog timer + margin
        return bool(result["allow"])

    def _show_dialog(self, payload: dict[str, Any]) -> None:
        from PySide6.QtWidgets import QMessageBox
        from PySide6.QtCore import QTimer

        box = QMessageBox(self._parent)
        box.setWindowTitle("ATHENA — allow this?")
        box.setText(payload["summary"])
        box.setInformativeText(payload["details"] or "")
        allow_button = box.addButton("Allow", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Deny", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(allow_button)
        timer = QTimer(box)
        timer.setSingleShot(True)
        timer.setInterval(60_000)
        timer.timeout.connect(box.reject)
        timer.start()
        box.exec()
        payload["result"]["allow"] = box.clickedButton() is allow_button
        payload["event"].set()


# ---------------------------------------------------------------------------
# Runners
# ---------------------------------------------------------------------------


def run_desktop(args: argparse.Namespace) -> int:
    """Build the brain + voice + UI and run the Qt event loop."""
    from athena.voice.settings import load_settings
    from athena.voice.wake_word import WakeWordListener
    from athena.tools.base_tool import ToolContext

    # File-write confirmations first: the popup needs the main
    # window, which is attached after build_app — until then every
    # write is refused (safe default).
    confirmer = AllowConfirmer()
    brain = build_brain(
        tool_context=ToolContext(confirm=confirmer, confirm_timeout_seconds=60.0)
    )
    settings = load_settings()

    # TTS: Qt player when available, else SAPI-only (never silent-fail).
    try:
        player: Any = QtAudioPlayer()
    except Exception as exc:
        logger.warning("[main] QtMultimedia unavailable, TTS falls back to SAPI: %s", exc)
        player = None
    tts: Any = None
    try:
        tts = FallbackTTS(settings=settings, player=player)
    except Exception as exc:
        logger.warning("[main] TTS unavailable: %s", exc)
        tts = None

    # Wake-word listener (Vosk, local only) behind the Qt bridge.
    wake: Any = None
    try:
        listener = WakeWordListener(on_wake=lambda: None)
        wake = WakeWordBridge(listener)
    except Exception as exc:
        logger.warning("[main] wake-word listener unavailable: %s", exc)
        wake = None

    # File-write confirmations: the popup needs the main window, so
    # the confirmer is attached after build_app returns it.
    from athena.tools.registry import REGISTRY

    chat = ChatAdapter(brain.agent, brain.client)

    from athena.interfaces.desktop import build_app

    app, window = build_app(
        worker=chat,
        tts=tts,
        wake=wake,
        tools=REGISTRY,
        memory=brain.lessons,
        start_hidden=args.tray,
        argv=sys.argv[1:],
    )
    confirmer.attach(window)
    if not args.tray:
        window.show()
    try:
        return int(app.exec())
    finally:
        if brain.scheduler is not None:
            try:
                brain.scheduler.stop()
            except Exception:
                pass


def run_terminal() -> int:
    """Terminal chat (testing interface, no Qt, no audio)."""
    from athena.interfaces.terminal.chat import main as terminal_main

    terminal_main()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point. Returns the process exit code."""
    args = parse_args(argv)
    if args.terminal:
        return run_terminal()
    return run_desktop(args)


if __name__ == "__main__":
    raise SystemExit(main())
