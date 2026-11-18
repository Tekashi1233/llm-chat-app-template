"""ATHENA Python v1 — UI package (PySide6 port of the C# WPF app).

Black shell, arc reactor, glass surfaces — the "minimal black
identity" from the C# app (MainWindow.xaml / App.xaml brushes).

Modules that are pure logic (no Qt, no I/O) so they can be tested
anywhere, including this Linux VM:

* :mod:`athena.interfaces.desktop.theme` — palette + glass styles.
* :mod:`athena.interfaces.desktop.presence` — presence state → reactor brightness.
* :mod:`athena.interfaces.desktop.autostart` — Run-key policy, fake + Windows stores.
* :mod:`athena.interfaces.desktop.settings_store` — voice.json / settings.json.

Qt widgets (need PySide6; importable on this VM, but all visual
behaviour here is simulated-only — nothing is verified on a real
Windows laptop by this build):

* :mod:`athena.interfaces.desktop.reactor` — the arc reactor.
* :mod:`athena.interfaces.desktop.chat_view` — chat feed + glass input bar.
* :mod:`athena.interfaces.desktop.settings_view` — Voice + Startup cards.
* :mod:`athena.interfaces.desktop.views` — Conversations / Memory / Tools / Devices /
  Activity panels.
* :mod:`athena.interfaces.desktop.tray` — system-tray icon + menu.
* :mod:`athena.interfaces.desktop.main_window` — the shell + ``build_app()`` factory.

Heavy collaborators (Worker client, voice engines, tool registry)
are injected through constructors — never imported at module top
level — so the UI can be built and tested without them.
"""

from __future__ import annotations

__all__ = ["build_app"]


def __getattr__(name: str):  # lazy: importing athena.interfaces.desktop must not need Qt
    if name == "build_app":
        from .main_window import build_app

        return build_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
