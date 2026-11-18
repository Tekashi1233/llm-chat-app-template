"""Tests for athena.llm / athena.config / athena.awareness — pure logic only.

Fake HTTP via httpx.MockTransport, fake slots, fake secure store
(a reversible test protector — NOT DPAPI). No Qt, Vosk, SAPI or
other UI/audio imports anywhere in these paths; the import test
below proves the core package loads without them.

Windows-only behaviour (real DPAPI via pywin32) is SIMULATED
only here: the fake protector stands in for CryptProtectData /
CryptUnprotectData, and no test runs on Windows. Real DPAPI must
still be verified on the laptop.
"""

from __future__ import annotations

import base64
import json
import os
import sys
from pathlib import Path

import httpx
import pytest

from athena.awareness import get_status, now_local, today_info
from athena.config import NotConfiguredError, SecureStore
from athena.llm import (
    AthenaApiError,
    KeyValidationResult,
    Mode,
    ModeSwitch,
    OllamaClient,
    OllamaError,
    OllamaSlot,
    SlottedProvider,
    WorkerClient,
    WorkerSlot,
    parse_sse,
)
from athena.reasoning.chain_of_thought import strip_thought_block


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------

SSE_OK = (
    'data: {"choices":[{"delta":{"content":"Hello "}}]}\n'
    'data: {"choices":[{"delta":{"content":"there"}}]}\n'
    "data: [DONE]\n"
)


def mock_client(handler) -> httpx.Client:
    # base_url required: the clients post relative paths ("/api/chat").
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://test")


class FakeProtector:
    """Reversible stand-in for DPAPI (tests only — NOT secure)."""

    def protect(self, plaintext: bytes) -> bytes:
        return base64.b64encode(plaintext[::-1])

    def unprotect(self, ciphertext: bytes) -> bytes:
        return base64.b64decode(ciphertext)[::-1]


class FakeWorkerSlot:
    """Slot 1 fake: returns canned text or raises a canned error."""

    def __init__(self, *, available=True, reply="worker reply", error=None):
        self.name = "ATHENA cloud (Worker)"
        self._available = available
        self.reply = reply
        self.error = error
        self.calls = 0

    @property
    def is_available(self):
        return self._available

    def generate(self, messages, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.reply


class FakeOllamaSlot:
    """Slot 2 fake."""

    def __init__(self, *, reply="ollama reply", error=None):
        self.name = "llama3.1:8b (Ollama)"
        self.reply = reply
        self.error = error
        self.calls = 0
        self.last_messages = None

    @property
    def is_available(self):
        return True

    def generate(self, messages, **kwargs):
        self.calls += 1
        self.last_messages = messages
        if self.error is not None:
            raise self.error
        return self.reply


# ----------------------------------------------------------------------
# import hygiene: core must load with no Qt / audio / voice imports
# ----------------------------------------------------------------------

def test_core_imports_need_no_qt_or_audio(tmp_path):
    """The brain layers must load with no Qt / audio / voice imports.

    Order-independent: pytest imports EVERY test module during
    collection, so by the time this test runs ``sys.modules`` already
    holds PySide6 (from the UI tests) — checking the live process
    proves nothing. Instead the check runs in a FRESH subprocess
    that imports only the brain layers and reports what got loaded.
    The assertion itself is unchanged: no forbidden module may load.
    """
    import subprocess

    forbidden = {"PySide6", "vosk", "edge_tts", "pyttsx3", "sounddevice"}
    probe = (
        "import sys; "
        "import athena.llm, athena.config, athena.awareness, athena.reasoning; "
        "loaded = {m.split('.')[0] for m in sys.modules}; "
        "print(','.join(sorted(loaded)))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, f"probe failed: {proc.stderr}"
    loaded = set(proc.stdout.strip().split(",")) if proc.stdout.strip() else set()
    assert not (loaded & forbidden), f"core pulled in {loaded & forbidden}"


# ----------------------------------------------------------------------
# parse_sse (port of C# SseParser)
# ----------------------------------------------------------------------

def test_parse_sse_delta_chunks_concatenated():
    assert parse_sse(SSE_OK) == "Hello there"


def test_parse_sse_message_content_and_response_forms():
    body = (
        'data: {"choices":[{"message":{"content":"full"}}]}\n'
        'data: {"response":"top"}\n'
        "data: [DONE]\n"
    )
    assert parse_sse(body) == "fulltop"


def test_parse_sse_skips_malformed_chunk_and_keeps_rest():
    body = 'data: {"choices":[{"delta":{"content":"a"}}]}\ndata: {broken\ndata: {"choices":[{"delta":{"content":"b"}}]}\n'
    assert parse_sse(body) == "ab"


def test_parse_sse_ignores_non_data_lines_and_empty():
    assert parse_sse("event: ping\n: keep-alive\ndata: [DONE]\n") == ""
    assert parse_sse("") == ""


# ----------------------------------------------------------------------
# WorkerClient — chat
# ----------------------------------------------------------------------

def test_send_message_posts_key_header_and_parses_stream():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=SSE_OK)

    client = WorkerClient(api_key="k-123", client=mock_client(handler))
    assert client.send_message("hi", "conv-1") == "Hello there"
    assert seen["headers"]["x-vesper-key"] == "k-123"
    assert "authorization" not in seen["headers"]
    assert seen["body"]["messages"] == [{"role": "user", "content": "hi"}]
    assert seen["body"]["conversation_id"] == "conv-1"


def test_send_message_token_preferred_over_key():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, text=SSE_OK)

    client = WorkerClient(
        api_key="k-123", auth_token="tok-abc", client=mock_client(handler)
    )
    client.send_message("hi", "conv-1")
    assert seen["headers"]["authorization"] == "Bearer tok-abc"
    assert "x-vesper-key" not in seen["headers"]


