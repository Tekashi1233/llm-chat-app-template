"""ATHENA Python v1 — live awareness (local clock, no network).

Local date/time plus a status snapshot for prompts and the UI.
Makes NO network calls.
"""

from __future__ import annotations

from athena.awareness.live_context import get_status, now_local, today_info

__all__ = ["get_status", "now_local", "today_info"]
