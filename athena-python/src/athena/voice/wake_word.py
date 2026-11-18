"""ATHENA Python v1 — wake-word listener (Vosk, offline).

Background thread listening for the single word "athena" with a
small Vosk grammar (``["athena"]``) — the offline counterpart of
C# ``WakeWordService`` (SAPI one-word grammar). Results are gated
by :class:`athena.config.policies.WakeWordPolicy` (exact word, confidence
>= 0.7, 3s cooldown) and by :class:`athena.config.policies.ListeningControl`
(persisted on/off + temporary pause) — the policies are the single
source of truth, this module only wires audio to them.

On a wake word the ``on_wake`` callback fires; the UI pops the
window up (and greets, see greeting.py). The listener NEVER sends
anything itself — no chat message, no model call, no memory write.

The listener pauses while dictation runs: call :meth:`pause`
before starting dictation and :meth:`resume` after (or use
:meth:`suspend_for_dictation` as a context manager) so the user's
own dictation cannot re-trigger the wake word.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from ..config import VOSK_MODEL_DIR
from ..config.policies import ListeningControl, WakeWordPolicy
from .stt import SAMPLE_RATE, find_model, model_missing_message

__all__ = ["WakeWordListener"]

logger = logging.getLogger("athena.voice.wake_word")

#: The Vosk grammar for the listener: one word only.
WAKE_WORD_GRAMMAR = '["athena"]'


class WakeWordListener:
    """Background wake-word listener.

    ``on_wake`` is called (from the listener thread — the UI must
    marshal to its own thread) when "athena" passes the policy
    gate. ``policy`` / ``control`` default to fresh instances; pass
    the app's shared ones so the Settings toggle and the tray menu
    control this listener. ``recognizer_factory`` /
    ``audio_stream_factory`` are injectable (fakes in tests).
    """

    def __init__(
        self,
        on_wake: Callable[[], None],
        model_dir: Path | str = VOSK_MODEL_DIR,
        sample_rate: int = SAMPLE_RATE,
        policy: WakeWordPolicy | None = None,
        control: ListeningControl | None = None,
        recognizer_factory=None,
        audio_stream_factory=None,
    ) -> None:
        self.on_wake = on_wake
        self.model_dir = Path(model_dir)
        self.sample_rate = sample_rate
        self.policy = policy or WakeWordPolicy()
        self.control = control or ListeningControl()
        self._recognizer_factory = recognizer_factory
        self._audio_stream_factory = audio_stream_factory
        self._thread: threading.Thread | None = None
        self._running = threading.Event()
        self.last_error: str | None = None

    @property
    def is_running(self) -> bool:
        """True while the listener thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def is_listening(self) -> bool:
        """True when the thread runs AND listening is enabled/unpaused."""
        return self.is_running and self.control.is_listening

    def start(self) -> bool:
        """Start the background thread. False when unavailable.

        False (never raises) when the model is missing, Vosk cannot
        be imported, or the microphone is unavailable — check
        :attr:`last_error` for the reason to show in the UI.
        """
        if self.is_running:
            return True
        self.last_error = None
        if find_model(self.model_dir) is None:
            self.last_error = model_missing_message(self.model_dir)
            logger.warning(self.last_error)
            return False
        self._running.set()
        thread = threading.Thread(
            target=self._run, daemon=True, name="athena-wake-word"
        )
        thread.start()
        self._thread = thread
        return True

    def stop(self) -> None:
        """Stop the listener thread (blocks briefly for it to exit)."""
        self._running.clear()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._thread = None

    def pause(self) -> None:
        """Temporarily pause (e.g. while dictation runs). In-memory
        only — never touches the persisted on/off setting."""
        self.control.pause()

    def resume(self) -> None:
        """Resume after a temporary pause."""
        self.control.resume()

    @contextmanager
    def suspend_for_dictation(self):
        """Pause the listener for a ``with`` block, resume after.

        Usage: ``with listener.suspend_for_dictation(): ...dictate...``
        """
        self.pause()
        try:
            yield
        finally:
            self.resume()

    def handle_result(self, text: str, confidence: float) -> bool:
        """Feed one recogniser result through the policy gate.

        Returns True when the wake word was accepted (and ``on_wake``
        was invoked). Pure wiring — no audio, no I/O — so tests drive
        it directly with fake results.
        """
        if not self.policy.try_trigger(text, confidence):
            return False
        try:
            self.on_wake()
        except Exception as exc:
            logger.warning("Wake-word callback failed: %s", exc)
        return True

    # -- internals ---------------------------------------------------

    def _make_recognizer(self, model_path: Path):
        if self._recognizer_factory is not None:
            return self._recognizer_factory(
                model_path, self.sample_rate, WAKE_WORD_GRAMMAR
            )
        import vosk

        vosk.SetLogLevel(-1)
        model = vosk.Model(str(model_path))
        rec = vosk.KaldiRecognizer(model, self.sample_rate, WAKE_WORD_GRAMMAR)
        rec.SetWords(True)
        return rec

    def _audio_chunks(self):
        if self._audio_stream_factory is not None:
            yield from self._audio_stream_factory(self.sample_rate)
            return
        import sounddevice as sd  # lazy: needs PortAudio (Windows)

        with sd.RawInputStream(
            samplerate=self.sample_rate,
            blocksize=4000,
            dtype="int16",
            channels=1,
        ) as stream:
            while self._running.is_set():
                data, _overflow = stream.read(4000)
                yield bytes(data)

    @staticmethod
    def _confidence_of(data: dict) -> float:
        words = data.get("result") or []
        confs = [
            w.get("conf")
            for w in words
            if isinstance(w, dict) and isinstance(w.get("conf"), (int, float))
        ]
        if confs:
            return sum(confs) / len(confs)
        # Grammar result with no word confidences: the word itself
        # matched the one-word grammar, so treat it as confident.
        return 1.0 if (data.get("text") or "").strip() else 0.0

    def _run(self) -> None:
        model_path = find_model(self.model_dir)
        if model_path is None:
            self.last_error = model_missing_message(self.model_dir)
            return
        try:
            recognizer = self._make_recognizer(model_path)
        except Exception as exc:
            self.last_error = f"Could not start wake-word listener: {exc}"
            logger.warning(self.last_error)
            return
        try:
            for chunk in self._audio_chunks():
                if not self._running.is_set():
                    break
                if not self.control.is_listening:
                    time.sleep(0.05)
                    continue
                if not chunk:
                    continue
                try:
                    if recognizer.AcceptWaveform(chunk):
                        try:
                            data = json.loads(recognizer.Result())
                        except ValueError:
                            continue
                        text = (data.get("text") or "").strip()
                        if text:
                            self.handle_result(
                                text, self._confidence_of(data)
                            )
                except Exception as exc:
                    self.last_error = f"Wake-word recognition error: {exc}"
                    logger.warning(self.last_error)
                    return
        except Exception as exc:
            self.last_error = f"Microphone unavailable: {exc}"
            logger.warning(self.last_error)
