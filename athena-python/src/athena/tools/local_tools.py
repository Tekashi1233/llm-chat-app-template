"""ATHENA Python v1 — local tools.

Ports of the C# Companion / Worker tool behaviour, with the same
allowlists the Companion enforces (see vesper-companion
``Config.DefaultApps/DefaultFolders/DefaultCommands``):

* apps: Notepad, Calculator, Paint, File Explorer, Chrome, Edge — nothing else.
* folders: Desktop, Documents, Downloads — file tools refuse anything outside.
* commands: ping, network info, list programs, list folder — read-only,
  fixed templates, no user-supplied flags (flag smuggling rejected).

File writes/creates/edits take their approval from
``ToolContext.confirm`` (the UI's on-screen Allow/Deny popup, 60s
auto-deny). No confirmer, no write. Windows-only behaviour
(win32gui, ``ipconfig``/``tasklist``) is guarded and fails honestly
on other platforms — it is simulated-only until run on the real laptop.
"""

from __future__ import annotations

import ast
import json
import math
import os
import re
import subprocess
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, quote_plus
from zoneinfo import ZoneInfo

import httpx

from ..config import APP_DATA_DIR
from .base_tool import BaseTool, ToolContext, ToolResult

__all__ = [
    "APPROVED_APPS",
    "APPROVED_COMMANDS",
    "approved_folders",
    "suggest_extension",
    "OpenAppTool",
    "SearchFilesTool",
    "ReadFileTool",
    "CreateFileTool",
    "EditFileTool",
    "RunCommandTool",
    "BatteryStatusTool",
    "ActiveWindowTool",
    "WeatherTool",
    "CalculateTool",
    "DateTimeTool",
    "WebSearchTool",
    "YouTubeTool",
    "TabControlTool",
    "SaveNoteTool",
    "ListNotesTool",
]

# ---------------------------------------------------------------------------
# Allowlists (mirror vesper-companion Config.cs defaults)
# ---------------------------------------------------------------------------

#: name -> executable, exactly the Companion's DefaultApps().
APPROVED_APPS: dict[str, str] = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "paint": "mspaint.exe",
    "file explorer": "explorer.exe",
    "chrome": "chrome.exe",
    "edge": "msedge.exe",
}

#: name -> (program, fixed args template, arg kind). Mirrors the
#: Companion's DefaultCommands(): read-only only, the model cannot add flags.
APPROVED_COMMANDS: dict[str, tuple[str, str, str]] = {
    "network info": ("ipconfig", "", "none"),
    "ping": ("ping", "-n 2 {arg}" if os.name == "nt" else "-c 2 {arg}", "host"),
    "list programs": ("tasklist", "", "none"),
    "list folder": ("dir", "{arg}", "folder"),
}

MAX_READ_BYTES = 200_000
MAX_SEARCH_RESULTS = 50
_COMMAND_TIMEOUT_SECONDS = 20

_HOST_RE = re.compile(r"^[A-Za-z0-9._-]{1,253}$")
_SHELL_META_RE = re.compile(r"[;&|<>$`\\\n\r]")


def approved_folders() -> list[Path]:
    """Desktop, Documents, Downloads under the user's home — the same
    three folders the Companion allows. Tests monkeypatch this."""
    home = Path.home()
    return [home / "Desktop", home / "Documents", home / "Downloads"]


def _inside_approved(path: Path) -> tuple[bool, str]:
    """True when ``path`` resolves inside an approved folder (symlink /
    junction escape included — ``resolve()`` follows the link first)."""
    try:
        resolved = path.resolve()
    except (OSError, RuntimeError):
        return False, f"Could not resolve path: {path}"
    for folder in approved_folders():
        try:
            if resolved.is_relative_to(folder.resolve()):
                return True, ""
        except OSError:
            continue
    return False, (
        f"The path '{path}' is not inside an approved folder. Approved "
        f"folders are: {', '.join(str(f) for f in approved_folders())}."
    )


