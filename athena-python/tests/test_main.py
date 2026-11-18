"""Tests for main.py — the entry-point wiring (adapters, TTS fallback,
wake-word bridge, confirmer safe default, arg parsing, brain build)."""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import main as main_mod
from main import AllowConfirmer, ChatAdapter, FallbackTTS, WakeWordBridge, parse_args


class FakeAgent:
    def __init__(self) -> None:
        self.turns: list[str] = []

    def run_turn(self, text: str) -> str:
        self.turns.append(text)
        return f"echo: {text}"


class FakeClient:
    def get_memories(self, conversation_id: str) -> dict:
        return {"conversation_id": conversation_id, "memories": []}

    def get_model_info(self) -> dict:
        return {"assistant": "ATHENA"}

    def get_device_status(self) -> dict:
        return {"devices": []}

    def get_device_activity(self, limit: int = 50) -> dict:
        return {"activity": []}


def test_chat_adapter_sends_through_agent():
    adapter = ChatAdapter(FakeAgent(), FakeClient())
    assert adapter.send_message("hello") == "echo: hello"


def test_chat_adapter_reads_delegate_to_client():
    adapter = ChatAdapter(FakeAgent(), FakeClient())
    assert adapter.get_memories("c1")["conversation_id"] == "c1"
    assert adapter.get_model_info()["assistant"] == "ATHENA"
    assert adapter.get_device_status() == {"devices": []}
    assert adapter.get_device_activity()["activity"] == []


def test_chat_adapter_without_client_returns_none():
    adapter = ChatAdapter(FakeAgent(), None)
    assert adapter.send_message("hi") == "echo: hi"
    assert adapter.get_memories("c1") is None
    assert adapter.get_model_info() == {}
    assert adapter.get_device_status() is None


def test_parse_args():
    assert parse_args([]).tray is False
    assert parse_args(["--tray"]).tray is True
    assert parse_args(["--terminal"]).terminal is True


class FakeEdge:
    def __init__(self, speak_result: bool = True) -> None:
        self.speak_result = speak_result
        self.spoken: list[str] = []
        self.stopped = False

    def speak(self, text: str) -> bool:
        self.spoken.append(text)
        return self.speak_result

    def stop(self) -> None:
        self.stopped = True


class FakeSapi:
    def __init__(self, available: bool = True) -> None:
        self.is_available = available
        self.spoken: list[str] = []

    def speak(self, text: str) -> bool:
        self.spoken.append(text)
        return True

    def stop(self) -> None:
        pass


def _make_tts(monkeypatch, edge_result=True, player_present=True):
    from athena.voice.settings import VoiceSettings

    edge = FakeEdge(edge_result)
    sapi = FakeSapi()
    monkeypatch.setattr("athena.voice.tts.EdgeTTS", lambda **kw: edge)
    monkeypatch.setattr("athena.voice.tts.SapiTTS", lambda **kw: sapi)
    player = object() if player_present else None
    tts = FallbackTTS(settings=VoiceSettings(), player=player)
    return tts, edge, sapi


def test_fallback_tts_prefers_edge_when_player_present(monkeypatch):
    tts, edge, sapi = _make_tts(monkeypatch, edge_result=True, player_present=True)
    tts._generation = 1
    tts._speak_in_thread("hello", 1)
    assert edge.spoken == ["hello"]
    assert sapi.spoken == []


def test_fallback_tts_uses_sapi_when_edge_fails(monkeypatch):
    tts, edge, sapi = _make_tts(monkeypatch, edge_result=False, player_present=True)
    tts._generation = 1
    tts._speak_in_thread("hello", 1)
    assert edge.spoken == ["hello"]
    assert sapi.spoken == ["hello"]


def test_fallback_tts_skips_edge_without_player(monkeypatch):
    tts, edge, sapi = _make_tts(monkeypatch, edge_result=True, player_present=False)
    tts._generation = 1
    tts._speak_in_thread("hello", 1)
    assert edge.spoken == []
    assert sapi.spoken == ["hello"]


def test_fallback_tts_superseded_speak_does_not_fall_back(monkeypatch):
    tts, edge, sapi = _make_tts(monkeypatch, edge_result=False, player_present=True)
    # _generation stays 0, so generation 999 is stale.
    tts._speak_in_thread("hello", 999)
    assert sapi.spoken == []


def test_confirmer_refuses_without_gui_parent():
    confirmer = AllowConfirmer()
    assert confirmer("write this file?") is False


class FakeListener:
    def __init__(self) -> None:
        self.on_wake = lambda: None
        self.started = False
        self.is_available = True

    def start(self) -> bool:
        self.started = True
        return True

    def stop(self) -> None:
        self.started = False

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass


def test_wake_word_bridge_emits_signal_and_delegates():
    pytest.importorskip("PySide6.QtCore")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    listener = FakeListener()
    bridge = WakeWordBridge(listener)
    received: list[bool] = []
    bridge.wake_word_heard.connect(lambda: received.append(True))
    listener.on_wake()
    # Cross-thread signals need the event loop; a direct emit from
    # the same thread is delivered synchronously.
    assert received == [True]
    assert bridge.start() is True
    assert listener.started is True
    bridge.stop()
    assert listener.started is False
    assert bridge.is_available is True


def test_http_clients_ignore_hostile_proxy_env(monkeypatch):
    """Regression: a bracketed no_proxy entry ("[::1]") made
    httpx.Client() raise InvalidURL at construction. Both clients
    must build with trust_env=False."""
    monkeypatch.setenv("no_proxy", "[::1],localhost")
    monkeypatch.setenv("NO_PROXY", "[::1],localhost")
    from athena.llm.ollama_client import OllamaClient
    from athena.llm.worker_client import WorkerClient

    ollama = OllamaClient()
    worker = WorkerClient(api_key="test-key-123")
    try:
        assert ollama.base_url == "http://localhost:11434"
        assert worker.base_url.startswith("https://")
    finally:
        ollama.close()
        worker.close()


def test_build_brain_degrades_without_key(tmp_path, monkeypatch):
    import athena.memory.lessons as lessons_mod

    monkeypatch.setattr(lessons_mod, "DEFAULT_DB_PATH", tmp_path / "lessons.db")
    brain = main_mod.build_brain()
    try:
        assert brain.agent is not None
        assert brain.lessons is not None
        # No key on this VM and no Ollama: slot 1 unavailable, but
        # the provider still exists and the agent answers honestly.
        assert brain.provider.slot1_available is False
        reply = brain.agent.run_turn("hello")
        assert isinstance(reply, str) and reply.strip()
    finally:
        if brain.scheduler is not None:
            brain.scheduler.stop()
        try:
            brain.lessons.close()
        except Exception:
            pass
