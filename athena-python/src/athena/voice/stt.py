"""ATHENA Python v1 — speech-to-text (Vosk dictation, offline).

Dictation is local-only: the Vosk model runs on this PC, no audio
leaves the machine. Recognised text is RETURNED to the caller —
dictation NEVER auto-sends; the UI fills the input box only (the
same rule as C# ``VoiceService``: its ``Recognized`` event hands
text to the UI, which decides what to do with it).

Model: ``vosk-model-small-en-us-0.15`` (~40 MB) under
``VOSK_MODEL_DIR``. :func:`ensure_model` downloads it on first use
ONLY when the caller passes ``download=True`` — it never silently
downloads gigabytes, and it never raises: no model / no network
returns ``None`` with a clear message for the UI to show.
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import tempfile
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from ..config import VOSK_MODEL_DIR

__all__ = [
    "MODEL_NAME",
    "MODEL_URL",
    "RecognitionEvent",
    "VoskDictation",
    "ensure_model",
    "find_model",
    "model_missing_message",
]

logger = logging.getLogger("athena.voice.stt")

#: Small English model (~40 MB). Small on purpose: dictation does
#: not need the large model, and the download stays cheap.
MODEL_NAME = "vosk-model-small-en-us-0.15"
MODEL_URL = f"https://alphacephei.com/vosk/models/{MODEL_NAME}.zip"

#: Vosk result JSON always carries these keys for a final result.
SAMPLE_RATE = 16000


def model_missing_message(model_dir: Path | str = VOSK_MODEL_DIR) -> str:
    """Human-readable message for the UI when no model is present."""
    return (
        f"Speech recognition model not found in {model_dir}. "
        f"Download '{MODEL_NAME}' (~40 MB) once to enable dictation "
        "and the wake word — no audio leaves this PC."
    )


def find_model(model_dir: Path | str = VOSK_MODEL_DIR) -> Path | None:
    """Return the installed model dir, or None when it is absent.

    A model counts as installed when the directory exists and holds
    the acoustic model (``am/``) or a model config.
    """
    root = Path(model_dir) / MODEL_NAME
    if not root.is_dir():
        return None
    if (root / "am").is_dir() or (root / "conf" / "model.conf").is_file():
        return root
    return None


def ensure_model(
    model_dir: Path | str = VOSK_MODEL_DIR, *, download: bool = False
) -> Path | None:
    """Return the Vosk model dir, downloading it on first use.

    * Model already present -> returned immediately, no network.
    * Missing and ``download=False`` -> logs the missing-model
      message and returns None (caller shows it in the UI).
    * Missing and ``download=True`` -> downloads ``MODEL_URL``
      (~40 MB zip) to a temp file, extracts it under ``model_dir``,
      verifies it, and returns the path. Any network/zip error logs
      and returns None — never raises, never downloads anything
      bigger than the one small model.
    """
    found = find_model(model_dir)
    if found is not None:
        return found
    if not download:
        logger.warning(model_missing_message(model_dir))
        return None
    url = MODEL_URL
    logger.info("Downloading Vosk model %s (~40 MB) from %s", MODEL_NAME, url)
    tmp_zip: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix=".zip", prefix="vosk-model-", delete=False
        ) as fh:
            tmp_zip = Path(fh.name)
        with urllib.request.urlopen(url, timeout=120) as resp, open(
            tmp_zip, "wb"
        ) as out:
            shutil.copyfileobj(resp, out, length=1024 * 256)
        with zipfile.ZipFile(tmp_zip) as zf:
            zf.extractall(Path(model_dir))
        found = find_model(model_dir)
        if found is None:
            logger.warning("Vosk model download unpacked but not usable")
            return None
        return found
    except Exception as exc:
        logger.warning("Vosk model download failed (%s): %s", url, exc)
        return None
    finally:
        if tmp_zip is not None:
            try:
                tmp_zip.unlink(missing_ok=True)
            except OSError:
                pass


@dataclass
class RecognitionEvent:
    """One dictation event yielded by :meth:`VoskDictation.transcribe`.

    ``is_final=False`` is a partial (still changing) hypothesis;
    ``is_final=True`` is a completed utterance. ``confidence`` is
    the mean word confidence (0..1) when Vosk reports it, else None.
    The caller decides what to do with the text — nothing here
    ever sends it anywhere.
    """

    text: str
    is_final: bool
    confidence: float | None = None


def _parse_result(payload: str, *, final: bool) -> RecognitionEvent | None:
    try:
        data = json.loads(payload)
    except ValueError:
        return None
    text = (data.get("text") or "").strip()
    if not text:
        return None
    confs = [
        w.get("conf")
        for w in data.get("result", [])
        if isinstance(w, dict) and isinstance(w.get("conf"), (int, float))
    ]
    conf = sum(confs) / len(confs) if confs else None
    return RecognitionEvent(text=text, is_final=final, confidence=conf)


class VoskDictation:
    """Offline dictation over the microphone using Vosk.

    ``recognizer_factory`` / ``audio_stream_factory`` are injectable
    (fakes in tests). By default the recognizer is
    ``vosk.KaldiRecognizer`` and audio comes from ``sounddevice``
    (lazy import — PortAudio is a Windows-runtime dependency and is
    absent on this Linux VM, so importing this module never requires
    it). :meth:`transcribe` yields partial + final events; the UI
    fills the input box with the text and never auto-sends it.
    """

    def __init__(
        self,
        model_dir: Path | str = VOSK_MODEL_DIR,
        sample_rate: int = SAMPLE_RATE,
        recognizer_factory=None,
        audio_stream_factory=None,
    ) -> None:
        self.model_dir = Path(model_dir)
        self.sample_rate = sample_rate
        self._recognizer_factory = recognizer_factory
        self._audio_stream_factory = audio_stream_factory
        self.last_error: str | None = None

    @property
    def is_available(self) -> bool:
        """True when a model is installed and Vosk imports."""
        if find_model(self.model_dir) is None:
            return False
        try:
            import vosk  # noqa: F401  (lazy, Windows runtime dep)

            return True
        except Exception:
            return False

    def _make_recognizer(self, model_path: Path):
        if self._recognizer_factory is not None:
            return self._recognizer_factory(model_path, self.sample_rate)
        import vosk

        vosk.SetLogLevel(-1)
        model = vosk.Model(str(model_path))
        rec = vosk.KaldiRecognizer(model, self.sample_rate)
        rec.SetWords(True)  # word confidences for RecognitionEvent
        return rec

    def _audio_chunks(self) -> Iterable[bytes]:
        """Yield raw 16-bit mono PCM chunks from the microphone."""
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
            while True:
                data, _overflow = stream.read(4000)
                yield bytes(data)

    def transcribe(self) -> Iterator[RecognitionEvent]:
        """Yield partial + final dictation events until stopped.

        Yields nothing (and sets :attr:`last_error`) when the model
        or microphone is unavailable — never raises. The caller
        stops iterating to stop listening; recognised text is only
        ever yielded, never sent.
        """
        self.last_error = None
        model_path = find_model(self.model_dir)
        if model_path is None:
            self.last_error = model_missing_message(self.model_dir)
            logger.warning(self.last_error)
            return
        try:
            recognizer = self._make_recognizer(model_path)
        except Exception as exc:
            self.last_error = f"Could not start speech recognition: {exc}"
            logger.warning(self.last_error)
            return
        try:
            for chunk in self._audio_chunks():
                if not chunk:
                    continue
                try:
                    if recognizer.AcceptWaveform(chunk):
                        event = _parse_result(recognizer.Result(), final=True)
                        if event is not None:
                            yield event
                    else:
                        event = _parse_result(
                            recognizer.PartialResult(), final=False
                        )
                        if event is not None:
                            yield event
                except Exception as exc:
                    self.last_error = f"Recognition error: {exc}"
                    logger.warning(self.last_error)
                    return
        except Exception as exc:
            self.last_error = f"Microphone unavailable: {exc}"
            logger.warning(self.last_error)
            return

    def listen_once(self, timeout_s: float = 15.0) -> str:
        """Listen for one utterance; return its final text (or "").

        Returns the last partial text if the timeout expires before
        a final result. Never raises; "" means nothing was heard or
        recognition is unavailable (see :attr:`last_error`).
        """
        import time

        deadline = time.monotonic() + max(0.0, timeout_s)
        last_partial = ""
        for event in self.transcribe():
            if event.is_final:
                return event.text
            last_partial = event.text
            if time.monotonic() >= deadline:
                break
        return last_partial