def _is_text_file(path: Path) -> bool:
    try:
        with path.open("rb") as fh:
            chunk = fh.read(8192)
        chunk.decode("utf-8")
        return True
    except (OSError, UnicodeDecodeError):
        return False


# ---------------------------------------------------------------------------
# File-extension helper (ported from jarvis Tools/create_file.py)
# ---------------------------------------------------------------------------

_EXTENSION_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("python file", ".py"), ("java file", ".java"), ("c++ file", ".cpp"),
    ("cpp file", ".cpp"), ("c# file", ".cs"), ("c sharp file", ".cs"),
    ("go file", ".go"), ("rust file", ".rs"), ("kotlin file", ".kt"),
    ("swift file", ".swift"), ("html file", ".html"), ("css file", ".css"),
    ("javascript file", ".js"), ("js file", ".js"), ("typescript file", ".ts"),
    ("json file", ".json"), ("xml file", ".xml"), ("yaml file", ".yaml"),
    ("yml file", ".yaml"), ("csv file", ".csv"), ("markdown file", ".md"),
    ("md file", ".md"), ("text file", ".txt"), ("txt file", ".txt"),
    ("shell file", ".sh"), ("bash file", ".sh"), ("batch file", ".bat"),
    ("powershell file", ".ps1"), ("sql file", ".sql"), ("log file", ".log"),
)


def suggest_extension(description: str) -> str:
    """Pick a file extension from a plain-language description
    (``"a python file"`` -> ``".py"``); ``".txt"`` when nothing matches."""
    text = (description or "").lower()
    for phrase, ext in _EXTENSION_KEYWORDS:
        if phrase in text:
            return ext
    return ".txt"


# ---------------------------------------------------------------------------
# Apps
# ---------------------------------------------------------------------------

class OpenAppTool(BaseTool):
    name = "open_app"
    description = "Open an approved app on the laptop: Notepad, Calculator, Paint, File Explorer, Chrome or Edge. Nothing else can be opened."
    category = "apps"
    parameters = {
        "app": {"type": "string", "description": "App name, e.g. 'Notepad' or 'Chrome'."},
    }
    required = ["app"]

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        missing = self._missing_args(args)
        if missing:
            return ToolResult.failure(f"Missing argument: {', '.join(missing)}.")
        asked = str(args["app"]).strip().lower()
        exe = APPROVED_APPS.get(asked)
        if exe is None:
            return ToolResult.failure(
                f"'{args['app']}' is not on the approved apps list. Approved apps: "
                f"{', '.join(sorted(APPROVED_APPS))}."
            )
        if os.name != "nt":
            return ToolResult.failure(
                f"Opening apps is Windows-only; this machine is not Windows, "
                f"so '{args['app']}' was not opened."
            )
        try:
            subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            return ToolResult.failure(f"Could not open '{args['app']}': {exc}")
        return ToolResult.success(f"Opened {args['app']}.")


# ---------------------------------------------------------------------------
# Files (read-only tools need no confirmation; writes do)
# ---------------------------------------------------------------------------

class SearchFilesTool(BaseTool):
    name = "search_files"
    description = "Find files by name in the approved folders (Desktop, Documents, Downloads)."
    category = "files"
    parameters = {
        "query": {"type": "string", "description": "Part of the file name to look for."},
        "folder": {"type": "string", "description": "Optional subfolder (Desktop, Documents or Downloads)."},
    }
    required = ["query"]

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        missing = self._missing_args(args)
        if missing:
            return ToolResult.failure(f"Missing argument: {', '.join(missing)}.")
        query = str(args["query"]).lower()
        roots = approved_folders()
        if args.get("folder"):
            wanted = str(args["folder"]).strip().lower()
            roots = [f for f in roots if f.name.lower() == wanted]
            if not roots:
                return ToolResult.failure(f"No approved folder named '{args['folder']}'.")
        found: list[str] = []
        for root in roots:
            if not root.is_dir():
                continue
            for dirpath, _dirnames, filenames in os.walk(root):
                for filename in filenames:
                    if query in filename.lower():
                        found.append(str(Path(dirpath) / filename))
                        if len(found) >= MAX_SEARCH_RESULTS:
                            break
                if len(found) >= MAX_SEARCH_RESULTS:
                    break
        if not found:
            return ToolResult.success(f"No files matching '{args['query']}' in the approved folders.")
        return ToolResult.success(
            f"Found {len(found)} file(s):\n" + "\n".join(found),
            data={"files": found},
        )


