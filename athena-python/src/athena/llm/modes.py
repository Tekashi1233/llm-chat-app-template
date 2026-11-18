"""ATHENA Python v1 — ONE online/offline mode switch.

The single switch the whole app reads. It is derived from the
slotted provider — the same mechanism that decides which slot
serves a request — and NOT a second switching system.

Idea borrowed from jarvis-exp's ``pipeline/output/mode_selector.py``
(auto-switch on connectivity, offline always works) and its
``mode_manager`` shim, but without LiveKit, without a connectivity
monitor, and without a second source of truth: there is no
background prober here. The mode is whatever the slotted provider
would try first right now:

* ``ONLINE`` — Slot 1 (Worker) is available: a site key or owner
  token is stored. The cloud serves; Ollama is the fallback.
* ``OFFLINE`` — Slot 1 is not available (no key/token stored, or
  the provider reports it down). The local model serves.

Listeners can subscribe via :meth:`ModeSwitch.on_change` and are
called (in registration order) whenever :meth:`ModeSwitch.refresh`
sees the mode flip. Callbacks must never raise into the caller —
a failing listener is logged, not propagated.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from enum import Enum

__all__ = ["Mode", "ModeSwitch"]

logger = logging.getLogger(__name__)


class Mode(Enum):
    ONLINE = "online"
    OFFLINE = "offline"


class ModeSwitch:
    """Derives the app mode from a slotted provider.

    Args:
        provider: A :class:`athena.llm.SlottedProvider` (or any
            object with ``active_slot_name`` and a ``_slot1``-style
            availability). The provider is the ONLY input — this
            class adds no network calls and no second probe.
    """

    def __init__(self, provider: object) -> None:
        self._provider = provider
        self._mode = self._derive()
        self._listeners: list[Callable[[Mode, Mode], None]] = []

    @property
    def mode(self) -> Mode:
        """Current mode (as of the last :meth:`refresh`)."""
        return self._mode

    @property
    def is_online(self) -> bool:
        return self._mode is Mode.ONLINE

    @property
    def is_offline(self) -> bool:
        return self._mode is Mode.OFFLINE

    def on_change(self, listener: Callable[[Mode, Mode], None]) -> None:
        """Register a ``listener(old_mode, new_mode)`` callback."""
        self._listeners.append(listener)

    def refresh(self) -> Mode:
        """Re-derive the mode from the provider; notify on a flip.

        Returns the (possibly new) mode. Cheap — no network call.
        """
        new_mode = self._derive()
        if new_mode is not self._mode:
            old_mode, self._mode = self._mode, new_mode
            for listener in list(self._listeners):
                try:
                    listener(old_mode, new_mode)
                except Exception:  # noqa: BLE001 — a listener must not break the switch
                    logger.exception("[ModeSwitch] mode-change listener failed")
        return self._mode

    def _derive(self) -> Mode:
        provider = self._provider
        slot1_available = getattr(provider, "slot1_available", None)
        if isinstance(slot1_available, bool):
            return Mode.ONLINE if slot1_available else Mode.OFFLINE
        # Provider without the slot1_available property (e.g. a
        # minimal fake): fall back to the active-slot name.
        active = getattr(provider, "active_slot_name", "")
        slot2_name = ""
        slot2 = getattr(provider, "_slot2", None)
        if slot2 is not None:
            slot2_name = getattr(slot2, "name", "")
        if active and slot2_name and active != slot2_name:
            return Mode.ONLINE
        return Mode.OFFLINE
