"""ATHENA Python v1 — system tray (N7/N8).

Port of the tray code in ``MainWindow.xaml.cs``: closing the window
hides it to the tray instead of quitting (real quit is only from
the tray menu), and the tray menu carries the listening controls.

The menu is the SAME switch as the Settings toggle, not a second
one: "Listening: On/Off" flips the persisted
``VoiceSettings.WakeWordEnabled``; "Pause/Resume listening" is a
temporary in-memory pause that never touches the persisted setting.
Labels are refreshed on every menu open, so they always reflect the
current state. One balloon tip on the first hide-to-tray.

The label logic is pure (:class:`TrayMenuState`, driven by the
shared :class:`~athena.config.policies.ListeningControl`) and testable
without Qt; :class:`AthenaTrayIcon` is the thin Qt shell around it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from ...config.policies import ListeningControl

__all__ = ["TrayMenuState", "AthenaTrayIcon"]


@dataclass(frozen=True)
class TrayMenuState:
    """The tray menu's labels for one moment in time.

    Computed fresh on every menu open (C# ``RefreshTrayMenuLabels``).
    """

    listening_label: str
    listening_checked: bool
    pause_label: str
    pause_checked: bool
    pause_enabled: bool

    @classmethod
    def from_listening(cls, listening: ListeningControl) -> "TrayMenuState":
        enabled = listening.enabled
        return cls(
            listening_label="Listening: On" if enabled else "Listening: Off",
            listening_checked=enabled,
            pause_label="Resume listening" if listening.paused else "Pause listening",
            pause_checked=listening.paused,
            pause_enabled=enabled,
        )


class AthenaTrayIcon:
    """QSystemTrayIcon wrapper. All PySide6 imports happen inside
    ``__init__``, so importing this module never needs Qt — only
    constructing the icon does.

    Callbacks (all injected, all optional):
    * ``on_show`` — tray "Show ATHENA" / double-click.
    * ``on_toggle_listening`` — "Listening: On/Off" clicked.
    * ``on_toggle_pause`` — "Pause/Resume listening" clicked.
    * ``on_quit`` — "Quit" clicked (the only real quit path).
    * ``listening`` — the shared :class:`ListeningControl`; labels
      refresh from it on every menu open.
    """

    def __init__(
        self,
        listening: ListeningControl | None = None,
        on_show: Callable[[], None] | None = None,
        on_toggle_listening: Callable[[], None] | None = None,
        on_toggle_pause: Callable[[], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        parent: Any | None = None,
    ) -> None:
        from PySide6.QtGui import QAction, QIcon, QPainter, QPixmap
        from PySide6.QtWidgets import QMenu, QSystemTrayIcon

        self._listening = listening or ListeningControl()
        self._on_show = on_show
        self._on_toggle_listening = on_toggle_listening
        self._on_toggle_pause = on_toggle_pause
        self._on_quit = on_quit
        self._balloon_shown = False

        self._icon = QSystemTrayIcon(parent)
        self._icon.setIcon(QIcon(self._make_icon_pixmap()))
        self._icon.setToolTip("ATHENA")
        self._icon.activated.connect(self._on_activated)

        menu = QMenu()
        self._show_action = QAction("Show ATHENA", menu)
        self._show_action.triggered.connect(lambda: self._call(self._on_show))
        menu.addAction(self._show_action)
        menu.addSeparator()

        self._listening_action = QAction("Listening: On", menu)
        self._listening_action.setCheckable(True)
        self._listening_action.triggered.connect(
            lambda: self._call(self._on_toggle_listening)
        )
        menu.addAction(self._listening_action)

        self._pause_action = QAction("Pause listening", menu)
        self._pause_action.setCheckable(True)
        self._pause_action.triggered.connect(
            lambda: self._call(self._on_toggle_pause)
        )
        menu.addAction(self._pause_action)
        menu.addSeparator()

        quit_action = QAction("Quit", menu)
        quit_action.triggered.connect(lambda: self._call(self._on_quit))
        menu.addAction(quit_action)

        menu.aboutToShow.connect(self.refresh_labels)
        self._icon.setContextMenu(menu)
        self._menu = menu

    # -- public API ------------------------------------------------------

    def show(self) -> None:
        self._icon.show()

    def hide(self) -> None:
        self._icon.hide()

    def is_visible(self) -> bool:
        return self._icon.isVisible()

    @property
    def balloon_shown(self) -> bool:
        return self._balloon_shown

    def refresh_labels(self) -> None:
        """Refresh every label from the current listening state —
        called on every menu open."""
        state = TrayMenuState.from_listening(self._listening)
        self._listening_action.setText(state.listening_label)
        self._listening_action.setChecked(state.listening_checked)
        self._pause_action.setText(state.pause_label)
        self._pause_action.setChecked(state.pause_checked)
        self._pause_action.setEnabled(state.pause_enabled)

    def show_first_hide_balloon(self) -> None:
        """One balloon tip on the first hide-to-tray. Only promises
        listening when it is actually on (C# MainWindow_Closing)."""
        if self._balloon_shown:
            return
        self._balloon_shown = True
        message = (
            "ATHENA is still listening in the tray — say 'athena' to bring it back"
            if self._listening.enabled
            else "ATHENA is in the tray — open it from the tray menu"
        )
        try:
            self._icon.showMessage("ATHENA", message, self._icon.MessageIcon.Information, 3000)
        except Exception:
            pass

    def show_startup_balloon(self) -> None:
        """Balloon for a --tray launch. Neutral wording on purpose:
        the wake-word setting may not be loaded yet, so this must
        not claim listening."""
        self._balloon_shown = True
        try:
            self._icon.showMessage(
                "ATHENA",
                "ATHENA started with Windows and is running in the tray — "
                "double-click the tray icon to open it",
                self._icon.MessageIcon.Information,
                3000,
            )
        except Exception:
            pass

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _call(fn: Callable[[], None] | None) -> None:
        if fn is not None:
            fn()

    def _on_activated(self, reason) -> None:
        from PySide6.QtWidgets import QSystemTrayIcon

        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._call(self._on_show)

    @staticmethod
    def _make_icon_pixmap():
        """Gold disc icon (the C# app extracts it from the exe; the
        Python build has no .ico yet, so draw the same gold disc)."""
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPixmap, QRadialGradient

        size = 64
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        grad = QRadialGradient(size / 2, size / 2, size / 2)
        grad.setColorAt(0.0, QColor("#F5D76E"))
        grad.setColorAt(0.6, QColor("#D4AF37"))
        grad.setColorAt(1.0, QColor("#8A6D1B"))
        painter.setPen(QPen(QColor("#7A8A99"), 3))
        painter.setBrush(QBrush(grad))
        painter.drawEllipse(3, 3, size - 6, size - 6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QBrush(QColor(255, 255, 255, 220)))
        painter.drawEllipse(int(size / 2 - 6), int(size / 2 - 6), 12, 12)
        painter.end()
        return pixmap
