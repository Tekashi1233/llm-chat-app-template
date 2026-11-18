"""ATHENA Python v1 — the arc reactor widget.

Port of the reactor in ``MainWindow.xaml``: concentric steel rings,
a segmented (dashed) coil ring, and a glowing blue-white core, with
a soft glow ellipse behind it. It lights up bright when ATHENA
replies, pulses gently while she thinks/speaks, and rests dim
when idle. All glow changes are slow fades — no flashing.

Brightness is driven by :meth:`ArcReactor.set_presence` with one
of the presence states (see :mod:`athena.interfaces.desktop.presence`). The widget
itself never decides the state — the shell does.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QPainter,
    QPen,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from .presence import FLASH_BRIGHTNESS, brightness_for

__all__ = ["ArcReactor"]


class ArcReactor(QWidget):
    """Arc reactor: steel rings + glowing core.

    ``size`` is the widget's square side in pixels (260 idle, 84 in
    conversation in the C# app). ``set_presence(state)`` drives the
    brightness; :meth:`flash` is the reply flash — bright for two
    seconds, then back to whatever the presence says.
    """

    def __init__(self, size: int = 260, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._size = size
        self._presence = "idle"
        self._glow = 0.25
        self._core = 0.55
        self._blue = 0.16
        self._red = 0.16
        self._pulse_phase = False
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._on_pulse_tick)
        self._flash_timer = QTimer(self)
        self._flash_timer.setSingleShot(True)
        self._flash_timer.timeout.connect(self._end_flash)
        self.setToolTip("Click to return to the idle screen")

    # -- public API ------------------------------------------------------

    @property
    def presence(self) -> str:
        return self._presence

    @property
    def brightness(self) -> tuple[float, float, float, float]:
        """Current (glow, core, blue, red) opacities — for tests."""
        return (self._glow, self._core, self._blue, self._red)

    def set_presence(self, state: str) -> None:
        """Drive the brightness from a presence state.

        Thinking and Speaking run a slow pulse timer (1.2s / 1.6s,
        as in C#); every other state is a single static level.
        """
        state = state.lower()
        self._presence = state
        self._pulse_timer.stop()
        target = brightness_for(state)
        if target.pulse:
            interval = 1200 if state == "thinking" else 1600
            self._pulse_timer.start(interval)
            self._pulse_phase = False
            self._on_pulse_tick()
            return
        self._apply(target.glow, target.core, target.blue, target.red)

    def flash(self) -> None:
        """Reply flash: lights up bright, then settles back after 2s.

        C# MainWindow.FlashReactor: glow 0.95 / core 1.0, then
        ApplyPresence restores the presence level.
        """
        self._pulse_timer.stop()
        self._apply(
            FLASH_BRIGHTNESS.glow,
            FLASH_BRIGHTNESS.core,
            FLASH_BRIGHTNESS.blue,
            FLASH_BRIGHTNESS.red,
        )
        self._flash_timer.start(2000)

    def set_size(self, size: int) -> None:
        self._size = size
        self.setFixedSize(size, size)
        self.update()

    # -- internals ---------------------------------------------------------

    def _end_flash(self) -> None:
        self.set_presence(self._presence)

    def _on_pulse_tick(self) -> None:
        self._pulse_phase = not self._pulse_phase
        if self._presence == "thinking":
            # C# Thinking: blue and red trade prominence; the
            # reactor breathes 0.35 <-> 0.60.
            if self._pulse_phase:
                self._apply(0.35, 0.65, 0.14, 0.42)
            else:
                self._apply(0.60, 0.65, 0.42, 0.14)
        else:  # speaking: slow blue pulse, reactor 0.50 <-> 0.85
            if self._pulse_phase:
                self._apply(0.85, 0.85, 0.50, 0.16)
            else:
                self._apply(0.50, 0.85, 0.30, 0.16)

    def _apply(self, glow: float, core: float, blue: float, red: float) -> None:
        self._glow = glow
        self._core = core
        self._blue = blue
        self._red = red
        self.update()

    # -- painting ----------------------------------------------------------

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        side = min(self.width(), self.height())
        cx = cy = side / 2.0

        # Glow ellipse behind the rings (bigger than the widget's
        # rings; clipped by the widget bounds, as in the XAML where
        # the 420px glow sits inside a 260px grid).
        glow_r = side * 0.80
        glow_grad = QRadialGradient(cx, cy, glow_r)
        glow_grad.setColorAt(0.0, QColor(127, 179, 255, int(255 * self._glow)))
        glow_grad.setColorAt(0.4, QColor(46, 90, 172, int(200 * self._glow)))
        glow_grad.setColorAt(1.0, QColor(46, 90, 172, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(glow_grad))
        painter.drawEllipse(int(cx - glow_r), int(cy - glow_r),
                            int(glow_r * 2), int(glow_r * 2))

        ring_r = side * 0.5 - 2
        # Outer steel ring.
        painter.setPen(QPen(QColor("#7A8A99"), 3))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(int(cx - ring_r), int(cy - ring_r),
                            int(ring_r * 2), int(ring_r * 2))
        # Segmented coil ring (dashed, thicker).
        coil_r = ring_r - side * 0.055
        coil_pen = QPen(QColor("#9FB4C7"), max(4, int(side * 0.046)))
        coil_pen.setDashPattern([10, 7])
        painter.setPen(coil_pen)
        painter.drawEllipse(int(cx - coil_r), int(cy - coil_r),
                            int(coil_r * 2), int(coil_r * 2))
        # Inner thin ring.
        inner_r = ring_r - side * 0.146
        painter.setPen(QPen(QColor("#5A6B7A"), 2))
        painter.drawEllipse(int(cx - inner_r), int(cy - inner_r),
                            int(inner_r * 2), int(inner_r * 2))

        # Glowing core: radial gradient, white centre -> blue edge.
        core_r = ring_r - side * 0.20
        core_grad = QRadialGradient(cx, cy, core_r)
        core_grad.setColorAt(0.0, QColor(255, 255, 255, int(255 * self._core)))
        core_grad.setColorAt(0.35, QColor(191, 224, 255, int(255 * self._core)))
        core_grad.setColorAt(0.70, QColor(46, 90, 172, int(255 * self._core)))
        core_grad.setColorAt(1.0, QColor(46, 90, 172, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(core_grad))
        painter.drawEllipse(int(cx - core_r), int(cy - core_r),
                            int(core_r * 2), int(core_r * 2))

        # Bright centre dot.
        dot_r = max(3.0, side * 0.05)
        painter.setBrush(QBrush(QColor(255, 255, 255, int(255 * self._core))))
        painter.drawEllipse(int(cx - dot_r), int(cy - dot_r),
                            int(dot_r * 2), int(dot_r * 2))
