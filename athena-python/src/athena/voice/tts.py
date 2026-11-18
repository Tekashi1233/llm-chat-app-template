"""ATHENA Python v1 — text-to-speech.

Voice identity (fixed): female British.

* Primary: Edge TTS neural en-GB female (``en-GB-SoniaNeural``) —
  the engine approach is copied from Jarvis's ``TextToSpeech.py``
  (``edge_tts.Communicate(...).save(file)`` then play the mp3),
  but Jarvis's voice (``en-CA-LiamNeural``) is MALE Canadian and
  was deliberately NOT copied.
* Offline fallback: SAPI via pyttsx3, Hazel preferred — the same
  selection order as C# ``VoiceSelection`` (saved voice, Hazel,
  female en-GB, any en-GB, any female, first installed).

``EdgeTTS`` synthesises to a temp mp3 file and returns its path;
the UI layer plays it. ``speak()`` is the convenience wrapper
(synthesise, then play via an injected player when one is given).
Every public method returns ``False``/``None`` on failure instead
of raising, so the caller can show the text fallback.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import threading
from pathlib import Path
from typing import Callable

from .settings import DEFAULT_VOICE_NAME, HAZEL_VOICE_NAME, VoiceSettings

__all__ = [
    "DEFAULT_VOICE_NAME",
    "EdgeTTS",
    "SapiTTS",
    "speak_with_fallback",
]

logger = logging.getLogger("athena.voice.tts")

#: Player signature: takes the synthesised mp3 path, returns True
#: when playback started. The UI supplies this (e.g. a pygame /
#: QMediaPlayer wrapper); when None, ``speak()`` only synthesises
#: and returns True — the caller plays the file itself.
Player = Callable[[Path], bool]


def _edge_rate(rate: int) -> str:
    """SAPI rate -10..10 -> edge-tts rate string (``+0%`` style)."""
    return f"{max(-10, min(10, int(rate))) * 10:+d}%"


def _edge_volume(volume: int) -> str:
    """Volume 0..100 -> edge-tts volume string (100 -> ``+0%``)."""
    return f"{max(0, min(100, int(volume))) - 100:+d}%"


class EdgeTTS:
    """Online TTS via edge-tts (Microsoft Edge neural voices).

    Synthesises to a temp ``.mp3`` file; the UI layer plays it.
    Cancellable via :meth:`stop` (cancels the in-flight synthesis).
    Never raises from :meth:`speak` / :meth:`synthesize`.
    """

    def __init__(
        self,
        voice_name: str = DEFAULT_VOICE_NAME,
        rate: int = 0,
        volume: int = 100,
        player: Player | None = None,
        communicate_cls=None,
    ) -> None:
        self.voice_name = voice_name or DEFAULT_VOICE_NAME
        self.rate = max(-10, min(10, int(rate)))
        self.volume = max(0, min(100, int(volume)))
        self.player = player
        self._communicate_cls = communicate_cls  # injectable fake
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._current: asyncio.Future | None = None
        self._lock = threading.Lock()
        self._last_path: Path | None = None
        self._stopped = threading.Event()

    @classmethod
    def from_settings(
        cls, settings: VoiceSettings, player: Player | None = None
    ) -> "EdgeTTS":
        """Build from :class:`VoiceSettings` (voice/rate/volume)."""
        return cls(
            voice_name=settings.voice_name,
            rate=settings.rate,
            volume=settings.volume,
            player=player,
        )

    @property
    def last_path(self) -> Path | None:
        """Path of the most recently synthesised file, if any."""
        return self._last_path

    def _communicate(self, text: str):
        if self._communicate_cls is not None:
            return self._communicate_cls(
                text,
                self.voice_name,
                rate=_edge_rate(self.rate),
                volume=_edge_volume(self.volume),
            )
        import edge_tts  # lazy: Windows-only at runtime, absent in tests

        return edge_tts.Communicate(
            text,
            self.voice_name,
            rate=_edge_rate(self.rate),
            volume=_edge_volume(self.volume),
        )

    async def synthesize(
        self, text: str, output_path: str | Path | None = None
    ) -> Path | None:
        """Synthesise ``text`` to an mp3 file; return its path or None.

        ``output_path`` overrides the temp-file location. Returns
        None (never raises) on empty text, missing edge-tts, no
        network, or any synthesis error.
        """
        if not text or not text.strip():
            return None
        self._stopped.clear()
        path = (
            Path(output_path)
            if output_path is not None
            else Path(
                tempfile.NamedTemporaryFile(
                    suffix=".mp3", prefix="athena-tts-", delete=False
                ).name
            )
        )
        try:
            await self._communicate(text).save(str(path))
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # network down, edge-tts missing, ...
            logger.warning("Edge TTS synthesis failed: %s", exc)
            return None
        if self._stopped.is_set():
            return None
        self._last_path = path
        return path

    def synthesize_sync(
        self, text: str, output_path: str | Path | None = None
    ) -> Path | None:
        """Blocking wrapper around :meth:`synthesize` (UI-thread safe).

        Runs the coroutine on a dedicated worker loop so ``stop()``
        can cancel it from another thread, and so it works even when
        the caller already runs an event loop (e.g. Qt).
        """
        loop = self._ensure_loop()
        if loop is None:
            try:
                return asyncio.run(self.synthesize(text, output_path))
            except Exception as exc:
                logger.warning("Edge TTS synthesis failed: %s", exc)
                return None
        try:
            fut = asyncio.run_coroutine_threadsafe(
                self.synthesize(text, output_path), loop
            )
            with self._lock:
                self._current = fut
            return fut.result(timeout=120)
        except Exception as exc:
            logger.warning("Edge TTS synthesis failed: %s", exc)
            return None
        finally:
            with self._lock:
                self._current = None

    def speak(self, text: str) -> bool:
        """Synthesise ``text`` and play it (via ``player`` if given).

        Returns True when synthesis succeeded (and playback started,
        when a player is set). Returns False on any failure — the
        caller should show the text fallback. Never raises.
        """
        path = self.synthesize_sync(text)
        if path is None:
            return False
        if self.player is None:
            return True  # UI plays last_path itself
        try:
            return bool(self.player(path))
        except Exception as exc:
            logger.warning("TTS playback failed: %s", exc)
            return False

    def stop(self) -> None:
        """Cancel any in-flight synthesis. Safe to call any time."""
        self._stopped.set()
        with self._lock:
            fut = self._current
        if fut is not None and not fut.done():
            fut.cancel()

    def _ensure_loop(self) -> asyncio.AbstractEventLoop | None:
        with self._lock:
            if self._loop is not None and not self._loop.is_closed():
                return self._loop
            try:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(
                    target=loop.run_forever, daemon=True, name="athena-edge-tts"
                )
                thread.start()
                self._loop, self._loop_thread = loop, thread
                return loop
            except Exception as exc:
                logger.warning("Could not start TTS worker loop: %s", exc)
                return None


class SapiTTS:
    """Offline TTS fallback via SAPI (pyttsx3), Hazel preferred.

    Windows-only in practice (pyttsx3/SAPI); on a machine without
    pyttsx3 or without SAPI voices every method degrades to False
    instead of raising. Voice selection mirrors C# VoiceSelection:
    saved voice name, then Hazel, then female en-GB, then any
    en-GB, then any female, then the first installed voice.
    """

    def __init__(
        self,
        voice_name: str | None = None,
        rate: int = 0,
        volume: int = 100,
        engine_factory=None,
    ) -> None:
        self.voice_name = voice_name
        self.rate = max(-10, min(10, int(rate)))
        self.volume = max(0, min(100, int(volume)))
        self._engine_factory = engine_factory  # injectable fake
        self._engine = None
        self._engine_tried = False

    @classmethod
    def from_settings(cls, settings: VoiceSettings) -> "SapiTTS":
        """Build from :class:`VoiceSettings` (rate/volume only — the
        Edge voice name is not a SAPI voice; Hazel is preferred)."""
        return cls(rate=settings.rate, volume=settings.volume)

    @property
    def is_available(self) -> bool:
        """True when a SAPI engine with at least one voice exists."""
        return self._ensure_engine() is not None

    @property
    def selected_voice(self) -> str | None:
        """Name of the voice that would be used, or None."""
        engine = self._ensure_engine()
        if engine is None:
            return None
        voice = self._pick_voice(engine)
        return getattr(voice, "name", None) if voice is not None else None

    def _ensure_engine(self):
        if self._engine_tried:
            return self._engine
        self._engine_tried = True
        try:
            if self._engine_factory is not None:
                engine = self._engine_factory()
            else:
                import pyttsx3  # lazy: Windows-only, absent in tests

                engine = pyttsx3.init()
        except Exception as exc:
            logger.warning("SAPI engine unavailable: %s", exc)
            self._engine = None
            return None
        try:
            voice = self._pick_voice(engine)
            if voice is not None:
                engine.setProperty("voice", voice.id)
            # pyttsx3 rate is words/min (~200 default); SAPI -10..10
            # maps onto roughly 100..300 wpm.
            engine.setProperty("rate", 200 + self.rate * 10)
            engine.setProperty("volume", self.volume / 100.0)
        except Exception as exc:
            logger.warning("SAPI engine setup failed: %s", exc)
            self._engine = None
            return None
        self._engine = engine
        return engine

    @staticmethod
    def _voice_info(voice) -> tuple[str, str, str]:
        """(name, culture/lang, gender-ish) for a pyttsx3 voice."""
        name = getattr(voice, "name", "") or ""
        languages = getattr(voice, "languages", None) or []
        lang = ""
        if languages:
            first = languages[0]
            lang = first.decode(errors="ignore") if isinstance(first, bytes) else str(first)
        gender = getattr(voice, "gender", "") or ""
        return name, lang, gender

    def _pick_voice(self, engine):
        try:
            voices = list(engine.getProperty("voices") or [])
        except Exception:
            return None
        if not voices:
            return None

        def is_en_gb(v) -> bool:
            _, lang, _ = self._voice_info(v)
            return lang.replace("_", "-").lower().startswith("en-gb")

        def is_female(v) -> bool:
            _, _, gender = self._voice_info(v)
            return "female" in gender.lower()

        if self.voice_name:
            for v in voices:
                if self._voice_info(v)[0].lower() == self.voice_name.lower():
                    return v
        for v in voices:  # Hazel first (classic offline en-GB female)
            name, lang, _ = self._voice_info(v)
            if "hazel" in name.lower() and is_en_gb(v):
                return v
        for v in voices:
            if is_female(v) and is_en_gb(v):
                return v
        for v in voices:
            if is_en_gb(v):
                return v
        for v in voices:
            if is_female(v):
                return v
        return voices[0]

    def speak(self, text: str) -> bool:
        """Speak ``text`` via SAPI. Returns False on any failure."""
        if not text or not text.strip():
            return False
        engine = self._ensure_engine()
        if engine is None:
            return False
        try:
            engine.say(text)
            engine.runAndWait()
            return True
        except Exception as exc:
            logger.warning("SAPI speak failed: %s", exc)
            return False

    def stop(self) -> None:
        """Interrupt any speech in progress. Never raises."""
        if self._engine is None:
            return
        try:
            self._engine.stop()
        except Exception:
            pass


def speak_with_fallback(
    text: str,
    settings: VoiceSettings | None = None,
    *,
    edge: EdgeTTS | None = None,
    sapi: SapiTTS | None = None,
) -> bool:
    """Speak ``text``: Edge TTS first, SAPI fallback on failure.

    ``edge``/``sapi`` are injectable (fakes in tests); when omitted
    they are built from ``settings``. Returns True when either engine
    spoke, False when both failed (or text is empty) — the caller
    shows the text fallback. Never raises.
    """
    if not text or not text.strip():
        return False
    settings = settings or VoiceSettings()
    try:
        edge = edge or EdgeTTS.from_settings(settings)
        if edge.speak(text):
            return True
    except Exception as exc:
        logger.warning("Edge TTS speak failed: %s", exc)
    try:
        sapi = sapi or SapiTTS.from_settings(settings)
        return bool(sapi.speak(text))
    except Exception as exc:
        logger.warning("SAPI speak failed: %s", exc)
        return False