def test_send_message_via_voice_tag_only_when_spoken():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=SSE_OK)

    client = WorkerClient(api_key="k", client=mock_client(handler))
    client.send_message("typed", "c")
    assert "via_voice" not in seen["body"]["messages"][0]
    client.send_message("spoken", "c", via_voice=True)
    assert seen["body"]["messages"][0]["via_voice"] is True


def test_send_message_rejects_empty_and_overlong():
    client = WorkerClient(api_key="k", client=mock_client(lambda r: httpx.Response(200, text=SSE_OK)))
    with pytest.raises(ValueError, match="empty"):
        client.send_message("   ", "c")
    with pytest.raises(ValueError, match="too long"):
        client.send_message("x" * 8001, "c")
    assert client.send_message("x" * 8000, "c") == "Hello there"


def test_send_message_no_auth_raises_clear_error():
    client = WorkerClient(client=mock_client(lambda r: httpx.Response(200, text=SSE_OK)))
    with pytest.raises(AthenaApiError, match="No token and no site key"):
        client.send_message("hi", "c")


def test_send_message_empty_reply_uses_fallback_text():
    client = WorkerClient(
        api_key="k",
        client=mock_client(lambda r: httpx.Response(200, text="data: [DONE]\n")),
    )
    assert client.send_message("hi", "c").startswith("Sorry, I didn't manage")


@pytest.mark.parametrize(
    "status, fragment",
    [
        (401, "Not authorised"),
        (429, "Too many requests"),
        (500, "had a problem"),
        (418, "HTTP 418"),
    ],
)
def test_send_message_http_errors_are_plain_language(status, fragment):
    client = WorkerClient(
        api_key="k", client=mock_client(lambda r: httpx.Response(status, text=""))
    )
    with pytest.raises(AthenaApiError) as exc_info:
        client.send_message("hi", "c")
    assert exc_info.value.status_code == status
    assert fragment in str(exc_info.value)


def test_send_message_offline_is_plain_language():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    client = WorkerClient(api_key="k", client=mock_client(handler))
    with pytest.raises(AthenaApiError) as exc_info:
        client.send_message("hi", "c")
    assert exc_info.value.status_code == 0
    assert "Couldn't reach the service" in str(exc_info.value)


# ----------------------------------------------------------------------
# WorkerClient — validate_auth + reads
# ----------------------------------------------------------------------

@pytest.mark.parametrize(
    "status, expected",
    [
        (400, KeyValidationResult.VALID),
        (401, KeyValidationResult.INVALID),
        (429, KeyValidationResult.RATE_LIMITED),
        (500, KeyValidationResult.ERROR),
    ],
)
def test_validate_auth_status_mapping(status, expected):
    client = WorkerClient(
        api_key="k", client=mock_client(lambda r: httpx.Response(status, text=""))
    )
    assert client.validate_auth() is expected


def test_validate_auth_sends_empty_messages_key_check():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(400, text="")

    client = WorkerClient(api_key="k", client=mock_client(handler))
    assert client.validate_auth() is KeyValidationResult.VALID
    assert seen["body"] == {"messages": [], "conversation_id": "key-check"}


