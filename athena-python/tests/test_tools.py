"""Tests for athena.tools — pure logic only.

Windows-only behaviour (win32gui active-window, app opening, ipconfig /
tasklist) is simulated-only here: on this Linux VM those tools must fail
honestly, and the tests assert exactly that. psutil and pytesseract ARE
installed in this venv, so the battery and OCR import paths are exercised
(the VM has no battery, no screen capture library and no Tesseract
binary, so both still answer honestly).
"""

from __future__ import annotations

import importlib.util

import pytest

from athena.router import dispatch, route
from athena.tools import REGISTRY, get, list_tools
from athena.tools.base_tool import BaseTool, ToolContext
from athena.tools.local_tools import evaluate_expression, suggest_extension
from athena.tools.perception import get_active_window, read_screen_text
from athena.tools.registry import Registry
from athena.router.category_router import CATEGORIES, category_overview, category_schemas

HAS_PSUTIL = importlib.util.find_spec("psutil") is not None
HAS_PYTESSERACT = importlib.util.find_spec("pytesseract") is not None


# ---------------------------------------------------------------------------
# Registry discovery
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "open_app", "search_files", "read_file", "create_file", "edit_file",
    "run_command", "battery_status", "active_window", "get_weather",
    "calculate", "get_datetime", "search_web", "youtube", "tab_control",
    "save_note", "list_notes", "read_screen",
}


def test_registry_discovers_all_tools():
    names = {t.name for t in list_tools()}
    assert EXPECTED_TOOLS <= names, f"missing: {EXPECTED_TOOLS - names}"
    assert REGISTRY.duplicates == []


def test_registry_get_and_unknown():
    assert get("calculate") is not None
    assert get("calculate").name == "calculate"
    assert get("no_such_tool") is None


def test_registry_never_overrides_duplicates():
    registry = Registry()

    class First(BaseTool):
        name = "dup_tool"
        description = "first"
        category = "calc"

    class Second(BaseTool):
        name = "dup_tool"
        description = "second"
        category = "calc"

    assert registry.register(First()) is True
    assert registry.register(Second()) is False  # never overrides
    assert registry.get("dup_tool").description == "first"
    assert registry.duplicates == [("dup_tool", Second.__module__)]


def test_registry_rejects_nameless_tool():
    registry = Registry()

    class Nameless(BaseTool):
        description = "no name"

    with pytest.raises(ValueError):
        registry.register(Nameless())


def test_tool_schema_shape():
    schema = get("calculate").to_schema()
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "calculate"
    assert fn["parameters"]["type"] == "object"
    assert "expression" in fn["parameters"]["properties"]
    assert fn["parameters"]["required"] == ["expression"]


def test_every_tool_has_category_and_schema():
    for tool in list_tools():
        assert tool.name and tool.description and tool.category
        assert tool.category in CATEGORIES
        assert tool.scope in ("general", "brain")
        schema = tool.to_schema()
        assert schema["function"]["name"] == tool.name


# ---------------------------------------------------------------------------
# Category routing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("intent,expected", [
    ("what's the weather in Riyadh", "weather"),
    ("is it going to rain tomorrow", "weather"),
    ("what is the weather in Riyadh", "weather"),
    ("what is the time in Tokyo", "time"),
    ("open notepad", "apps"),
    ("launch chrome", "apps"),
    ("find the file report.docx", "files"),
    ("calculate 250*1.15/3", "calc"),
    ("what time is it in Tokyo", "time"),
    ("search wikipedia for Marie Curie", "web"),
    ("play something on youtube", "media"),
    ("what's on my screen right now", "screen"),
    ("how much battery is left", "system"),
    ("ping google.com", "system"),
    ("save a note about the meeting", "notes"),
])
def test_route_keywords(intent, expected):
    assert route(intent) == expected


def test_route_unknown_falls_back_to_general():
    assert route("blorp zibble wobble") == "general"
    assert route("") == "general"


def test_category_overview_lists_all_categories():
    overview = category_overview()
    assert {c["name"] for c in overview} == set(CATEGORIES)
    assert all(c["description"] for c in overview)


