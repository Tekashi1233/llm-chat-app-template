"""ATHENA Python v1 — presence → reactor brightness (pure, no Qt).

Port of the lighting table in ``MainWindow.xaml.cs`` ``ApplyPresence``:
the reactor's glow/core opacity and the blue/red background glows
follow the presence state. Only states real events produce ever
light up (C# PresenceStateMachine rule); this module only maps a
state to numbers — it never decides which state applies.

Brightness values are (glow_opacity, core_opacity, blue_glow, red_glow)
as in the C# code. ``flash`` is the reply flash: glow 0.95 / core 1.0
for a couple of seconds, then the presence values return.
"""

from __future__ import annotations

__all__ = [
    "PRESENCE_STATES",
    "PresenceBrightness",
    "brightness_for",
    "FLASH_BRIGHTNESS",
]

#: Presence states the reactor knows (C# PresenceState, minus
#: Approval which shares Working's lighting here).
PRESENCE_STATES = (
    "idle",
    "listening",
    "thinking",
    "speaking",
    "working",
    "approval",
    "offline",
    "error",
)


class PresenceBrightness:
    """Opacity tuple for one presence state (all 0.0–1.0)."""

    __slots__ = ("glow", "core", "blue", "red", "pulse")

    def __init__(
        self,
        glow: float,
        core: float,
        blue: float,
        red: float,
        pulse: bool = False,
    ) -> None:
        self.glow = glow
        self.core = core
        self.blue = blue
        self.red = red
        #: True when the C# code runs a slow timer for this state
        #: (Thinking cross-fades, Speaking pulses) instead of a
        #: single static level.
        self.pulse = pulse

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"PresenceBrightness(glow={self.glow}, core={self.core}, "
            f"blue={self.blue}, red={self.red}, pulse={self.pulse})"
        )


#: Reply flash (C# MainWindow.FlashReactor): lights up bright the
#: moment ATHENA answers, then settles back to the presence level.
FLASH_BRIGHTNESS = PresenceBrightness(glow=0.95, core=1.0, blue=0.45, red=0.16)

_BRIGHTNESS: dict[str, PresenceBrightness] = {
    # C# ApplyPresence: Idle — ReactorGlow 0.25, core 0.55, both 0.16.
    "idle": PresenceBrightness(glow=0.25, core=0.55, blue=0.16, red=0.16),
    # Listening — glow 0.60, core 0.75, blue 0.45.
    "listening": PresenceBrightness(glow=0.60, core=0.75, blue=0.45, red=0.16),
    # Thinking — slow cross-fade, reactor breathes 0.35 <-> 0.60.
    "thinking": PresenceBrightness(glow=0.48, core=0.65, blue=0.28, red=0.28, pulse=True),
    # Speaking — slow pulse, reactor 0.50 <-> 0.85, blue 0.30 <-> 0.50.
    "speaking": PresenceBrightness(glow=0.68, core=0.85, blue=0.40, red=0.16, pulse=True),
    # Working / Approval — both glows 0.30, reactor 0.45 / 0.70.
    "working": PresenceBrightness(glow=0.45, core=0.70, blue=0.30, red=0.30),
    "approval": PresenceBrightness(glow=0.45, core=0.70, blue=0.30, red=0.30),
    # Offline — nearly dark: reactor 0.08 / 0.15, glows 0.07.
    "offline": PresenceBrightness(glow=0.08, core=0.15, blue=0.07, red=0.07),
    # Error — red glow 0.35, reactor dim 0.10 / 0.25.
    "error": PresenceBrightness(glow=0.10, core=0.25, blue=0.10, red=0.35),
}


def brightness_for(state: str) -> PresenceBrightness:
    """Brightness for a presence state; unknown states fall back to idle.

    Accepts the C# enum names too (``"Idle"``, ``"Thinking"``, …).
    """
    return _BRIGHTNESS.get(state.lower(), _BRIGHTNESS["idle"])
