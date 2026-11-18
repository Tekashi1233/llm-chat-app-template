"""Tests for athena.voice — pure logic only.

No real audio, no network, no microphone: Edge TTS, SAPI, Vosk
and the mic are all driven through fakes. The one exception is
``test_real_voice_deps_import`` which only checks the real packages
import (they are installed in the venv); it synthesises nothing.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from athena.config.policies import ListeningControl, WakeWordPolicy
from athena.voice import (
    DEFAULT_VOICE_NAME,
    GREETING_TEXT,
    EdgeTTS,
    RecognitionEvent,
    SapiTTS,
    ShowReason,
    VoiceSettings,
    VoskDictation,
    WakeWordListener,
    ensure_model,
    find_model,
    greet,
    greet_if_wake_word,
    load_settings,
    model_missing_message,
    save_settings,
    should_greet,
    speak_with_fallback,
)


# ----------------------------------------------------------------------
# Real dependency imports (no audio, no network — import only)
# ----------------------------------------------------------------------

def test_real_voice_deps_import():
    import edge_tts
    import vosk

    assert edge_tts.Communicate is not None
    assert vosk.Model is not None
    assert vosk.KaldiRecognizer is not None


# ----------------------------------------------------------------------
# Settings
# ----------------------------------------------------------------------

def test_settings_defaults_output_off_wake_word_on():
    s = VoiceSettings()
    assert s.output_enabled is False
    assert s.voice_name == DEFAULT_VOICE_NAME == "en-GB-SoniaNeural"
    assert s.rate == 0 and s.volume == 100
    assert s.wake_word_enabled is True
    assert s.greet_on_wake_word is True


def test_settings_round_trip(tmp_path):
    path = tmp_path / "voice.json"
    s = VoiceSettings(output_enabled=True, voice_name="en-GB-SoniaNeural",
                      rate=3, volume=80, wake_word_enabled=False)
    assert save_settings(s, path) is True
    loaded = load_settings(path)
    assert loaded == s


def test_settings_saved_shape_is_csharp_pascalcase(tmp_path):
    path = tmp_path / "voice.json"
    save_settings(VoiceSettings(output_enabled=True), path)
    raw = json.loads(path.read_text())
    assert raw["OutputEnabled"] is True
    assert raw["VoiceName"] == "en-GB-SoniaNeural"
    assert raw["WakeWordEnabled"] is True
    assert raw["GreetOnWakeWord"] is True


def test_settings_loads_csharp_file(tmp_path):
    # A voice.json written by the C# app (PascalCase keys).
    path = tmp_path / "voice.json"
    path.write_text(json.dumps({
        "OutputEnabled": True, "VoiceName": "Microsoft Hazel Desktop",
        "Rate": -2, "Volume": 55, "MicEnabled": False,
        "RecognitionCulture": "en-GB",
        "WakeWordEnabled": False, "GreetOnWakeWord": False,
    }))
    s = load_settings(path)
    assert s.output_enabled is True
    assert s.voice_name == "Microsoft Hazel Desktop"
    assert s.rate == -2 and s.volume == 55
    assert s.mic_enabled is False
    assert s.wake_word_enabled is False
    assert s.greet_on_wake_word is False


def test_settings_loads_snake_case_too(tmp_path):
    path = tmp_path / "voice.json"
    path.write_text(json.dumps({"output_enabled": True, "rate": 4}))
    s = load_settings(path)
    assert s.output_enabled is True and s.rate == 4
    assert s.voice_name == DEFAULT_VOICE_NAME  # missing -> default


def test_settings_missing_file_is_defaults(tmp_path):
    assert load_settings(tmp_path / "nope.json") == VoiceSettings()


def test_settings_corrupt_file_is_defaults_never_raises(tmp_path):
    path = tmp_path / "voice.json"
    path.write_text("{not json")
    assert load_settings(path) == VoiceSettings()
    path.write_text(json.dumps(["not", "a", "dict"]))
    assert load_settings(path) == VoiceSettings()
    path.write_text(json.dumps({"Rate": "banana", "Volume": 9999,
                                "OutputEnabled": "yes"}))
    s = load_settings(path)
    assert s.rate == 0 and s.volume == 100  # clamped / defaulted
    assert s.output_enabled is False  # wrong type -> default


def test_settings_clamping():
    s = VoiceSettings(rate=99, volume=-5).clamped()
    assert s.rate == 10 and s.volume == 0
    s = VoiceSettings(recognition_culture="   ").clamped()
    assert s.recognition_culture == "en-GB"


def test_settings_save_creates_parent_dir(tmp_path):
    path = tmp_path / "deep" / "voice.json"
    assert save_settings(VoiceSettings(), path) is True
    assert path.is_file()


# ----------------------------------------------------------------------
# TTS fakes
# ----------------------------------------------------------------------

class FakeCommunicate:
    """Stand-in for edge_tts.Communicate; records args, writes bytes."""
    instances = []

    def __init__(self, text, voice, rate="+0%", volume="+0%", **_kw):
        self.text = text
        self.voice = voice
        self.rate = rate
        self.volume = volume
        FakeCommunicate.instances.append(self)

    async def save(self, path):
        Path(path).write_bytes(b"fake-mp3:" + self.text.encode())


class FakeSapiEngine:
    def __init__(self, voices):
        self._voices = voices
        self.said = []
        self.props = {}
        self.stopped = False

    def getProperty(self, name):
        return self._voices if name == "voices" else self.props.get(name)

    def setProperty(self, name, value):
        self.props[name] = value

    def say(self, text):
        self.said.append(text)

    def runAndWait(self):
        pass

    def stop(self):
        self.stopped = True


class FakeVoice:
    def __init__(self, name, lang, gender="Female"):
        self.name = name
        self.id = name
        self.languages = [lang.encode()]
        self.gender = gender


class FakeTTS:
    """Minimal speak()-only fake for fallback-selection tests."""
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def speak(self, text):
        self.calls.append(text)
        return self.result

    def stop(self):
        pass


def test_edge_tts_synthesise_writes_mp3_and_maps_settings(tmp_path):
    FakeCommunicate.instances.clear()
    tts = EdgeTTS(voice_name="en-GB-SoniaNeural", rate=5, volume=50,
                  communicate_cls=FakeCommunicate)
    out = tmp_path / "out.mp3"
    path = tts.synthesize_sync("hello", out)
    assert path == out and out.read_bytes() == b"fake-mp3:hello"
    comm = FakeCommunicate.instances[-1]
    assert comm.voice == "en-GB-SoniaNeural"
    assert comm.rate == "+50%" and comm.volume == "-50%"
    assert tts.last_path == out


def test_edge_tts_empty_text_returns_none(tmp_path):
    tts = EdgeTTS(communicate_cls=FakeCommunicate)
    assert tts.synthesize_sync("   ") is None
    assert tts.speak("") is False


def test_edge_tts_speak_with_player(tmp_path):
    played = []
    tts = EdgeTTS(communicate_cls=FakeCommunicate,
                  player=lambda p: played.append(p) or True)
    assert tts.speak("hi") is True
    assert played and played[0].read_bytes() == b"fake-mp3:hi"


def test_edge_tts_speak_without_player_returns_true(tmp_path):
    # No player: synthesis succeeded, the UI plays last_path itself.
    tts = EdgeTTS(communicate_cls=FakeCommunicate)
    assert tts.speak("hi") is True
    assert tts.last_path is not None


def test_edge_tts_failed_synthesis_returns_false():
    class Boom:
        def __init__(self, *a, **k):
            pass

        async def save(self, path):
            raise RuntimeError("no network")

    tts = EdgeTTS(communicate_cls=Boom)
    assert tts.speak("hi") is False
    assert tts.synthesize_sync("hi") is None


def test_edge_tts_stop_is_safe_when_idle():
    tts = EdgeTTS(communicate_cls=FakeCommunicate)
    tts.stop()  # must not raise; nothing in flight


def test_sapi_prefers_hazel_over_other_female_gb():
    voices = [
        FakeVoice("Microsoft David Desktop", "en-US", "Male"),
        FakeVoice("Microsoft Susan Desktop", "en-GB", "Female"),
        FakeVoice("Microsoft Hazel Desktop", "en-GB", "Female"),
    ]
    engine = FakeSapiEngine(voices)
    sapi = SapiTTS(engine_factory=lambda: engine)
    assert sapi.selected_voice == "Microsoft Hazel Desktop"
    assert sapi.is_available is True


def test_sapi_falls_back_when_no_hazel():
    voices = [FakeVoice("Microsoft David Desktop", "en-US", "Male")]
    engine = FakeSapiEngine(voices)
    sapi = SapiTTS(engine_factory=lambda: engine)
    assert sapi.selected_voice == "Microsoft David Desktop"


def test_sapi_speak_and_stop():
    engine = FakeSapiEngine([FakeVoice("Microsoft Hazel Desktop", "en-GB")])
    sapi = SapiTTS(engine_factory=lambda: engine)
    assert sapi.speak("hello") is True
    assert engine.said == ["hello"]
    sapi.stop()
    assert engine.stopped is True
    assert sapi.speak("  ") is False


def test_sapi_missing_engine_never_raises():
    def boom():
        raise ImportError("no pyttsx3 here")

    sapi = SapiTTS(engine_factory=boom)
    assert sapi.is_available is False
    assert sapi.speak("hello") is False
    sapi.stop()  # must not raise


def test_speak_with_fallback_prefers_edge():
    edge, sapi = FakeTTS(True), FakeTTS(True)
    assert speak_with_fallback("hi", edge=edge, sapi=sapi) is True
    assert edge.calls == ["hi"] and sapi.calls == []


def test_speak_with_fallback_falls_back_to_sapi():
    edge, sapi = FakeTTS(False), FakeTTS(True)
    assert speak_with_fallback("hi", edge=edge, sapi=sapi) is True
    assert sapi.calls == ["hi"]


def test_speak_with_fallback_both_fail():
    assert speak_with_fallback("hi", edge=FakeTTS(False),
                               sapi=FakeTTS(False)) is False
    assert speak_with_fallback("   ", edge=FakeTTS(True),
                               sapi=FakeTTS(True)) is False


# ----------------------------------------------------------------------
# STT: model handling + dictation with fakes
# ----------------------------------------------------------------------

def test_find_model_missing_and_present(tmp_path):
    assert find_model(tmp_path) is None
    model = tmp_path / "vosk-model-small-en-us-0.15"
    (model / "am").mkdir(parents=True)
    assert find_model(tmp_path) == model


def test_ensure_model_no_download_never_downloads(tmp_path):
    assert ensure_model(tmp_path, download=False) is None
    assert list(tmp_path.iterdir()) == []  # nothing created
    model = tmp_path / "vosk-model-small-en-us-0.15"
    (model / "am").mkdir(parents=True)
    assert ensure_model(tmp_path, download=False) == model


def test_model_missing_message_names_model_and_size(tmp_path):
    msg = model_missing_message(tmp_path)
    assert "vosk-model-small-en-us-0.15" in msg
    assert "40 MB" in msg
    assert str(tmp_path) in msg


class FakeRecognizer:
    """Scripted Vosk recognizer: (is_final, payload) per chunk."""
    def __init__(self, script):
        self._script = list(script)
        self._last = ('{"text": ""}', False)

    def AcceptWaveform(self, chunk):
        if not self._script:
            return False
        payload, final = self._script.pop(0)
        self._last = (payload, final)
        return final

    def Result(self):
        return self._last[0]

    def PartialResult(self):
        return self._last[0]

    def SetWords(self, value):
        pass


def _fake_audio(chunks):
    def factory(sample_rate):
        yield from chunks
    return factory


def test_dictation_yields_partial_then_final(tmp_path):
    model = tmp_path / "vosk-model-small-en-us-0.15"
    (model / "am").mkdir(parents=True)
    script = [
        ('{"partial": "hel", "text": ""}', False),
        ('{"text": "hello world", "result": ['
         '{"conf": 0.9, "word": "hello"}, '
         '{"conf": 0.8, "word": "world"}]}', True),
    ]
    d = VoskDictation(
        model_dir=tmp_path,
        recognizer_factory=lambda p, sr: FakeRecognizer(script),
        audio_stream_factory=_fake_audio([b"\x00\x00" * 10] * 2),
    )
    events = list(d.transcribe())
    # Empty partials are skipped; only the final event survives here.
    assert events == [RecognitionEvent(text="hello world", is_final=True,
                                       confidence=pytest.approx(0.85))]


def test_dictation_partial_events_yielded(tmp_path):
    model = tmp_path / "vosk-model-small-en-us-0.15"
    (model / "am").mkdir(parents=True)
    script = [('{"text": "hel"}', False),
              ('{"text": "hello"}', True)]
    d = VoskDictation(
        model_dir=tmp_path,
        recognizer_factory=lambda p, sr: FakeRecognizer(script),
        audio_stream_factory=_fake_audio([b"\x00\x00" * 10] * 2),
    )
    events = list(d.transcribe())
    assert events[0] == RecognitionEvent(text="hel", is_final=False,
                                         confidence=None)
    assert events[1].text == "hello" and events[1].is_final is True


def test_listen_once_returns_final_text(tmp_path):
    model = tmp_path / "vosk-model-small-en-us-0.15"
    (model / "am").mkdir(parents=True)
    script = [('{"text": "open notepad"}', True)]
    d = VoskDictation(
        model_dir=tmp_path,
        recognizer_factory=lambda p, sr: FakeRecognizer(script),
        audio_stream_factory=_fake_audio([b"\x00\x00" * 10]),
    )
    # Returned to the caller only — listen_once sends nothing anywhere.
    assert d.listen_once(timeout_s=5.0) == "open notepad"


def test_dictation_without_model_yields_nothing(tmp_path):
    d = VoskDictation(model_dir=tmp_path)
    assert list(d.transcribe()) == []
    assert d.last_error is not None
    assert "vosk-model-small-en-us-0.15" in d.last_error
    assert d.listen_once() == ""


# ----------------------------------------------------------------------
# Wake word: policy wiring with fake recogniser results
# ----------------------------------------------------------------------

def _listener(tmp_path, calls, **kw):
    model = tmp_path / "vosk-model-small-en-us-0.15"
    (model / "am").mkdir(parents=True)
    return WakeWordListener(lambda: calls.append("wake"), model_dir=tmp_path, **kw)


def test_wake_word_accepts_exact_word_high_confidence(tmp_path):
    calls = []
    listener = _listener(tmp_path, calls)
    assert listener.handle_result("athena", 0.9) is True
    assert calls == ["wake"]  # on_wake invoked exactly once


def test_wake_word_rejects_low_confidence_and_phrases(tmp_path):
    calls = []
    listener = _listener(tmp_path, calls)
    assert listener.handle_result("athena", 0.5) is False
    assert listener.handle_result("athena please", 0.95) is False
    assert listener.handle_result("", 1.0) is False
    assert listener.handle_result("Athena!", 0.9) is True  # normalised
    assert calls == ["wake"]


def test_wake_word_cooldown_blocks_second_trigger(tmp_path):
    calls = []
    policy = WakeWordPolicy()
    listener = _listener(tmp_path, calls, policy=policy)
    now = datetime.now(timezone.utc)
    assert policy.try_trigger("athena", 0.9, now) is True
    # Same policy, immediate second result: cooldown rejects it.
    assert listener.handle_result("athena", 0.95) is False
    assert len(calls) == 0
    later = now + timedelta(seconds=4)
    assert policy.try_trigger("athena", 0.9, later) is True


def test_wake_word_start_fails_without_model(tmp_path):
    calls = []
    listener = WakeWordListener(lambda: calls.append("wake"), model_dir=tmp_path)
    assert listener.start() is False
    assert listener.last_error is not None
    assert "vosk-model-small-en-us-0.15" in listener.last_error
    assert listener.is_running is False


def test_wake_word_start_and_stop_with_fake_audio(tmp_path):
    calls = []
    model = tmp_path / "vosk-model-small-en-us-0.15"
    (model / "am").mkdir(parents=True)
    script = [('{"text": "athena", "result": [{"conf": 0.95}]}', True)]
    listener = WakeWordListener(
        lambda: calls.append("wake"),
        model_dir=tmp_path,
        recognizer_factory=lambda p, sr, g: FakeRecognizer(script),
        audio_stream_factory=_fake_audio([b"\x00\x00" * 10]),
    )
    assert listener.start() is True
    listener._thread.join(timeout=5.0)
    assert calls == ["wake"]
    listener.stop()
    assert listener.is_running is False


def test_wake_word_pause_resume_and_dictation_suspension(tmp_path):
    calls = []
    control = ListeningControl(enabled=True)
    listener = _listener(tmp_path, calls, control=control)
    assert control.is_listening is True
    listener.pause()
    assert control.is_listening is False
    assert control.enabled is True  # pause never touches the setting
    listener.resume()
    assert control.is_listening is True
    with listener.suspend_for_dictation():
        assert control.is_listening is False
    assert control.is_listening is True


def test_wake_word_disabled_control_never_listens(tmp_path):
    calls = []
    control = ListeningControl(enabled=False)
    listener = _listener(tmp_path, calls, control=control)
    assert listener.is_listening is False
    # handle_result still gates on the policy only; the control gates
    # the audio loop, not the (test-only) direct result path.
    assert listener.handle_result("athena", 0.9) is True


# ----------------------------------------------------------------------
# Greeting
# ----------------------------------------------------------------------

def test_should_greet_only_wake_word():
    assert should_greet(ShowReason.WAKE_WORD, True) is True
    assert should_greet(ShowReason.WAKE_WORD, False) is False
    assert should_greet(ShowReason.LAUNCH, True) is False
    assert should_greet(ShowReason.TRAY_RESTORE, True) is False


def test_greet_speaks_exact_text_and_reports_success():
    tts = FakeTTS(True)
    assert greet(tts) is True
    assert tts.calls == ["Welcome, boss."]
    assert GREETING_TEXT == "Welcome, boss."


def test_greet_returns_false_and_shows_text_fallback_when_speech_fails():
    assert greet(FakeTTS(False)) is False
    assert greet(None) is False


def test_greet_respects_setting_off():
    tts = FakeTTS(True)
    settings = VoiceSettings(greet_on_wake_word=False)
    assert greet(tts, settings) is False
    assert tts.calls == []  # never even attempted


def test_greet_if_wake_word_only_on_wake_word():
    tts = FakeTTS(True)
    assert greet_if_wake_word(tts, ShowReason.WAKE_WORD) is True
    assert greet_if_wake_word(tts, ShowReason.LAUNCH) is False
    assert greet_if_wake_word(tts, ShowReason.TRAY_RESTORE) is False
    assert tts.calls == ["Welcome, boss."]  # spoken exactly once
