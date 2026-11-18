"""ATHENA Python v1 — security filters for local logs and history.

Credential redaction for anything written to a local log or history
file: anything shaped like a key, token, or password is replaced
with ``<redacted>`` before writing. Heuristic, not complete — callers
must still avoid logging secrets in the first place.

Also: small input-validation helpers (message length, blank input).
"""

from __future__ import annotations

import re

__all__ = [
    "is_sensitive_key",
    "redact",
    "redact_mapping",
    "sanitize_for_log",
    "validate_message",
]

#: Longest user message the app will send (mirrors the Worker rule).
MAX_MESSAGE_LENGTH = 8000

_REDACTED = "<redacted>"

#: Keys whose VALUES are secrets, whatever the value looks like.
_SENSITIVE_KEY_RE = re.compile(
    r"(api[_-]?key|api[_-]?token|access[_-]?token|auth[_-]?token|"
    r"secret|password|passwd|pwd|bearer|session|cookie|private[_-]?key|"
    r"x[-_]?vesper[-_]?key|site[_-]?key|owner[_-]?token|client[_-]?secret)",
    re.IGNORECASE,
)

#: Value shapes that look like secrets on their own.
_VALUE_PATTERNS = [
    # sk-... / sk-proj-... style API keys
    re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b"),
    # Bearer <token>
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{8,}"),
    # AWS access key id
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    # key = "..." / token: '...' assignments (value redacted, key kept)
    re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd)\s*[:=]\s*['\"][^'\"]+['\"]"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd)\s*[:=]\s*\S+"),
    # long hex / base64-ish blobs (32+ chars) that smell like tokens
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    re.compile(r"\b[A-Za-z0-9_-]{40,}\b"),
]


def is_sensitive_key(name: str | None) -> bool:
    """True when ``name`` looks like a credential field name."""
    return bool(name) and bool(_SENSITIVE_KEY_RE.search(name))


def redact(text: str | None) -> str:
    """Redact credential-shaped values from ``text``.

    Returns "" for None/empty input. Never raises.
    """
    if not text:
        return ""
    out = str(text)
    for pattern in _VALUE_PATTERNS:
        out = pattern.sub(_REDACTED, out)
    return out


def redact_mapping(mapping: dict | None) -> dict:
    """Copy ``mapping`` with sensitive keys' values redacted.

    A value under a sensitive key name is always redacted, even when
    the value itself looks innocent; other values are passed through
    :func:`redact` as well.
    """
    if not mapping:
        return {}
    out: dict = {}
    for key, value in mapping.items():
        if is_sensitive_key(str(key)):
            out[key] = _REDACTED
        elif isinstance(value, str):
            out[key] = redact(value)
        elif isinstance(value, dict):
            out[key] = redact_mapping(value)
        else:
            out[key] = value
    return out


def sanitize_for_log(text: str | None) -> str:
    """Redact + single-line a value before writing it to a log."""
    return " ".join(redact(text).split())


def validate_message(text: str | None, *, max_length: int = MAX_MESSAGE_LENGTH) -> str | None:
    """Validate one user message.

    Returns an error string when the message is unusable, else None:
    blank messages and messages over ``max_length`` are refused.
    """
    if text is None or not str(text).strip():
        return "Message is empty."
    if len(str(text)) > max_length:
        return f"Message is over {max_length} characters."
    return None
