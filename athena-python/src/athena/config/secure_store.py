"""ATHENA Python v1 — secure local credential store.

Port of the C# ``Athena.Core.SecureStore`` (N1/N5-M2/N6):

* The site key and the owner session token live in
  ``%APPDATA%\\Athena\\config.json`` (``athena.config.CONFIG_PATH``),
  encrypted with Windows DPAPI (current-user scope), base64-encoded
  in the JSON. They are NEVER written in plain text.
* The conversation id is not a secret and is stored in plain text
  next to them.
* A corrupt, empty or unreadable config behaves as "nothing stored"
  instead of crashing startup — the backend still holds the
  conversation history, so the user just signs in again.

DPAPI is Windows-only. ``pywin32`` (``win32crypt``) is imported
LAZILY inside the Windows-only functions — it does not exist on
Linux and is never imported at module top level. On non-Windows,
or when decryption fails, loading returns ``None`` ("not
configured") and saving raises :class:`SecureStoreError` with a
clear message. Nothing here ever prints or logs a key or token.

The C# app writes PascalCase JSON keys (``ProtectedKey``,
``ConversationId``, ``ProtectedToken``) because its record has no
``JsonPropertyName`` attributes. This module reads those keys, and
also accepts snake_case (``protected_key`` …) defensively; it
writes PascalCase so both apps keep sharing one file.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import uuid
from pathlib import Path
from typing import Any, Protocol

from .settings import CONFIG_PATH

__all__ = [
    "NotConfiguredError",
    "SecureStore",
    "SecureStoreError",
]


class SecureStoreError(Exception):
    """A clear, user-facing secure-store failure (never carries a secret)."""


class NotConfiguredError(SecureStoreError):
    """No usable credential is stored, or this OS cannot protect one.

    Raised by :meth:`SecureStore.require_key` / ``require_token`` so
    callers can show the sign-in flow instead of a crash.
    """


class _Protector(Protocol):
    """Encrypt/decrypt bytes. DPAPI on Windows; injected fakes in tests."""

    def protect(self, plaintext: bytes) -> bytes: ...

    def unprotect(self, ciphertext: bytes) -> bytes: ...


class _DpapiProtector:
    """Windows DPAPI (current-user scope) via pywin32.

    ``win32crypt`` is imported here — inside the method, on first
    use — so importing this module on Linux never touches pywin32.
    """

    def protect(self, plaintext: bytes) -> bytes:
        import win32crypt  # noqa: PLC0415  (Windows-only, lazy)

        _desc, blob = win32crypt.CryptProtectData(
            plaintext, None, None, None, None, 0x01  # CRYPTPROTECT_UI_FORBIDDEN
        )
        return blob

    def unprotect(self, ciphertext: bytes) -> bytes:
        import win32crypt  # noqa: PLC0415  (Windows-only, lazy)

        _desc, plain = win32crypt.CryptUnprotectData(
            ciphertext, None, None, None, None, 0x01
        )
        return plain


def _default_protector() -> _Protector:
    if os.name != "nt":
        raise SecureStoreError(
            "Secure storage needs Windows DPAPI, which is not available "
            "on this system — the site key cannot be saved or loaded here."
        )
    return _DpapiProtector()


class SecureStore:
    """Reads/writes %APPDATA%\\Athena\\config.json (DPAPI-protected).

    Args:
        config_path: Override for tests / non-standard locations.
            Defaults to :data:`athena.config.CONFIG_PATH`.
        protector: Override for tests. Defaults to Windows DPAPI.
            Pass a fake protector to exercise save/load without Windows.
    """

    # JSON keys the C# app writes (PascalCase); snake_case accepted on read.
    _KEY_FIELDS = ("ProtectedKey", "protected_key")
    _TOKEN_FIELDS = ("ProtectedToken", "protected_token")
    _ID_FIELDS = ("ConversationId", "conversation_id")

    def __init__(
        self,
        config_path: str | Path | None = None,
        protector: _Protector | None = None,
    ) -> None:
        self._config_path = Path(config_path) if config_path else CONFIG_PATH
        self._protector = protector

    # -- public API -----------------------------------------------------

    @property
    def config_path(self) -> Path:
        return self._config_path

    def is_configured(self) -> bool:
        """True when a site key (or owner token) can actually be decrypted."""
        return self.try_load_key() is not None or self.try_load_token() is not None

    def try_load_key(self) -> str | None:
        """Return the stored site key, or None when none is stored.

        A corrupt config, undecryptable blob, or non-Windows system
        all behave as "no key stored" — never a crash, never a secret
        in the error.
        """
        return self._try_load(self._KEY_FIELDS)

    def try_load_token(self) -> str | None:
        """Return the stored owner session token, or None when not signed in."""
        return self._try_load(self._TOKEN_FIELDS)

    def require_key(self) -> str:
        """Return the site key, or raise :class:`NotConfiguredError`."""
        key = self.try_load_key()
        if key is None:
            raise NotConfiguredError(
                "No site key is stored yet — paste the site key to sign in."
            )
        return key

    def require_token(self) -> str:
        """Return the owner session token, or raise :class:`NotConfiguredError`."""
        token = self.try_load_token()
        if token is None:
            raise NotConfiguredError("Not signed in — no session token is stored.")
        return token

    def save_key(self, key: str) -> None:
        """Encrypt and store the site key, keeping token + conversation id."""
        self._save(key=key)

    def save_token(self, token: str) -> None:
        """Encrypt and store the owner session token, keeping key + id."""
        self._save(token=token)

    def delete_key(self) -> None:
        """Delete the stored site key (M2 migration: after a successful
        token exchange the key must not linger). Token and id are kept."""
        self._save(key=None, keep_key=False)

    def delete_token(self) -> None:
        """Delete the stored token (sign out). Key and id are kept."""
        self._save(token=None, keep_token=False)

    def get_or_create_conversation_id(self) -> str:
        """Return the stored conversation id, creating one if needed."""
        cfg = self._read_config()
        for field in self._ID_FIELDS:
            value = cfg.get(field)
            if isinstance(value, str) and value:
                try:
                    uuid.UUID(value)
                except ValueError:
                    continue
                return value
        new_id = str(uuid.uuid4())
        self._write_config({**cfg, "ConversationId": new_id})
        return new_id

    # -- internals ------------------------------------------------------

    def _protector_or_default(self) -> _Protector:
        if self._protector is not None:
            return self._protector
        return _default_protector()

    def _try_load(self, fields: tuple[str, ...]) -> str | None:
        try:
            cfg = self._read_config()
            for field in fields:
                raw = cfg.get(field)
                if not isinstance(raw, str) or not raw:
                    continue
                try:
                    cipher = base64.b64decode(raw, validate=True)
                except (binascii.Error, ValueError):
                    return None
                try:
                    plain = self._protector_or_default().unprotect(cipher)
                except Exception:
                    # Undecryptable (wrong user/machine, corrupt blob,
                    # or non-Windows) behaves as "not stored".
                    return None
                try:
                    return plain.decode("utf-8")
                except UnicodeDecodeError:
                    return None
            return None
        except SecureStoreError:
            return None

    def _save(
        self,
        *,
        key: str | None = None,
        token: str | None = None,
        keep_key: bool = True,
        keep_token: bool = True,
    ) -> None:
        protector = self._protector_or_default()
        cfg = self._read_config()
        out: dict[str, Any] = {}
        if key is not None:
            out["ProtectedKey"] = base64.b64encode(
                protector.protect(key.encode("utf-8"))
            ).decode("ascii")
        elif keep_key:
            for field in self._KEY_FIELDS:
                if cfg.get(field):
                    out["ProtectedKey"] = cfg[field]
                    break
        if token is not None:
            out["ProtectedToken"] = base64.b64encode(
                protector.protect(token.encode("utf-8"))
            ).decode("ascii")
        elif keep_token:
            for field in self._TOKEN_FIELDS:
                if cfg.get(field):
                    out["ProtectedToken"] = cfg[field]
                    break
        conv_id = next(
            (cfg[f] for f in self._ID_FIELDS if isinstance(cfg.get(f), str) and cfg[f]),
            None,
        )
        out["ConversationId"] = conv_id or str(uuid.uuid4())
        self._write_config(out)

    def _read_config(self) -> dict[str, Any]:
        """Never raises: a corrupt/empty/unreadable config is {}."""
        try:
            if not self._config_path.is_file():
                return {}
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _write_config(self, cfg: dict[str, Any]) -> None:
        self._config_path.parent.mkdir(parents=True, exist_ok=True)
        self._config_path.write_text(
            json.dumps(cfg, indent=2), encoding="utf-8"
        )
