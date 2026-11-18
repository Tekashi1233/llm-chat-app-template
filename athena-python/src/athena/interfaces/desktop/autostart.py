"""ATHENA Python v1 — autostart (N8), pure policy + stores.

Direct port of ``Athena.Core/Autostart.cs`` and
``Services/WindowsAutostartStore.cs``:

* Per-user Registry Run key only — HKCU\\Software\\Microsoft\\
  Windows\\CurrentVersion\\Run, value name ``"ATHENA"``,
  value = quoted exe path + ``" --tray"``. No Startup-folder
  shortcut, no admin rights, no all-users install.
* All policy logic is pure (no registry access) and testable on
  Linux behind :class:`IAutostartStore`; the real store is
  Windows-only and every call site must treat it as best-effort.
* ``--tray`` (case-insensitive, anywhere in the args) means: start
  hidden with the tray icon live. Whether the wake-word listener
  actually starts is NOT decided here — it follows the persisted
  voice setting, exactly as a normal launch does.

Nothing here records, stores or transmits audio.
"""

from __future__ import annotations

from typing import Callable, Protocol

__all__ = [
    "IAutostartStore",
    "FakeAutostartStore",
    "WindowsAutostartStore",
    "AutostartPolicy",
    "StartupOptions",
    "parse_startup_options",
]

VALUE_NAME = "ATHENA"
ARGUMENT = "--tray"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


class IAutostartStore(Protocol):
    """Raw Run-key value for "ATHENA", or None when absent."""

    def get_entry(self) -> str | None: ...

    def set_entry(self, value: str) -> None: ...

    def remove_entry(self) -> None: ...


class FakeAutostartStore:
    """In-memory store for tests (and for non-Windows runs)."""

    def __init__(self, entry: str | None = None) -> None:
        self._entry = entry
        self.writes = 0
        self.removals = 0

    def get_entry(self) -> str | None:
        return self._entry

    def set_entry(self, value: str) -> None:
        self._entry = value
        self.writes += 1

    def remove_entry(self) -> None:
        self._entry = None
        self.removals += 1


class WindowsAutostartStore:
    """The real store — per-user Registry Run key. Windows-only.

    ``winreg`` is imported lazily so importing this module on Linux
    never fails; on non-Windows every method raises
    ``NotImplementedError`` and callers must treat that as
    best-effort (autostart is a nicety, never fatal to startup).
    """

    def get_entry(self) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
                value, _kind = winreg.QueryValueEx(key, VALUE_NAME)
        except FileNotFoundError:
            return None
        return value if isinstance(value, str) else None

    def set_entry(self, value: str) -> None:
        import winreg

        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY_PATH) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, value)

    def remove_entry(self) -> None:
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, RUN_KEY_PATH, 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.DeleteValue(key, VALUE_NAME)
        except FileNotFoundError:
            pass


class AutostartPolicy:
    """Pure Run-key policy. Writes only when the entry is missing or
    wrong — a launch must not re-write the key blindly every time."""

    value_name = VALUE_NAME
    argument = ARGUMENT
    run_key_path = RUN_KEY_PATH

    @staticmethod
    def build_entry_value(exe_path: str) -> str:
        """Quoted exe path + " --tray" (paths with spaces need quotes)."""
        return f'"{exe_path}" {ARGUMENT}'

    @staticmethod
    def parse_exe_path(value: str | None) -> str | None:
        """Extract the exe path from a Run-key value.

        Handles a quoted path (``"C:\\...\\Athena.exe" --tray``) and
        an unquoted one. Returns None when blank or unparseable —
        the caller must leave such an entry alone rather than
        delete something it cannot understand.
        """
        if not value or not value.strip():
            return None
        v = value.strip()
        if v[0] == '"':
            close = v.find('"', 1)
            if close <= 1:
                return None
            path = v[1:close]
            return path if path.strip() else None
        for sep in (" ", "\t"):
            end = v.find(sep)
            if end >= 0:
                return v[:end]
        return v

    @staticmethod
    def has_tray_argument(value: str | None) -> bool:
        """True when the value carries --tray as its own argument."""
        if not value or not value.strip():
            return False
        return any(part.lower() == ARGUMENT for part in value.split())

    @classmethod
    def is_enabled(cls, store: IAutostartStore, exe_path: str) -> bool:
        """True when the entry exists, points at this exe, and carries --tray."""
        entry = store.get_entry()
        if entry is None:
            return False
        parsed = cls.parse_exe_path(entry)
        return (
            parsed is not None
            and parsed.lower() == exe_path.lower()
            and cls.has_tray_argument(entry)
        )

    @classmethod
    def ensure_enabled(cls, store: IAutostartStore, exe_path: str) -> bool:
        """Register autostart for this exe. Returns True when written."""
        if cls.is_enabled(store, exe_path):
            return False
        store.set_entry(cls.build_entry_value(exe_path))
        return True

    @classmethod
    def ensure_disabled(cls, store: IAutostartStore) -> bool:
        """Remove the entry when present. Returns True when removed."""
        if store.get_entry() is None:
            return False
        store.remove_entry()
        return True

    @classmethod
    def clean_stale_entry(
        cls, store: IAutostartStore, file_exists: Callable[[str], bool]
    ) -> bool:
        """Remove a stale entry: one whose exe path parses cleanly
        but the file no longer exists (moved/deleted install). An
        unparseable entry is left alone — it may belong to something
        else. Returns True when an entry was removed."""
        entry = store.get_entry()
        if entry is None:
            return False
        path = cls.parse_exe_path(entry)
        if path is None:
            return False
        if file_exists(path):
            return False
        store.remove_entry()
        return True


class StartupOptions:
    """Parsed command line. ``start_hidden`` is the --tray launch."""

    def __init__(self, start_hidden: bool = False) -> None:
        self.start_hidden = start_hidden

    @classmethod
    def parse(cls, args) -> "StartupOptions":
        return cls(
            start_hidden=any(
                str(a).lower() == ARGUMENT for a in args
            )
        )


def parse_startup_options(args=None) -> StartupOptions:
    """Parse startup options from ``args`` (defaults to ``sys.argv[1:]``).

    --tray anywhere in the args (case-insensitive) starts hidden.
    """
    import sys

    if args is None:
        args = sys.argv[1:]
    return StartupOptions.parse(args)
