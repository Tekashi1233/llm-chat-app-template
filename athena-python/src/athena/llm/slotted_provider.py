"""ATHENA Python v1 — slotted LLM provider (Worker → Ollama fallback).

Port of Friday's ``friday/llm/slotted_provider.py`` design, adapted
to ATHENA's two slots:

* Slot 1 — the Cloudflare Worker (``WorkerClient``). Cheap local
  availability check: a site key or owner token is stored, with
  zero network traffic.
* Slot 2 — local Ollama (``OllamaClient``). The always-available
  last resort.

Fallback triggers (only these): HTTP 429, HTTP 5xx, connection
error, timeout. A 400/401 is NOT a fallback trigger — a wrong or
missing key is a real problem the user must see, not something to
hide behind the local model.

:func:`SlottedProvider.generate` NEVER raises: it always returns a
``(text, slot_used, fallback_note)`` tuple, even when both slots
fail — then the text is an honest plain-language error.

On the Ollama path the model is asked to reason inside a
``<thought>…</thought>`` block first and then answer; the block is
stripped before display (see :func:`athena.reasoning.chain_of_thought.strip_thought_block`), so the
user only ever sees the answer.
"""

from __future__ import annotations

import logging
from typing import Protocol

from athena.reasoning.chain_of_thought import (
    build_thought_instruction,
    strip_thought_block,
)

from athena.llm.ollama_client import OllamaClient, OllamaError
from athena.llm.worker_client import (
    AthenaApiError,
    WorkerClient,
)

__all__ = [
    "OllamaSlot",
    "SlottedProvider",
    "WorkerSlot",
    "strip_thought_block",
]

logger = logging.getLogger(__name__)

#: HTTP statuses on Slot 1 that trigger a fall back to Ollama.
_FALLBACK_STATUSES = frozenset({429, 500, 502, 503, 504})

class _Slot(Protocol):
    """The interface both slots implement (mirrors Friday's LLMProvider)."""

    @property
    def name(self) -> str: ...

    @property
    def is_available(self) -> bool: ...

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float = 60.0,
    ) -> str: ...


class WorkerSlot:
    """Slot 1: the Cloudflare Worker, via :class:`WorkerClient`.

    ``is_available`` is a CHEAP LOCAL CHECK — it returns False when
    no site key and no owner token is stored, with no network call
    (same rule as Friday's APISlot).
    """

    def __init__(
        self,
        client: WorkerClient,
        conversation_id: str,
        *,
        via_voice: bool = False,
    ) -> None:
        self._client = client
        self.conversation_id = conversation_id
        self.via_voice = via_voice

    @property
    def name(self) -> str:
        return "ATHENA cloud (Worker)"

    @property
    def is_available(self) -> bool:
        return bool(
            (self._client.api_key and self._client.api_key.strip())
            or (self._client.auth_token and self._client.auth_token.strip())
        )

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float = 60.0,
    ) -> str:
        # The Worker loads this conversation's history itself, so
        # only the latest user message is sent. temperature /
        # max_tokens / timeout are accepted for the shared slot
        # interface; the Worker controls its own sampling.
        last_user = next(
            (m for m in reversed(messages) if m.get("role") == "user"),
            None,
        )
        if last_user is None or not str(last_user.get("content", "")).strip():
            raise ValueError("No user message to send.")
        return self._client.send_message(
            str(last_user["content"]),
            self.conversation_id,
            via_voice=self.via_voice,
        )