def test_category_schemas_only_that_category():
    schemas = category_schemas("calc")
    assert [s["function"]["name"] for s in schemas] == ["calculate"]
    assert len(category_schemas("weather")) == 1
    # The whole point: never the full 60+ list at once.
    assert len(category_schemas("files")) == 4
    assert sum(len(category_schemas(c)) for c in CATEGORIES) == len(list_tools())


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

def test_dispatch_runs_tool_in_its_category():
    result = dispatch("calc", "calculate", {"expression": "2+3"})
    assert result.ok and "5" in result.text


def test_dispatch_refuses_wrong_category():
    result = dispatch("weather", "calculate", {"expression": "2+3"})
    assert not result.ok
    assert "calc" in result.text and "weather" in result.text


def test_dispatch_refuses_unknown_tool():
    result = dispatch("calc", "explode", {})
    assert not result.ok and "Unknown tool" in result.text


def test_dispatch_missing_args_is_honest_failure():
    result = dispatch("calc", "calculate", {})
    assert not result.ok and "Missing argument" in result.text


# ---------------------------------------------------------------------------
# Calculator (safe parser)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expr,expected", [
    ("250*1.15/3", 95.83333333333333),
    ("2+3*4", 14.0),
    ("(2+3)*4", 20.0),
    ("sqrt(16)", 4.0),
    ("2**10", 1024.0),
    ("10 % 3", 1.0),
])
def test_calculate_values(expr, expected):
    assert evaluate_expression(expr) == pytest.approx(expected)


def test_calculate_rejects_code_and_div_zero():
    result = dispatch("calc", "calculate", {"expression": "__import__('os').system('id')"})
    assert not result.ok
    result = dispatch("calc", "calculate", {"expression": "1/0"})
    assert not result.ok and "zero" in result.text.lower()
    result = dispatch("calc", "calculate", {"expression": "x + 1"})
    assert not result.ok


# ---------------------------------------------------------------------------
# Allowlist enforcement — refused names never run
# ---------------------------------------------------------------------------

def test_open_app_refuses_non_allowlisted_app():
    for bad in ("powershell", "cmd", "word", "notepad.exe --evil"):
        result = dispatch("apps", "open_app", {"app": bad})
        assert not result.ok, bad
        assert "approved apps" in result.text
    # Empty app name is refused too (as a missing argument).
    assert not dispatch("apps", "open_app", {"app": ""}).ok


def test_open_app_allowlisted_but_not_windows():
    # Notepad IS allowlisted; on this Linux VM the refusal must be
    # about the platform, not the allowlist.
    result = dispatch("apps", "open_app", {"app": "Notepad"})
    assert not result.ok
    assert "Windows-only" in result.text


def test_run_command_refuses_non_allowlisted_command():
    for bad in ("format", "del", "shutdown", "rm -rf /", "powershell"):
        result = dispatch("system", "run_command", {"command": bad})
        assert not result.ok, bad
        assert "approved commands" in result.text


def test_run_command_rejects_flag_smuggling():
    # Host must be a plain hostname — no flags, no shell metacharacters.
    for bad_host in ("google.com; rm -rf /", "-n 100 google.com", "google.com && id", "google.com|cat /etc/passwd"):
        result = dispatch("system", "run_command", {"command": "ping", "arg": bad_host})
        assert not result.ok, bad_host
        assert "Invalid host" in result.text


def test_run_command_ping_localhost_runs():
    # This sandbox blocks raw sockets, so ping may fail with
    # "Operation not permitted" here; on the real laptop it succeeds.
    # Either way the tool must attempt the approved command and report
    # the outcome honestly instead of crashing.
    result = dispatch("system", "run_command", {"command": "ping", "arg": "127.0.0.1"})
    assert result.text, "ping must produce some output or error"
    assert (
        "127.0.0.1" in result.text
        or "Operation not permitted" in result.text
        or "socket" in result.text.lower()
    )


def test_run_command_list_folder_rejects_outside_approved(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "athena.tools.local_tools.approved_folders", lambda: [tmp_path / "Docs"]
    )
    result = dispatch("system", "run_command", {"command": "list folder", "arg": "/etc"})
    assert not result.ok
    assert "approved folder" in result.text