class ReadFileTool(BaseTool):
    name = "read_file"
    description = "Read a text file inside an approved folder (Desktop, Documents, Downloads). Text files only, up to 200 KB."
    category = "files"
    parameters = {
        "path": {"type": "string", "description": "Full path of the file to read."},
    }
    required = ["path"]

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        missing = self._missing_args(args)
        if missing:
            return ToolResult.failure(f"Missing argument: {', '.join(missing)}.")
        path = Path(str(args["path"]))
        ok, reason = _inside_approved(path)
        if not ok:
            return ToolResult.failure(reason)
        if not path.is_file():
            return ToolResult.failure(f"No such file: {path}")
        if path.stat().st_size > MAX_READ_BYTES:
            return ToolResult.failure(f"File is over the 200 KB read limit: {path}")
        if not _is_text_file(path):
            return ToolResult.failure(f"Not a readable text file: {path}")
        try:
            return ToolResult.success(path.read_text(encoding="utf-8"), data={"path": str(path)})
        except OSError as exc:
            return ToolResult.failure(f"Could not read {path}: {exc}")


class CreateFileTool(BaseTool):
    name = "create_file"
    description = "Create a new text file in an approved folder. Asks the owner for on-screen approval first."
    category = "files"
    parameters = {
        "path": {"type": "string", "description": "Full path of the file to create, inside an approved folder."},
        "content": {"type": "string", "description": "Text to write into the file."},
        "description": {"type": "string", "description": "Plain-language description used to pick the extension when the path has none, e.g. 'a python file'."},
    }
    required = ["path"]

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        missing = self._missing_args(args)
        if missing:
            return ToolResult.failure(f"Missing argument: {', '.join(missing)}.")
        path = Path(str(args["path"]))
        if not path.suffix and args.get("description"):
            path = path.with_suffix(suggest_extension(str(args["description"])))
        ok, reason = _inside_approved(path)
        if not ok:
            return ToolResult.failure(reason)
        if path.exists():
            return ToolResult.failure(f"File already exists: {path} (use edit_file to change it).")
        content = str(args.get("content", ""))
        denied = self._require_confirm(
            context,
            f"Create file {path.name} in {path.parent}",
            {"path": str(path), "content_preview": content[:500]},
        )
        if denied is not None:
            return denied
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return ToolResult.failure(f"Could not create {path}: {exc}")
        return ToolResult.success(f"Created {path}.", data={"path": str(path)})