def test_get_model_info_public_no_auth_header():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = dict(request.headers)
        return httpx.Response(200, json={"model": "gpt-oss-20b", "tools": []})

    client = WorkerClient(client=mock_client(handler))
    assert client.get_model_info()["model"] == "gpt-oss-20b"
    assert "x-vesper-key" not in seen["headers"]
    assert "authorization" not in seen["headers"]


def test_get_memories_404_returns_none_not_error():
    client = WorkerClient(
        api_key="k", client=mock_client(lambda r: httpx.Response(404, text=""))
    )
    assert client.get_memories("conv-1") is None
    assert client.get_device_status() is None
    assert client.get_device_activity() is None


def test_get_memories_returns_json_when_deployed():
    client = WorkerClient(
        api_key="k",
        client=mock_client(
            lambda r: httpx.Response(200, json={"memories": [{"topic": "colour"}]})
        ),
    )
    assert client.get_memories("c") == {"memories": [{"topic": "colour"}]}


def test_get_device_activity_passes_limit():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"actions": []})

    client = WorkerClient(api_key="k", client=mock_client(handler))
    assert client.get_device_activity(limit=10) == {"actions": []}
    assert "limit=10" in seen["url"]


# ----------------------------------------------------------------------
# OllamaClient
# ----------------------------------------------------------------------

def test_ollama_generate_posts_chat_and_returns_content():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"message": {"role": "assistant", "content": "local hi"}}
        )

    client = OllamaClient(client=mock_client(handler))
    assert client.generate([{"role": "user", "content": "hi"}]) == "local hi"
    assert seen["body"]["model"] == "llama3.1:8b"
    assert seen["body"]["stream"] is False


def test_ollama_generate_absent_server_is_clear_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = OllamaClient(client=mock_client(handler))
    with pytest.raises(OllamaError, match="Ollama isn't running"):
        client.generate([{"role": "user", "content": "hi"}])


def test_ollama_generate_empty_reply_is_error():
    client = OllamaClient(
        client=mock_client(
            lambda r: httpx.Response(200, json={"message": {"content": "  "}})
        )
    )
    with pytest.raises(OllamaError, match="empty"):
        client.generate([{"role": "user", "content": "hi"}])


def test_ollama_is_running_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    assert OllamaClient(client=mock_client(handler)).is_running() is False
    ok = OllamaClient(
        client=mock_client(lambda r: httpx.Response(200, json={"models": []}))
    )
    assert ok.is_running() is True


# ----------------------------------------------------------------------
# strip_thought_block
# ----------------------------------------------------------------------

def test_strip_thought_block_removes_reasoning():
    raw = "<thought>Let me think…</thought>Final answer."
    assert strip_thought_block(raw) == "Final answer."


def test_strip_thought_block_handles_unclosed_block():
    assert strip_thought_block("Answer start <thought>trailing") == "Answer start"


def test_strip_thought_block_leaves_plain_text_alone():
    assert strip_thought_block("plain answer") == "plain answer"


# ----------------------------------------------------------------------
# SlottedProvider
# ----------------------------------------------------------------------

def _provider(slot1, slot2=None):
    return SlottedProvider(slot1, slot2 or FakeOllamaSlot())


def test_provider_worker_serves_directly_no_note():
    p = _provider(FakeWorkerSlot(reply="cloud answer"))
    text, slot, note = p.generate([{"role": "user", "content": "hi"}])
    assert (text, slot, note) == ("cloud answer", "worker", None)
    assert p.last_used() == "worker"


def test_provider_falls_back_on_429():
    slot1 = FakeWorkerSlot(error=AthenaApiError(429, "Too many requests"))
    slot2 = FakeOllamaSlot(reply="local answer")
    p = _provider(slot1, slot2)
    text, slot, note = p.generate([{"role": "user", "content": "hi"}])
    assert text == "local answer" and slot == "ollama"
    assert note and "on-device model" in note
    assert slot1.calls == 1 and slot2.calls == 1


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504, 0])
def test_provider_falls_back_on_retryable_worker_failures(status):
    slot1 = FakeWorkerSlot(error=AthenaApiError(status, "fail"))
    p = _provider(slot1, FakeOllamaSlot(reply="local"))
    _, slot, _ = p.generate([{"role": "user", "content": "hi"}])
    assert slot == "ollama"