class OllamaSlot:
    """Slot 2: local Ollama, via :class:`OllamaClient`.

    Always the last-resort fallback. Adds the ``<thought>`` system
    instruction and strips the thought block before returning.
    """

    def __init__(self, client: OllamaClient | None = None) -> None:
        self._client = client or OllamaClient()

    @property
    def name(self) -> str:
        return f"{self._client.model} (Ollama)"

    @property
    def is_available(self) -> bool:
        return True  # Ollama is always the last-resort fallback

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float = 60.0,
    ) -> str:
        with_thought = [
            {"role": "system", "content": build_thought_instruction()},
            *messages,
        ]
        raw = self._client.generate(
            with_thought,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        return strip_thought_block(raw)


class SlottedProvider:
    """Two-slot LLM provider with automatic fallback.

    This is the ONLY object the UI (and voice/tools layers) should
    hold for chat. Callers never need to know which slot served a
    request — :meth:`generate` reports it in the return tuple.

    Args:
        slot1: Worker slot (or None to run Ollama-only, e.g. when
            no key is stored yet).
        slot2: Ollama slot. Defaults to a fresh :class:`OllamaSlot`.
    """

    def __init__(
        self,
        slot1: WorkerSlot | None,
        slot2: OllamaSlot | None = None,
    ) -> None:
        self._slot1 = slot1
        self._slot2 = slot2 or OllamaSlot()
        self._last_used = ""

    @property
    def name(self) -> str:
        return self.active_slot_name

    @property
    def active_slot_name(self) -> str:
        """Name of the slot that would be tried first right now."""
        if self._slot1 is not None and self._slot1.is_available:
            return self._slot1.name
        return self._slot2.name

    @property
    def slot1_available(self) -> bool:
        """Cheap local check: is Slot 1 (Worker) usable right now?

        True only when a Worker slot is configured and reports
        itself available (a key/token is stored). No network call.
        This is the single fact :class:`athena.llm.modes.ModeSwitch`
        reads — there is no second availability probe.
        """
        return self._slot1 is not None and self._slot1.is_available

    @property
    def is_available(self) -> bool:
        return True  # Ollama always available as last resort

    def last_used(self) -> str:
        """Which slot served the most recent request ("" before the first)."""
        return self._last_used

    def get_status(self) -> dict[str, object]:
        """Diagnostic summary — useful for a status view."""
        return {
            "slot1": self._slot1.name if self._slot1 else "disabled",
            "slot1_available": self._slot1.is_available if self._slot1 else False,
            "slot2": self._slot2.name,
            "last_used": self._last_used or "none yet",
        }

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float = 60.0,
    ) -> tuple[str, str, str | None]:
        """Try Slot 1 (Worker); fall back to Slot 2 (Ollama).

        Returns ``(text, slot_used, fallback_note)`` where
        ``slot_used`` is ``"worker"``, ``"ollama"`` or ``"none"``,
        and ``fallback_note`` explains a fallback (or None when
        Slot 1 served directly). NEVER raises.
        """
        # ── Slot 1: Worker ──────────────────────────────────────────
        if self._slot1 is not None and self._slot1.is_available:
            try:
                result = self._slot1.generate(
                    messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
                self._last_used = "worker"
                return result, "worker", None
            except AthenaApiError as exc:
                if exc.status_code in _FALLBACK_STATUSES or exc.status_code == 0:
                    logger.warning(
                        "[SlottedProvider] Worker failed "
                        f"(HTTP {exc.status_code}) — falling back to Ollama"
                    )
                else:
                    # 400/401 etc: a real problem (e.g. wrong key).
                    # Do not hide it behind the local model.
                    self._last_used = "worker"
                    return str(exc), "worker", None
            except (ValueError, Exception) as exc:  # noqa: BLE001 — never raise
                logger.warning(
                    "[SlottedProvider] Worker slot failed "
                    f"({type(exc).__name__}: {exc}) — falling back to Ollama"
                )

        # ── Slot 2: Ollama fallback ─────────────────────────────────
        try:
            result = self._slot2.generate(
                messages,
                temperature=temperature,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            self._last_used = "ollama"
            note = (
                "Answered by the on-device model because the cloud "
                "service was unavailable."
                if self._slot1 is not None and self._slot1.is_available
                else "Answered by the on-device model (cloud not configured)."
            )
            return result, "ollama", note
        except OllamaError as exc:
            logger.warning(f"[SlottedProvider] Ollama failed: {exc}")
            self._last_used = "none"
            return str(exc), "none", None
        except Exception as exc:  # noqa: BLE001 — never raise
            logger.warning(
                f"[SlottedProvider] Ollama slot failed unexpectedly: {exc}"
            )
            self._last_used = "none"
            return (
                "Sorry, I couldn't answer that — both the cloud service "
                "and the on-device model are unavailable right now.",
                "none",
                None,
            )