class EditFileTool(BaseTool):
    name = "edit_file"
    description = "Add to or replace text in a file in an approved folder. Asks the owner for on-screen approval first."
    category = "files"
    parameters = {
        "path": {"type": "string", "description": "Full path of the file to edit."},
        "old_text": {"type": "string", "description": "Text to replace; omit for append mode."},
        "new_text": {"type": "string", "description": "Replacement text, or text to append when old_text is omitted."},
        "mode": {"type": "string", "description": "'replace' (default) or 'append'."},
    }
    required = ["path", "new_text"]

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        missing = self._missing_args(args)
        if missing:
            return ToolResult.failure(f"Missing argument: {', '.join(missing)}.")
        path = Path(str(args["path"]))
        ok, reason = _inside_approved(path)
        if not ok:
            return ToolResult.failure(reason)
        if not path.is_file():
            return ToolResult.failure(f"No such file: {path}")
        if not _is_text_file(path):
            return ToolResult.failure(f"Not a readable text file: {path}")
        mode = str(args.get("mode", "replace")).lower()
        if mode not in ("replace", "append"):
            return ToolResult.failure(f"Unknown edit mode: {mode!r} (use 'replace' or 'append').")
        try:
            current = path.read_text(encoding="utf-8")
        except OSError as exc:
            return ToolResult.failure(f"Could not read {path}: {exc}")
        new_text = str(args["new_text"])
        if mode == "append":
            updated = current + new_text
        else:
            old_text = str(args.get("old_text", ""))
            if old_text not in current:
                return ToolResult.failure(f"Text to replace was not found in {path}. Nothing was changed.")
            updated = current.replace(old_text, new_text, 1)
        denied = self._require_confirm(
            context,
            f"Edit file {path.name} ({mode})",
            {"path": str(path), "mode": mode, "new_text_preview": new_text[:500]},
        )
        if denied is not None:
            return denied
        try:
            path.write_text(updated, encoding="utf-8")
        except OSError as exc:
            return ToolResult.failure(f"Could not write {path}: {exc}")
        return ToolResult.success(f"Edited {path} ({mode}).", data={"path": str(path)})


# ---------------------------------------------------------------------------
# System
# ---------------------------------------------------------------------------

class RunCommandTool(BaseTool):
    name = "run_command"
    description = "Run an approved read-only command: 'ping' (a host), 'network info', 'list programs', or 'list folder' (an approved folder). No other command can run, and no extra flags can be added."
    category = "system"
    parameters = {
        "command": {"type": "string", "description": "One of: ping, network info, list programs, list folder."},
        "arg": {"type": "string", "description": "Host for ping, or folder path for list folder."},
    }
    required = ["command"]

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        missing = self._missing_args(args)
        if missing:
            return ToolResult.failure(f"Missing argument: {', '.join(missing)}.")
        asked = str(args["command"]).strip().lower()
        entry = APPROVED_COMMANDS.get(asked)
        if entry is None:
            return ToolResult.failure(
                f"'{args['command']}' is not on the approved commands list. Approved commands: "
                f"{', '.join(sorted(APPROVED_COMMANDS))}."
            )
        program, template, kind = entry
        argv: list[str] = [program]
        if kind == "host":
            host = str(args.get("arg", "")).strip()
            if not _HOST_RE.match(host):
                return ToolResult.failure(
                    f"Invalid host for ping: {host!r}. Only plain hostnames and IPs are allowed."
                )
            argv = [program] + [
                host if part == "{arg}" else part for part in template.split()
            ]
        elif kind == "folder":
            folder = str(args.get("arg", "")).strip()
            if not folder:
                return ToolResult.failure("list folder needs a folder path.")
            if _SHELL_META_RE.search(folder):
                return ToolResult.failure(f"Invalid folder path: {folder!r}.")
            ok, reason = _inside_approved(Path(folder))
            if not ok:
                return ToolResult.failure(reason)
            if os.name == "nt":
                argv = ["cmd", "/c", "dir", folder]
            else:
                argv = ["ls", "-la", folder]
        else:  # "none" — fixed program, no arguments at all
            if os.name != "nt" and program in ("ipconfig", "tasklist"):
                argv = ["ip", "addr"] if program == "ipconfig" else ["ps", "-e", "-o", "comm="]
            elif os.name != "nt" and program == "dir":
                argv = ["ls", "-la"]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True,
                timeout=_COMMAND_TIMEOUT_SECONDS, shell=False,
            )
        except FileNotFoundError:
            return ToolResult.failure(f"Command program not found on this machine: {program}")
        except subprocess.TimeoutExpired:
            return ToolResult.failure(f"Command timed out after {_COMMAND_TIMEOUT_SECONDS}s: {asked}")
        except OSError as exc:
            return ToolResult.failure(f"Could not run '{asked}': {exc}")
        output = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0 and not output.strip():
            return ToolResult.failure(f"'{asked}' failed (exit {proc.returncode}).")
        return ToolResult.success(output.strip() or f"'{asked}' ran with no output.",
                                  data={"exit_code": proc.returncode})