def test_provider_does_not_fall_back_on_401():
    slot1 = FakeWorkerSlot(error=AthenaApiError(401, "Not authorised"))
    slot2 = FakeOllamaSlot(reply="local")
    p = _provider(slot1, slot2)
    text, slot, note = p.generate([{"role": "user", "content": "hi"}])
    assert slot == "worker" and "Not authorised" in text
    assert slot2.calls == 0


def test_provider_ollama_only_when_no_slot1():
    p = SlottedProvider(None, FakeOllamaSlot(reply="local only"))
    text, slot, note = p.generate([{"role": "user", "content": "hi"}])
    assert (slot, p.last_used()) == ("ollama", "ollama")
    assert note and "not configured" in note


def test_provider_never_raises_when_both_slots_fail():
    slot1 = FakeWorkerSlot(error=AthenaApiError(500, "boom"))
    slot2 = FakeOllamaSlot(error=OllamaError("Ollama isn't running"))
    p = _provider(slot1, slot2)
    text, slot, note = p.generate([{"role": "user", "content": "hi"}])
    assert slot == "none" and "Ollama isn't running" in text


def test_provider_status_and_active_slot_name():
    p = _provider(FakeWorkerSlot())
    assert p.active_slot_name == "ATHENA cloud (Worker)"
    assert p.get_status()["slot1_available"] is True
    p2 = SlottedProvider(None, FakeOllamaSlot())
    assert p2.active_slot_name == "llama3.1:8b (Ollama)"
    assert p2.get_status()["slot1"] == "disabled"


def test_worker_slot_availability_is_key_presence_only():
    with_key = WorkerSlot(
        WorkerClient(api_key="k", client=mock_client(lambda r: httpx.Response(200, text=SSE_OK))),
        "c",
    )
    assert with_key.is_available is True
    without = WorkerSlot(
        WorkerClient(client=mock_client(lambda r: httpx.Response(200, text=SSE_OK))),
        "c",
    )
    assert without.is_available is False


def test_worker_slot_sends_only_last_user_message():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, text=SSE_OK)

    slot = WorkerSlot(
        WorkerClient(api_key="k", client=mock_client(handler)), "conv-9"
    )
    slot.generate([
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ])
    assert seen["body"]["messages"] == [{"role": "user", "content": "second"}]
    assert seen["body"]["conversation_id"] == "conv-9"


def test_ollama_slot_adds_thought_instruction_and_strips_block():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"message": {"content": "<thought>reasoning</thought>Shown."}},
        )

    slot = OllamaSlot(OllamaClient(client=mock_client(handler)))
    assert slot.generate([{"role": "user", "content": "hi"}]) == "Shown."
    system_msgs = [m for m in seen["body"]["messages"] if m["role"] == "system"]
    assert system_msgs and "<thought>" in system_msgs[0]["content"]


# ----------------------------------------------------------------------
# SecureStore (fake protector — DPAPI itself is simulated-only here)
# ----------------------------------------------------------------------

def test_secure_store_round_trip_key_and_token(tmp_path):
    store = SecureStore(config_path=tmp_path / "config.json", protector=FakeProtector())
    assert store.try_load_key() is None
    assert store.is_configured() is False
    store.save_key("site-key-123")
    assert store.try_load_key() == "site-key-123"
    assert store.is_configured() is True
    store.save_token("owner-token-abc")
    assert store.try_load_token() == "owner-token-abc"
    # Saving one keeps the other.
    assert store.try_load_key() == "site-key-123"


def test_secure_store_never_writes_plaintext(tmp_path):
    path = tmp_path / "config.json"
    store = SecureStore(config_path=path, protector=FakeProtector())
    store.save_key("super-secret-key")
    store.save_token("super-secret-token")
    raw = path.read_text()
    assert "super-secret-key" not in raw
    assert "super-secret-token" not in raw
    assert "ProtectedKey" in raw and "ProtectedToken" in raw


def test_secure_store_reads_csharp_pascalcase_keys(tmp_path):
    path = tmp_path / "config.json"
    fake = FakeProtector()
    blob = base64.b64encode(fake.protect(b"csharp-key")).decode()
    path.write_text(json.dumps({"ProtectedKey": blob, "ConversationId": "id-1"}))
    store = SecureStore(config_path=path, protector=fake)
    assert store.try_load_key() == "csharp-key"


