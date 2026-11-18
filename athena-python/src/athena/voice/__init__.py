"""ATHENA Python v1 — voice (TTS, dictation, wake word, greeting).

Female British identity. Primary TTS is Edge TTS neural en-GB
female (en-GB-SoniaNeural); offline fallback is SAPI Hazel.
Output is OFF by default. Dictation never auto-sends; the wake
word never sends anything either.
"""

from .greeting import (
    GREETING_TEXT,
    ShowReason,
    greet,
    greet_if_wake_word,
    should_greet,
)
from .settings import (
    DEFAULT_VOICE_NAME,
    HAZEL_VOICE_NAME,
    VoiceSettings,
    load_settings,
    save_settings,
)
from .stt import (
    MODEL_NAME,
    MODEL_URL,
    RecognitionEvent,
    VoskDictation,
    ensure_model,
    find_model,
    model_missing_message,
)
from .tts import EdgeTTS, SapiTTS, speak_with_fallback
from .wake_word import WAKE_WORD_GRAMMAR, WakeWordListener

__all__ = [
    "DEFAULT_VOICE_NAME",
    "GREETING_TEXT",
    "HAZEL_VOICE_NAME",
    "MODEL_NAME",
    "MODEL_URL",
    "WAKE_WORD_GRAMMAR",
    "EdgeTTS",
    "RecognitionEvent",
    "SapiTTS",
    "ShowReason",
    "VoiceSettings",
    "VoskDictation",
    "WakeWordListener",
    "ensure_model",
    "find_model",
    "greet",
    "greet_if_wake_word",
    "load_settings",
    "model_missing_message",
    "save_settings",
    "should_greet",
    "speak_with_fallback",
]