class BatteryStatusTool(BaseTool):
    name = "battery_status"
    description = "Current battery percentage and whether the laptop is charging. Says so honestly when there is no battery."
    category = "system"
    parameters = {}
    required = []

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        try:
            import psutil
        except ImportError:
            return ToolResult.failure(
                "Battery status needs psutil, which is not installed."
            )
        battery = psutil.sensors_battery()
        if battery is None:
            return ToolResult.failure("No battery detected on this machine.")
        percent = int(battery.percent)
        plugged = "charging" if battery.power_plugged else "on battery"
        secs = getattr(battery, "secsleft", None)
        text = f"Battery: {percent}% ({plugged})."
        if secs is not None and secs > 0 and not battery.power_plugged:
            text += f" About {secs // 60} minutes left."
        return ToolResult.success(text, data={
            "percent": percent,
            "plugged": bool(battery.power_plugged),
            "secs_left": secs,
        })


class ActiveWindowTool(BaseTool):
    name = "active_window"
    description = "Which app or website is in the foreground right now: window title, app and process name. Local-only, Windows-only, checked once when asked — never watched continuously."
    category = "screen"
    parameters = {}
    required = []

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        try:
            import win32gui
            import win32process
            import psutil
        except ImportError:
            return ToolResult.failure(
                "Active-window detection needs pywin32 and psutil on Windows; "
                "it is not available on this machine."
            )
        try:
            hwnd = win32gui.GetForegroundWindow()
            if hwnd == 0:
                return ToolResult.failure("No foreground window.")
            title = win32gui.GetWindowText(hwnd)
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            try:
                proc = psutil.Process(pid)
                process_name = proc.name().lower()
                app_name = proc.name().replace(".exe", "").title()
            except Exception:
                process_name, app_name = "", "Unknown"
            browsers = {"chrome.exe", "msedge.exe", "firefox.exe", "opera.exe",
                        "brave.exe", "vivaldi.exe"}
            is_browser = process_name in browsers
            text = f"Active window: {app_name} — {title!r}" if title else f"Active app: {app_name}"
            return ToolResult.success(text, data={
                "title": title, "app_name": app_name,
                "process_name": process_name, "is_browser": is_browser,
            })
        except Exception as exc:
            return ToolResult.failure(f"Could not read the active window: {exc}")


# ---------------------------------------------------------------------------
# Weather (Open-Meteo, no API key — same provider as the Worker tool)
# ---------------------------------------------------------------------------

_WEATHER_CODES: dict[int, str] = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Depositing rime fog", 51: "Light drizzle",
    53: "Moderate drizzle", 55: "Dense drizzle", 56: "Light freezing drizzle",
    57: "Dense freezing drizzle", 61: "Slight rain", 63: "Moderate rain",
    65: "Heavy rain", 66: "Light freezing rain", 67: "Heavy freezing rain",
    71: "Slight snow", 73: "Moderate snow", 75: "Heavy snow",
    77: "Snow grains", 80: "Slight rain showers", 81: "Moderate rain showers",
    82: "Violent rain showers", 85: "Slight snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