def test_secure_store_corrupt_config_behaves_as_empty(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{not json")
    store = SecureStore(config_path=path, protector=FakeProtector())
    assert store.try_load_key() is None
    assert store.is_configured() is False
    # A later save rewrites the file cleanly.
    store.save_key("k2")
    assert store.try_load_key() == "k2"


def test_secure_store_bad_base64_and_undecryptable_are_none(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"ProtectedKey": "!!!not-base64!!!"}))
    store = SecureStore(config_path=path, protector=FakeProtector())
    assert store.try_load_key() is None

    class BrokenProtector:
        def protect(self, b): return b
        def unprotect(self, b): raise ValueError("bad blob")

    path.write_text(json.dumps({"ProtectedKey": base64.b64encode(b"x").decode()}))
    assert SecureStore(config_path=path, protector=BrokenProtector()).try_load_key() is None


def test_secure_store_delete_key_keeps_token_and_id(tmp_path):
    store = SecureStore(config_path=tmp_path / "config.json", protector=FakeProtector())
    store.save_key("k")
    store.save_token("t")
    conv_id = store.get_or_create_conversation_id()
    store.delete_key()
    assert store.try_load_key() is None
    assert store.try_load_token() == "t"
    assert store.get_or_create_conversation_id() == conv_id
    store.delete_token()
    assert store.try_load_token() is None
    assert store.get_or_create_conversation_id() == conv_id


def test_secure_store_conversation_id_stable_and_uuid(tmp_path):
    store = SecureStore(config_path=tmp_path / "config.json", protector=FakeProtector())
    first = store.get_or_create_conversation_id()
    assert store.get_or_create_conversation_id() == first
    assert len(first) == 36 and first.count("-") == 4


def test_secure_store_require_key_raises_not_configured(tmp_path):
    store = SecureStore(config_path=tmp_path / "config.json", protector=FakeProtector())
    with pytest.raises(NotConfiguredError, match="No site key"):
        store.require_key()


def test_secure_store_non_windows_default_protector_refuses(tmp_path):
    # On this Linux box there is no DPAPI: saving must fail clearly,
    # loading must behave as "not stored" — never a crash.
    store = SecureStore(config_path=tmp_path / "config.json")
    assert store.try_load_key() is None
    with pytest.raises(Exception, match="DPAPI"):
        store.save_key("k")


# ----------------------------------------------------------------------
# live_context + modes
# ----------------------------------------------------------------------

def test_today_info_shape_and_riyadh_day():
    info = today_info("Asia/Riyadh")
    assert set(info) == {"day", "date", "time", "timezone", "iso"}
    assert info["timezone"] == "Asia/Riyadh"
    assert info["day"] in {
        "Monday", "Tuesday", "Wednesday", "Thursday", "Friday",
        "Saturday", "Sunday",
    }
    assert len(info["date"]) == 10 and info["date"][4] == "-"


def test_now_local_is_aware():
    assert now_local("Asia/Riyadh").tzinfo is not None


def test_get_status_never_probes_network():
    status = get_status(_provider(FakeWorkerSlot()))
    assert status["laptop_online"] == "unknown"
    assert status["slots"]["slot1_available"] is True
    assert "day" in status


def test_mode_switch_online_when_key_stored():
    p = _provider(FakeWorkerSlot(available=True))
    switch = ModeSwitch(p)
    assert switch.mode is Mode.ONLINE
    assert switch.is_online and not switch.is_offline


def test_mode_switch_offline_when_no_slot1():
    p = SlottedProvider(None, FakeOllamaSlot())
    switch = ModeSwitch(p)
    assert switch.mode is Mode.OFFLINE
    assert switch.is_offline


def test_mode_switch_notifies_on_flip_only():
    slot1 = FakeWorkerSlot(available=True)
    p = _provider(slot1)
    switch = ModeSwitch(p)
    seen = []
    switch.on_change(lambda old, new: seen.append((old, new)))
    assert switch.refresh() is Mode.ONLINE
    assert seen == []
    slot1._available = False
    assert switch.refresh() is Mode.OFFLINE
    assert seen == [(Mode.ONLINE, Mode.OFFLINE)]
    assert switch.refresh() is Mode.OFFLINE
    assert len(seen) == 1  # no repeat notification without a flip


def test_mode_switch_listener_failure_does_not_break_switch():
    slot1 = FakeWorkerSlot(available=True)
    p = _provider(slot1)
    switch = ModeSwitch(p)

    def bad_listener(old, new):
        raise RuntimeError("listener boom")

    switch.on_change(bad_listener)
    slot1._available = False
    assert switch.refresh() is Mode.OFFLINE
