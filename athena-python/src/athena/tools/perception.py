"""ATHENA Python v1 — screen perception, ON-DEMAND ONLY.

Two read-only facts about the screen, both computed once per explicit
call and never watched, logged, or sent anywhere:

* :func:`get_active_window` — foreground window title / app / process
  (lazy ``win32gui`` import, Windows-only; ported from jarvis
  ``Tools/Simple_Automations/active_window.py``).
* :func:`read_screen_text` — OCR of the current screen
  (``pytesseract`` + the free Tesseract engine, guarded imports; if
  either is missing the call returns an honest "OCR unavailable"
  result instead of failing). The real Windows laptop needs a free
  Tesseract install for OCR to work — see the note in
  :func:`read_screen_text`.

There is deliberately NO monitor thread, NO background capture, NO
screenshot saved to disk (the image lives in memory only for the OCR
call and is then discarded), and NO remote logging. The jarvis-exp
``perception/screen`` modules saved screenshots to OneDrive and ran
continuously — ATHENA does neither. If the user wants the screen read,
they ask; otherwise nothing is captured.

``ScreenTextTool`` exposes the OCR read as a normal tool so the router
can offer it under the ``screen`` category.
"""

from __future__ import annotations

from typing import Any

from .base_tool import BaseTool, ToolContext, ToolResult

__all__ = ["get_active_window", "read_screen_text", "ScreenTextTool"]

_BROWSERS = {
    "chrome.exe": "Chrome",
    "msedge.exe": "Edge",
    "firefox.exe": "Firefox",
    "opera.exe": "Opera",
    "brave.exe": "Brave",
    "vivaldi.exe": "Vivaldi",
}


def get_active_window() -> dict[str, Any]:
    """One-shot foreground-window read. Returns a dict with
    ``available`` False + ``reason`` when win32gui is missing (i.e.
    not Windows) — never raises, never logs."""
    try:
        import win32gui
        import win32process
        import psutil
    except ImportError:
        return {
            "available": False,
            "reason": "Active-window detection needs pywin32 and psutil on Windows.",
        }
    try:
        hwnd = win32gui.GetForegroundWindow()
        if hwnd == 0:
            return {"available": False, "reason": "No foreground window."}
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            proc = psutil.Process(pid)
            process_name = proc.name().lower()
            app_name = _BROWSERS.get(process_name, proc.name().replace(".exe", "").title())
        except Exception:
            process_name, app_name = "", "Unknown"
        return {
            "available": True,
            "title": title,
            "app_name": app_name,
            "process_name": process_name,
            "is_browser": process_name in _BROWSERS,
        }
    except Exception as exc:
        return {"available": False, "reason": f"Could not read the active window: {exc}"}


def read_screen_text() -> ToolResult:
    """OCR the screen once, right now, and return the text found.

    Guarded at every step: no screenshot library -> honest failure;
    no OCR library -> honest failure; no Tesseract engine binary ->
    honest failure. The screenshot is held in memory only and
    discarded after the OCR call; nothing is written to disk,
    nothing leaves the machine, and nothing is captured unless this
    function is explicitly called.

    Backend is ``pytesseract`` (free, $0) driving the Tesseract OCR
    engine. The Python package alone is not enough: the Tesseract
    *binary* must also be installed — on the real Windows laptop
    that means the free Tesseract installer
    (https://github.com/UB-Mannheim/tesseract/wiki), after which
    ``pytesseract`` finds it on PATH. Without the binary this
    function returns an honest failure, never a crash.
    """
    try:
        import pyautogui
    except ImportError:
        return ToolResult.failure(
            "Screen capture needs pyautogui, which is not installed — "
            "OCR unavailable."
        )
    try:
        import pytesseract
    except ImportError:
        return ToolResult.failure(
            "OCR needs pytesseract, which is not installed — "
            "OCR unavailable."
        )
    try:
        image = pyautogui.screenshot()  # in-memory only; never saved
        text = pytesseract.image_to_string(image)
    except ImportError as exc:
        return ToolResult.failure(f"OCR unavailable: {exc}")
    except Exception as exc:
        # pytesseract raises TesseractNotFoundError (a subclass of
        # RuntimeError) when the Tesseract binary is missing; it is
        # also the bucket for any other OCR failure.
        if "tesseract" in type(exc).__name__.lower() or "tesseract" in str(exc).lower():
            return ToolResult.failure(
                "OCR unavailable — install Tesseract (free): the Tesseract "
                "engine binary was not found on this machine."
            )
        return ToolResult.failure(f"Could not read the screen: {exc}")
    text = (text or "").strip()
    if not text:
        return ToolResult.success("No text found on the screen.")
    lines = [line for line in text.splitlines() if line.strip()]
    return ToolResult.success(text, data={"lines": lines})


class ScreenTextTool(BaseTool):
    name = "read_screen"
    description = "Read the text currently on the screen (one-shot OCR, done only when asked). Says so honestly when OCR is unavailable."
    category = "screen"
    parameters = {}
    required = []

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        return read_screen_text()