class WeatherTool(BaseTool):
    name = "get_weather"
    description = "Current weather for any city or place in the world (Open-Meteo, no API key)."
    category = "weather"
    parameters = {
        "place": {"type": "string", "description": "City or place, e.g. 'Riyadh'."},
    }
    required = ["place"]

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        missing = self._missing_args(args)
        if missing:
            return ToolResult.failure(f"Missing argument: {', '.join(missing)}.")
        place = str(args["place"]).strip()
        try:
            geo = httpx.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": place, "count": 1},
                timeout=10,
            )
            geo.raise_for_status()
            results = geo.json().get("results") or []
            if not results:
                return ToolResult.failure(f"Could not find a place called '{place}'.")
            loc = results[0]
            fc = httpx.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": loc["latitude"], "longitude": loc["longitude"],
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,"
                               "weather_code,wind_speed_10km_h,wind_direction_10km_h",
                },
                timeout=10,
            )
            fc.raise_for_status()
            cur = fc.json()["current"]
        except httpx.HTTPError as exc:
            return ToolResult.failure(f"Weather service unreachable: {exc}")
        except (KeyError, ValueError, TypeError) as exc:
            return ToolResult.failure(f"Could not parse the weather reply: {exc}")
        desc = _WEATHER_CODES.get(int(cur.get("weather_code", -1)), "Unknown conditions")
        city = loc.get("name", place)
        country = loc.get("country", "")
        where = f"{city}, {country}" if country else city
        text = (
            f"Weather in {where}: {desc}, {cur['temperature_2m']}°C "
            f"(feels like {cur['apparent_temperature']}°C), "
            f"humidity {cur['relative_humidity_2m']}%, "
            f"wind {cur['wind_speed_10km_h']} km/h."
        )
        return ToolResult.success(text, data={
            "place": where, "description": desc,
            "temperature_c": cur["temperature_2m"],
            "feels_like_c": cur["apparent_temperature"],
            "humidity": cur["relative_humidity_2m"],
            "wind_kmh": cur["wind_speed_10km_h"],
        })


# ---------------------------------------------------------------------------
# Calc — own safe maths parser (no eval), mirroring the Worker tool
# ---------------------------------------------------------------------------

_ALLOWED_FUNCS = {
    "sqrt": math.sqrt, "abs": abs, "round": round,
    "min": min, "max": max, "sin": math.sin, "cos": math.cos,
    "tan": math.tan, "log": math.log, "floor": math.floor, "ceil": math.ceil,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.BinOp):
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            return left // right
        if isinstance(node.op, ast.Mod):
            return left % right
        if isinstance(node.op, ast.Pow):
            return left ** right
        raise ValueError(f"Operator not allowed: {type(node.op).__name__}")
    if isinstance(node, ast.UnaryOp):
        value = _eval_node(node.operand)
        if isinstance(node.op, ast.USub):
            return -value
        if isinstance(node.op, ast.UAdd):
            return +value
        raise ValueError(f"Operator not allowed: {type(node.op).__name__}")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        func = _ALLOWED_FUNCS.get(node.func.id)
        if func is None:
            raise ValueError(f"Function not allowed: {node.func.id}")
        return float(func(*[_eval_node(a) for a in node.args]))
    raise ValueError(f"Expression not allowed: {type(node).__name__}")


def evaluate_expression(expression: str) -> float:
    """Evaluate a maths expression safely (AST walk, no ``eval``)."""
    tree = ast.parse(expression, mode="eval")
    return _eval_node(tree)


class CalculateTool(BaseTool):
    name = "calculate"
    description = "Evaluate a maths expression exactly, e.g. '250*1.15/3'. No variables, no code — maths only."
    category = "calc"
    parameters = {
        "expression": {"type": "string", "description": "Maths expression, e.g. '250*1.15/3'."},
    }
    required = ["expression"]

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        missing = self._missing_args(args)
        if missing:
            return ToolResult.failure(f"Missing argument: {', '.join(missing)}.")
        expression = str(args["expression"]).strip()
        try:
            value = evaluate_expression(expression)
        except ZeroDivisionError:
            return ToolResult.failure("Cannot divide by zero.")
        except (SyntaxError, ValueError) as exc:
            return ToolResult.failure(f"Could not evaluate {expression!r}: {exc}")
        return ToolResult.success(f"{expression} = {value}", data={"value": value})


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