# ---------------------------------------------------------------------------
# File tools + confirm-callback flow
# ---------------------------------------------------------------------------

@pytest.fixture
def approved_tmp(monkeypatch, tmp_path):
    """Point the approved folders at a temp dir for file tests."""
    docs = tmp_path / "Documents"
    docs.mkdir()
    monkeypatch.setattr(
        "athena.tools.local_tools.approved_folders", lambda: [docs]
    )
    return docs


def _allow(summary, details):
    return True


def _deny(summary, details):
    return False


def test_create_file_with_allow_writes(approved_tmp):
    target = approved_tmp / "hello.txt"
    result = dispatch("files", "create_file",
                      {"path": str(target), "content": "hi"},
                      ToolContext(confirm=_allow))
    assert result.ok, result.text
    assert target.read_text() == "hi"


def test_create_file_with_deny_writes_nothing(approved_tmp):
    target = approved_tmp / "nope.txt"
    result = dispatch("files", "create_file",
                      {"path": str(target), "content": "hi"},
                      ToolContext(confirm=_deny))
    assert not result.ok
    assert not target.exists()


def test_create_file_without_confirmer_refuses(approved_tmp):
    target = approved_tmp / "orphan.txt"
    result = dispatch("files", "create_file",
                      {"path": str(target), "content": "hi"},
                      ToolContext(confirm=None))
    assert not result.ok
    assert "confirmer" in result.text
    assert not target.exists()


def test_create_file_auto_deny_simulated(approved_tmp):
    """The UI's 60s auto-deny arrives as confirm() -> False; the tool
    must treat it exactly like an explicit Deny."""
    def expired_confirmer(summary, details):
        return False  # 60 seconds passed with no tap

    target = approved_tmp / "expired.txt"
    result = dispatch("files", "create_file",
                      {"path": str(target), "content": "hi"},
                      ToolContext(confirm=expired_confirmer))
    assert not result.ok
    assert not target.exists()


def test_create_file_outside_approved_refused(tmp_path):
    outside = tmp_path / "elsewhere" / "x.txt"
    result = dispatch("files", "create_file",
                      {"path": str(outside), "content": "hi"},
                      ToolContext(confirm=_allow))
    assert not result.ok
    assert "approved folder" in result.text
    assert not outside.exists()


def test_edit_file_replace_and_append(approved_tmp):
    target = approved_tmp / "notes.txt"
    target.write_text("hello world")
    result = dispatch("files", "edit_file",
                      {"path": str(target), "old_text": "world", "new_text": "athena"},
                      ToolContext(confirm=_allow))
    assert result.ok, result.text
    assert target.read_text() == "hello athena"
    result = dispatch("files", "edit_file",
                      {"path": str(target), "new_text": "!", "mode": "append"},
                      ToolContext(confirm=_allow))
    assert result.ok
    assert target.read_text() == "hello athena!"


def test_edit_file_deny_leaves_file_untouched(approved_tmp):
    target = approved_tmp / "keep.txt"
    target.write_text("original")
    result = dispatch("files", "edit_file",
                      {"path": str(target), "old_text": "original", "new_text": "changed"},
                      ToolContext(confirm=_deny))
    assert not result.ok
    assert target.read_text() == "original"


def test_edit_file_missing_old_text_changes_nothing(approved_tmp):
    target = approved_tmp / "keep2.txt"
    target.write_text("original")
    result = dispatch("files", "edit_file",
                      {"path": str(target), "old_text": "absent", "new_text": "x"},
                      ToolContext(confirm=_allow))
    assert not result.ok
    assert target.read_text() == "original"


def test_search_and_read_files(approved_tmp):
    (approved_tmp / "report-2026.txt").write_text("quarterly numbers")
    (approved_tmp / "other.md").write_text("unrelated")
    result = dispatch("files", "search_files", {"query": "report"})
    assert result.ok
    assert "report-2026.txt" in result.text
    assert "other.md" not in result.text
    result = dispatch("files", "read_file", {"path": str(approved_tmp / "report-2026.txt")})
    assert result.ok and "quarterly" in result.text


