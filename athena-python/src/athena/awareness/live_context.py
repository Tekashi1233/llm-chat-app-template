"""ATHENA Python v1 — live context (local clock, no network).

Local date/time plus a status snapshot for the UI. Makes NO
network calls: the "laptop online" entry is a placeholder that
reports what is actually known locally (whether a site key is
stored), and slot availability comes from the slotted provider —
it is never probed here.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

__all__ = ["get_status", "now_local", "today_info"]


def now_local(tz_name: str | None = None) -> datetime:
    """Current local time as an aware datetime.

    ``tz_name`` is any IANA timezone (e.g. ``"Asia/Riyadh"``);
    None means the system local timezone.
    """
    tz = ZoneInfo(tz_name) if tz_name else None
    return datetime.now(tz)


def today_info(tz_name: str | None = None) -> dict[str, str]:
    """Day/date/time snapshot for prompts and the UI.

    Returns ``day`` (e.g. "Wednesday"), ``date`` (ISO, e.g.
    "2026-11-18"), ``time`` ("HH:MM", 24-hour), ``timezone``
    (IANA name or "local"), and ``iso`` (full ISO timestamp).
    """
    now = now_local(tz_name)
    tz = now.tzinfo
    tz_name_out = getattr(tz, "key", None) if tz is not None else None
    return {
        "day": now.strftime("%A"),
        "date": now.date().isoformat(),
        "time": now.strftime("%H:%M"),
        "timezone": tz_name_out or "local",
        "iso": now.isoformat(),
    }


def get_status(provider: object | None = None) -> dict[str, object]:
    """Status snapshot: slot availability + local clock.

    ``provider`` is a :class:`athena.llm.SlottedProvider` (or any
    object with ``get_status()``). ``laptop_online`` is honest
    about what is known without a network call: it is always
    ``"unknown"`` here — online-ness is derived from whether a
    slot actually answers, never probed separately.
    """
    status: dict[str, object] = {
        "laptop_online": "unknown",
        **today_info(),
    }
    if provider is not None and hasattr(provider, "get_status"):
        status["slots"] = provider.get_status()  # type: ignore[attr-defined]
    return status