class DateTimeTool(BaseTool):
    name = "get_datetime"
    description = "Current date and time, in any timezone you ask for (IANA name, e.g. 'Asia/Riyadh')."
    category = "time"
    parameters = {
        "timezone": {"type": "string", "description": "IANA timezone, e.g. 'Asia/Riyadh'. Defaults to the local timezone."},
    }
    required = []

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        tz_name = str(args.get("timezone") or "").strip()
        try:
            tz = ZoneInfo(tz_name) if tz_name else datetime.now().astimezone().tzinfo
            now = datetime.now(tz)
        except Exception:
            return ToolResult.failure(f"Unknown timezone: {tz_name!r}.")
        text = now.strftime("%A, %d %B %Y, %H:%M:%S %Z")
        return ToolResult.success(text, data={
            "iso": now.isoformat(), "timezone": tz_name or str(tz),
        })


# ---------------------------------------------------------------------------
# Web (Wikipedia summary — same source as the Worker search_web tool)
# ---------------------------------------------------------------------------

class WebSearchTool(BaseTool):
    name = "search_web"
    description = "Look up a topic, person, place or fact on Wikipedia, with a short summary and a link."
    category = "web"
    parameters = {
        "query": {"type": "string", "description": "Topic to look up."},
    }
    required = ["query"]

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        missing = self._missing_args(args)
        if missing:
            return ToolResult.failure(f"Missing argument: {', '.join(missing)}.")
        query = str(args["query"]).strip()
        try:
            resp = httpx.get(
                "https://en.wikipedia.org/api/rest_v1/page/summary/"
                + quote(query, safe=""),
                timeout=10,
            )
            if resp.status_code == 404:
                return ToolResult.failure(f"No Wikipedia page found for '{query}'.")
            resp.raise_for_status()
            page = resp.json()
        except httpx.HTTPError as exc:
            return ToolResult.failure(f"Wikipedia unreachable: {exc}")
        summary = page.get("extract") or "No summary available."
        url = page.get("content_urls", {}).get("desktop", {}).get("page", "")
        text = f"{page.get('title', query)}: {summary}"
        if url:
            text += f"\n{url}"
        return ToolResult.success(text, data={"title": page.get("title"), "url": url})


# ---------------------------------------------------------------------------
# Media — YouTube + browser tabs (ported from jarvis tab/youtube automations)
# Best-effort on Windows: keystrokes need pyautogui; without it the tool
# says so honestly instead of pretending it worked.
# ---------------------------------------------------------------------------

def _press(*keys: str) -> ToolResult:
    """Send keystrokes via pyautogui, or fail honestly when unavailable."""
    try:
        import pyautogui
    except ImportError:
        return ToolResult.failure(
            "Keyboard control needs pyautogui, which is not installed, "
            "so this action was not performed."
        )
    if os.name != "nt":
        return ToolResult.failure(
            "Keyboard control is Windows-only; this machine is not Windows."
        )
    try:
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)
    except Exception as exc:
        return ToolResult.failure(f"Could not send the keystroke: {exc}")
    return ToolResult.success("Done.")


class YouTubeTool(BaseTool):
    name = "youtube"
    description = "YouTube control: search (opens the results in the browser), play/pause, volume, seek. Keystroke actions need pyautogui on Windows and fail honestly without it."
    category = "media"
    parameters = {
        "action": {"type": "string", "description": "One of: search, play_pause, volume_up, volume_down, seek_forward, seek_backward."},
        "query": {"type": "string", "description": "Search text for the 'search' action."},
    }
    required = ["action"]

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        missing = self._missing_args(args)
        if missing:
            return ToolResult.failure(f"Missing argument: {', '.join(missing)}.")
        action = str(args["action"]).strip().lower()
        if action == "search":
            query = str(args.get("query", "")).strip()
            if not query:
                return ToolResult.failure("YouTube search needs a query.")
            url = (
                "https://www.youtube.com/results?search_query="
                + quote_plus(query)
            )
            try:
                webbrowser.open(url)
            except Exception as exc:
                return ToolResult.failure(f"Could not open the browser: {exc}")
            return ToolResult.success(f"Searching YouTube for '{query}'.", data={"url": url})
        keys = {
            "play_pause": ("space",),
            "volume_up": ("up",),
            "volume_down": ("down",),
            "seek_forward": ("right",),
            "seek_backward": ("left",),
        }.get(action)
        if keys is None:
            return ToolResult.failure(
                f"Unknown YouTube action: {action!r}. Use search, play_pause, "
                "volume_up, volume_down, seek_forward or seek_backward."
            )
        result = _press(*keys)
        if result.ok:
            return ToolResult.success(f"YouTube: {action.replace('_', ' ')}.")
        return result