def test_read_file_outside_approved_refused(tmp_path):
    victim = tmp_path / "secret.txt"
    victim.write_text("do not read")
    result = dispatch("files", "read_file", {"path": str(victim)})
    assert not result.ok
    assert "approved folder" in result.text


def test_suggest_extension():
    assert suggest_extension("create a python file") == ".py"
    assert suggest_extension("make a markdown file") == ".md"
    assert suggest_extension("something vague") == ".txt"


# ---------------------------------------------------------------------------
# Battery — psutil IS installed in this venv, so the import path is real.
# The VM has no battery, so the honest answer is "no battery".
# ---------------------------------------------------------------------------

def test_battery_status():
    if not HAS_PSUTIL:
        pytest.skip("psutil not installed")
    import psutil
    result = dispatch("system", "battery_status", {})
    if psutil.sensors_battery() is None:
        assert not result.ok
        assert "No battery" in result.text
    else:
        assert result.ok
        assert 0 <= result.data["percent"] <= 100


# ---------------------------------------------------------------------------
# Weather / web — network faked with monkeypatched httpx
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ValueError(f"HTTP {self.status_code}")


def test_get_weather_with_fake_http(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if "geocoding" in url:
            return _FakeResponse({"results": [{
                "name": "Riyadh", "country": "Saudi Arabia",
                "latitude": 24.7136, "longitude": 46.6753,
            }]})
        return _FakeResponse({"current": {
            "temperature_2m": 31.0, "apparent_temperature": 33.0,
            "relative_humidity_2m": 20, "weather_code": 0,
            "wind_speed_10km_h": 12.0, "wind_direction_10km_h": 90,
        }})

    monkeypatch.setattr("athena.tools.local_tools.httpx.get", fake_get)
    result = dispatch("weather", "get_weather", {"place": "Riyadh"})
    assert result.ok, result.text
    assert "Riyadh" in result.text and "31.0" in result.text
    assert result.data["temperature_c"] == 31.0
    assert len(calls) == 2


def test_get_weather_unknown_place(monkeypatch):
    monkeypatch.setattr(
        "athena.tools.local_tools.httpx.get",
        lambda url, params=None, timeout=None: _FakeResponse({"results": []}),
    )
    result = dispatch("weather", "get_weather", {"place": "Nowhereville"})
    assert not result.ok and "Could not find" in result.text


def test_search_web_with_fake_http(monkeypatch):
    def fake_get(url, timeout=None):
        assert "Marie_Curie" in url or "Marie%20Curie" in url
        return _FakeResponse({
            "title": "Marie Curie",
            "extract": "Polish physicist, pioneer of radioactivity.",
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Marie_Curie"}},
        })

    monkeypatch.setattr("athena.tools.local_tools.httpx.get", fake_get)
    result = dispatch("web", "search_web", {"query": "Marie Curie"})
    assert result.ok
    assert "radioactivity" in result.text
    assert "wikipedia.org" in result.text


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------

def test_get_datetime_riyadh():
    result = dispatch("time", "get_datetime", {"timezone": "Asia/Riyadh"})
    assert result.ok
    assert result.data["timezone"] == "Asia/Riyadh"
    assert "2026" in result.text


def test_get_datetime_bad_timezone():
    result = dispatch("time", "get_datetime", {"timezone": "Mars/Olympus"})
    assert not result.ok and "Unknown timezone" in result.text


# ---------------------------------------------------------------------------
# Media — keystroke actions need pyautogui on Windows; honest failure here
# ---------------------------------------------------------------------------

def test_youtube_search_opens_browser(monkeypatch):
    opened = []
    monkeypatch.setattr("athena.tools.local_tools.webbrowser.open",
                        lambda url: opened.append(url))
    result = dispatch("media", "youtube", {"action": "search", "query": "cats"})
    assert result.ok
    assert opened and "youtube.com" in opened[0] and "cats" in opened[0]


def test_tab_control_without_pyautogui_fails_honestly():
    result = dispatch("media", "tab_control", {"action": "new_tab"})
    assert not result.ok
    assert "pyautogui" in result.text or "Windows-only" in result.text


def test_youtube_unknown_action():
    result = dispatch("media", "youtube", {"action": "dance"})
    assert not result.ok and "Unknown YouTube action" in result.text


# ---------------------------------------------------------------------------
# Notes (ATHENA's own store — no confirmer needed)
# ---------------------------------------------------------------------------

def test_save_and_list_notes(monkeypatch, tmp_path):
    monkeypatch.setattr("athena.tools.local_tools.APP_DATA_DIR", tmp_path)
    result = dispatch("notes", "save_note", {"text": "buy milk"})
    assert result.ok
    result = dispatch("notes", "list_notes", {})
    assert result.ok and "buy milk" in result.text
    assert (tmp_path / "notes.json").is_file()


# ---------------------------------------------------------------------------
# Perception guards — nothing is captured unless explicitly called
# ---------------------------------------------------------------------------

def test_active_window_unavailable_off_windows():
    info = get_active_window()
    assert info["available"] is False
    assert "pywin32" in info["reason"]
    result = dispatch("screen", "active_window", {})
    assert not result.ok


def test_read_screen_text_without_capture_lib_fails_honestly(tmp_path, monkeypatch):
    # pyautogui is not installed in this venv, so the OCR path must
    # refuse before touching any screen — and must not create any file.
    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    result = read_screen_text()
    assert not result.ok
    assert "unavailable" in result.text.lower()
    assert set(tmp_path.iterdir()) == before  # no screenshot saved


def test_pytesseract_imports():
    # The OCR backend is pytesseract now (rapidocr-onnxruntime was
    # removed): the package must be importable in this venv.
    if not HAS_PYTESSERACT:
        pytest.skip("pytesseract not installed")
    import pytesseract  # noqa: F401
    assert pytesseract is not None


def test_read_screen_text_with_fake_ocr(monkeypatch, tmp_path):
    # Fake both guarded imports: pyautogui screenshot + pytesseract.
    # The real Tesseract binary is absent on this VM, so the OCR call
    # itself is faked — this proves the wiring, not the engine.
    import sys
    import types

    fake_shot = object()

    fake_pyautogui = types.ModuleType("pyautogui")
    fake_pyautogui.screenshot = lambda: fake_shot
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    seen = {}

    class FakeTesseract:
        def image_to_string(self, image):
            seen["image"] = image
            return "Hello\nWorld\n"

    fake_ocr = types.ModuleType("pytesseract")
    fake_ocr.image_to_string = FakeTesseract().image_to_string
    monkeypatch.setitem(sys.modules, "pytesseract", fake_ocr)

    monkeypatch.chdir(tmp_path)
    before = set(tmp_path.iterdir())
    result = read_screen_text()
    assert result.ok and "Hello" in result.text and "World" in result.text
    assert result.data == {"lines": ["Hello", "World"]}
    assert seen["image"] is fake_shot
    assert set(tmp_path.iterdir()) == before  # no screenshot saved


def test_read_screen_text_missing_tesseract_binary_is_honest(monkeypatch):
    # pyautogui present, pytesseract present, but the Tesseract binary
    # missing -> the engine raises TesseractNotFoundError; the call
    # must fail honestly, naming Tesseract, never crash.
    import sys
    import types

    import pytesseract as real_ocr

    fake_pyautogui = types.ModuleType("pyautogui")
    fake_pyautogui.screenshot = lambda: object()
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)

    def _raise(image):
        raise real_ocr.TesseractNotFoundError()

    fake_ocr = types.ModuleType("pytesseract")
    fake_ocr.image_to_string = _raise
    fake_ocr.TesseractNotFoundError = real_ocr.TesseractNotFoundError
    monkeypatch.setitem(sys.modules, "pytesseract", fake_ocr)

    result = read_screen_text()
    assert not result.ok
    assert "tesseract" in result.text.lower()


def test_perception_module_has_no_monitor():
    # No background thread / continuous capture may exist in perception.
    import athena.tools.perception as perception
    assert not hasattr(perception, "start_window_monitor")
    assert not hasattr(perception, "start_monitor")
    import inspect
    source = inspect.getsource(perception)
    assert "threading" not in source
    assert "while True" not in source