class TabControlTool(BaseTool):
    name = "tab_control"
    description = "Browser-tab control: new tab, close tab, refresh, next/previous tab, back, forward. Needs pyautogui on Windows; fails honestly without it."
    category = "media"
    parameters = {
        "action": {"type": "string", "description": "One of: new_tab, close_tab, refresh, next_tab, previous_tab, go_back, go_forward."},
    }
    required = ["action"]

    _HOTKEYS: dict[str, tuple[str, ...]] = {
        "new_tab": ("ctrl", "t"),
        "close_tab": ("ctrl", "w"),
        "refresh": ("ctrl", "r"),
        "next_tab": ("ctrl", "tab"),
        "previous_tab": ("ctrl", "shift", "tab"),
        "go_back": ("alt", "left"),
        "go_forward": ("alt", "right"),
    }

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        missing = self._missing_args(args)
        if missing:
            return ToolResult.failure(f"Missing argument: {', '.join(missing)}.")
        action = str(args["action"]).strip().lower()
        keys = self._HOTKEYS.get(action)
        if keys is None:
            return ToolResult.failure(
                f"Unknown tab action: {action!r}. Use: {', '.join(sorted(self._HOTKEYS))}."
            )
        result = _press(*keys)
        if result.ok:
            return ToolResult.success(f"Tab action: {action.replace('_', ' ')}.")
        return result


# ---------------------------------------------------------------------------
# Notes — ATHENA's own local notes (app-data dir, not user folders, so no
# on-screen confirmation is needed: this is ATHENA's own store).
# ---------------------------------------------------------------------------

def _notes_path() -> Path:
    return APP_DATA_DIR / "notes.json"


def _load_notes() -> list[dict[str, Any]]:
    path = _notes_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


class SaveNoteTool(BaseTool):
    name = "save_note"
    description = "Save a short note in ATHENA's own local notes."
    category = "notes"
    scope = "brain"
    parameters = {
        "text": {"type": "string", "description": "The note text."},
    }
    required = ["text"]

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        missing = self._missing_args(args)
        if missing:
            return ToolResult.failure(f"Missing argument: {', '.join(missing)}.")
        notes = _load_notes()
        notes.append({
            "text": str(args["text"]),
            "created_at": datetime.now().isoformat(timespec="seconds"),
        })
        try:
            _notes_path().parent.mkdir(parents=True, exist_ok=True)
            _notes_path().write_text(json.dumps(notes, indent=2), encoding="utf-8")
        except OSError as exc:
            return ToolResult.failure(f"Could not save the note: {exc}")
        return ToolResult.success(f"Note saved ({len(notes)} total).", data={"count": len(notes)})


class ListNotesTool(BaseTool):
    name = "list_notes"
    description = "List ATHENA's own local notes."
    category = "notes"
    scope = "brain"
    parameters = {}
    required = []

    def run(self, args: dict[str, Any], context: ToolContext | None = None) -> ToolResult:
        notes = _load_notes()
        if not notes:
            return ToolResult.success("No notes saved yet.")
        lines = [f"{i + 1}. {n.get('text', '')} ({n.get('created_at', '')})"
                 for i, n in enumerate(notes)]
        return ToolResult.success("\n".join(lines), data={"notes": notes})
