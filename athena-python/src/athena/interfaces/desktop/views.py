"""ATHENA Python v1 — section panels (the six "···" menu sections).

Ports of the C# views: ConversationsView, MemoryVaultView,
ToolsView, DevicesView, ActivityView. Each panel is read-only
except where noted, shows live data through the injected worker
client where available, and otherwise says so honestly
("not enabled yet" / offline) instead of inventing data.

Rules carried over from the C# code:

* A failed refresh never leaves stale rows on screen — the panel
  is cleared and shows what happened plus what to do next.
* Devices quick actions send approved chat prompts (the model,
  Worker and Companion enforce their allowlists exactly as if the
  user had typed them); buttons stay disabled while offline.
* The Unpair card NEVER fakes a revoke: the revoke needs the
  device secret, which lives only in the Companion tray app — the
  site key cannot revoke (Worker answers 401). It shows the exact
  tray-app steps instead.
* Memory Vault is read-only: memories are never altered or deleted
  from the view, and each row shows its provenance.
* Conversation titles come from the first user message; nothing
  is invented.

Data fetching is duck-typed: the worker is probed for
``get_backup``, ``get_memories``, ``get_model_info``,
``get_device_status`` and ``get_device_activity``. A missing method
means "not enabled yet" — the panel says so and never crashes.
Fetches run in a worker thread (sync or async callables both
work); results arrive back on the GUI thread.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import theme

__all__ = [
    "NOT_ENABLED_DEVICE",
    "NOT_ENABLED_MEMORIES",
    "NOT_ENABLED_ACTIVITY",
    "DEVICE_QUICK_ACTIONS",
    "UNPAIR_STEPS",
    "TOOL_CATALOG",
    "format_age",
    "format_relative_time",
    "snippet",
    "group_conversations",
    "filter_memories",
    "is_approved_device_prompt",
    "ConversationsPanel",
    "MemoryVaultPanel",
    "ToolsPanel",
    "DevicesPanel",
    "ActivityPanel",
]

# --- Pure data lives in views_data.py (no Qt) — re-exported here ---
from .views_data import (
    NOT_ENABLED_ACTIVITY,
    NOT_ENABLED_DEVICE,
    NOT_ENABLED_MEMORIES,
    NO_MEMORY_SELECTED,
    RETRY_HINT_REFRESH,
    RETRY_HINT_REOPEN,
    TOOL_CATALOG,
    UNPAIR_STEPS,
    DEVICE_QUICK_ACTIONS,
    ConversationSummary,
    _get,
    filter_memories,
    format_age,
    format_relative_time,
    group_conversations,
    is_approved_device_prompt,
    snippet,
)


# --- Async fetch helper ----------------------------------------------------

class _FetchWorker(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self._fn = fn

    def run(self) -> None:
        try:
            result = self._fn()
            if inspect.isawaitable(result):
                result = asyncio.run(result)
            self.finished.emit(result)
        except Exception as exc:
            self.failed.emit(f"{type(exc).__name__}: {exc}")


def _run_in_thread(parent: QWidget, fn: Callable[[], Any], on_done, on_fail) -> None:
    thread = QThread(parent)
    worker = _FetchWorker(fn)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(on_done)
    worker.failed.connect(on_fail)
    worker.finished.connect(thread.quit)
    worker.failed.connect(thread.quit)
    thread.finished.connect(thread.deleteLater)
    thread.start()


# --- Shared widget helpers ---------------------------------------------------

def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    frame.setStyleSheet(theme.CARD_QSS)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(14, 10, 14, 10)
    label = QLabel(title)
    label.setStyleSheet(f"color: {theme.GOLD}; font-weight: 600; font-size: 13px;")
    layout.addWidget(label)
    return frame, layout


def _body(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {theme.TEXT}; font-size: 13px;")
    return label


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
    return label


def _status_line(text: str, colour: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet(f"color: {colour}; font-weight: 600; font-size: 13px;")
    return label


def _row_button(content: QWidget) -> QPushButton:
    button = QPushButton()
    button.setFlat(True)
    button.setStyleSheet(theme.GHOST_BUTTON_QSS)
    inner = QVBoxLayout(button)
    inner.setContentsMargins(10, 8, 10, 8)
    inner.addWidget(content)
    return button


class _PanelBase(QWidget):
    """Scrollable panel with a clear-and-show-error refresh contract:
    a failed refresh never leaves stale rows on screen."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        scroller = QScrollArea(self)
        scroller.setWidgetResizable(True)
        scroller.setStyleSheet("QScrollArea { border: none; }")
        self._content = QWidget()
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(8, 0, 8, 16)
        self._layout.setSpacing(12)
        self._layout.addStretch(1)
        scroller.setWidget(self._content)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroller)

    def _clear(self) -> None:
        while self._layout.count() > 1:  # keep the stretch
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _add(self, widget: QWidget) -> None:
        self._layout.insertWidget(self._layout.count() - 1, widget)

    def _show_error(self, error: str, hint: str) -> None:
        self._clear()
        self._add(_body(error))
        self._add(_muted(hint))

    def _show_loading(self) -> None:
        self._clear()
        self._add(_muted("Loading…"))


# --- Conversations ------------------------------------------------------------

class ConversationsPanel(_PanelBase):
    """Conversation list from the /api/backup snapshot."""

    def __init__(
        self,
        worker: Any | None = None,
        on_open: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker = worker
        self._on_open = on_open
        self._all: list[ConversationSummary] = []

        search_row = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search conversations…")
        self._search.setStyleSheet(theme.INPUT_QSS)
        self._search.textChanged.connect(lambda _t: self._render())
        search_row.addWidget(self._search, 1)
        new_button = QPushButton("New")
        new_button.setStyleSheet(theme.GOLD_BUTTON_QSS)
        new_button.clicked.connect(self._on_new)
        search_row.addWidget(new_button)
        wrap = QWidget()
        wrap.setLayout(search_row)
        self._add(wrap)
        self.refresh()

    def refresh(self) -> None:
        fetch = getattr(self._worker, "get_backup", None) if self._worker else None
        if not callable(fetch):
            self._show_error(
                "Sign in first to see conversations." if self._worker is None
                else "Conversations aren't enabled yet — the backup endpoint isn't available.",
                RETRY_HINT_REOPEN,
            )
            return
        self._show_loading()
        _run_in_thread(
            self,
            lambda: fetch(),
            self._on_backup,
            lambda e: self._show_error(f"Couldn't load conversations — {e}", RETRY_HINT_REOPEN),
        )

    def _on_backup(self, backup: Any) -> None:
        # A failed refresh clears the cached rows — typing in the
        # search box afterwards must not resurrect them as current.
        self._all = []
        messages = _get(backup, "messages", []) or []
        self._all = group_conversations(list(messages))
        self._render()

    def _render(self) -> None:
        # Rebuild everything below the search row.
        while self._layout.count() > 2:
            item = self._layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        query = self._search.text().strip().lower()
        shown = [
            c for c in self._all
            if not query or query in c.title.lower() or query in c.last_snippet.lower()
        ]
        if not shown:
            self._add(
                _muted(
                    "No conversations yet. Say hello in Chat."
                    if not self._all
                    else "Nothing matches that search."
                )
            )
            return
        for conv in shown:
            stack = QWidget()
            v = QVBoxLayout(stack)
            v.setContentsMargins(0, 0, 0, 0)
            title = QLabel(conv.title)
            title.setStyleSheet(f"color: {theme.TEXT}; font-weight: 600; font-size: 14px;")
            title.setWordWrap(True)
            meta = QLabel(
                f"{format_relative_time(conv.last_message_at)} · "
                f"{conv.message_count} messages"
            )
            meta.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
            snip = QLabel(conv.last_snippet)
            snip.setWordWrap(True)
            snip.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
            v.addWidget(title)
            v.addWidget(meta)
            v.addWidget(snip)
            button = _row_button(stack)
            button.clicked.connect(lambda _c, cid=conv.id: self._open(cid))
            self._add(button)

    def _open(self, conversation_id: str) -> None:
        if self._on_open is not None:
            self._on_open(conversation_id)

    def _on_new(self) -> None:
        if self._on_open is not None:
            self._on_open("")


# --- Memory Vault ---------------------------------------------------------------

class MemoryVaultPanel(_PanelBase):
    """Read-only Memory Vault with provenance. Memories are never
    altered or deleted from this view."""

    def __init__(
        self,
        worker: Any | None = None,
        conversation_id: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker = worker
        self._conversation_id = conversation_id
        self._all: list[dict] = []
        self._loaded = False

        filters = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Search memories…")
        self._search.setStyleSheet(theme.INPUT_QSS)
        self._search.textChanged.connect(lambda _t: self._render() if self._loaded else None)
        filters.addWidget(self._search, 1)
        self._kind_box = QComboBox()
        self._kind_box.addItems(["All", "fact", "preference", "project", "plan"])
        self._kind_box.currentTextChanged.connect(lambda _t: self._render() if self._loaded else None)
        filters.addWidget(self._kind_box)
        self._status_box = QComboBox()
        self._status_box.addItems(["All", "active", "superseded"])
        self._status_box.currentTextChanged.connect(lambda _t: self._render() if self._loaded else None)
        filters.addWidget(self._status_box)
        wrap = QWidget()
        wrap.setLayout(filters)
        self._add(wrap)

        self._detail_card, self._detail_body = _card("Details")
        self._detail_body.addWidget(_muted(NO_MEMORY_SELECTED))
        self._add(self._detail_card)
        self.refresh()

    def refresh(self) -> None:
        self._loaded = False
        fetch = getattr(self._worker, "get_memories", None) if self._worker else None
        if not callable(fetch):
            self._show_error(
                "Sign in first to see memory." if self._worker is None else NOT_ENABLED_MEMORIES,
                RETRY_HINT_REOPEN,
            )
            return
        self._show_loading()
        cid = self._conversation_id
        _run_in_thread(
            self,
            lambda: fetch(cid) if cid else fetch(),
            self._on_memories,
            lambda e: self._show_error(f"Couldn't load memories — {e}", RETRY_HINT_REOPEN),
        )

    def _on_memories(self, result: Any) -> None:
        self._all = []
        self._loaded = False
        memories = _get(result, "memories", result) or []
        if not isinstance(memories, list):
            memories = []
        self._all = list(memories)
        self._loaded = True
        self._render()

    def _render(self) -> None:
        # Rebuild the list rows (search row at 0, detail card last).
        while self._layout.count() > 2:
            item = self._layout.takeAt(1)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        if not self._loaded:
            return
        if not self._all:
            self._add(
                _muted(
                    "Nothing remembered in this conversation yet. Ask "
                    "ATHENA to remember something in chat."
                )
            )
            return
        shown = filter_memories(
            self._all,
            query=self._search.text(),
            kind=self._kind_box.currentText(),
            status=self._status_box.currentText(),
        )
        if not shown:
            self._add(_muted("Nothing matches those filters."))
            return
        for memory in shown:
            stack = QWidget()
            v = QVBoxLayout(stack)
            v.setContentsMargins(0, 0, 0, 0)
            title = QLabel(str(_get(memory, "topic") or "—"))
            title.setStyleSheet(f"color: {theme.TEXT}; font-weight: 600; font-size: 14px;")
            title.setWordWrap(True)
            meta = QLabel(
                f"{_get(memory, 'kind') or '—'} · {_get(memory, 'project') or '—'} · "
                f"{_get(memory, 'status') or '—'} · {_get(memory, 'recorded_date') or _get(memory, 'recordedDate') or '—'}"
            )
            meta.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
            snip = QLabel(snippet(_get(memory, "fact")))
            snip.setWordWrap(True)
            snip.setStyleSheet(f"color: {theme.MUTED}; font-size: 12px;")
            v.addWidget(title)
            v.addWidget(meta)
            v.addWidget(snip)
            button = _row_button(stack)
            button.clicked.connect(lambda _c, m=memory: self._show_detail(m))
            self._add(button)

    def _show_detail(self, memory: dict) -> None:
        while self._detail_body.count():
            item = self._detail_body.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        self._detail_body.addWidget(_body(str(_get(memory, "topic") or "—")))
        self._detail_body.addWidget(_body(str(_get(memory, "fact") or "")))
        self._detail_body.addWidget(_muted(f"Kind: {_get(memory, 'kind') or '—'}"))
        self._detail_body.addWidget(_muted(f"Project: {_get(memory, 'project') or '—'}"))
        self._detail_body.addWidget(
            _muted(f"Source: {_get(memory, 'source_label') or _get(memory, 'source') or '—'}")
        )
        self._detail_body.addWidget(
            _muted(f"Recorded: {_get(memory, 'recorded_date') or _get(memory, 'recordedDate') or '—'}")
        )
        self._detail_body.addWidget(_muted(f"Status: {_get(memory, 'status') or '—'}"))
        self._detail_body.addWidget(_muted(f"Id: {_get(memory, 'id') or '—'}"))


# --- Tools -------------------------------------------------------------------------

class ToolsPanel(_PanelBase):
    """The registered tools, as documentation: name, description,
    category and whether the server currently lists the tool.
    Nothing here executes anything."""

    def __init__(
        self,
        worker: Any | None = None,
        tools: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker = worker
        self._tools = tools
        self.refresh()

    def _catalog(self) -> list[tuple[str, str, str]]:
        """Tool list: the injected registry wins; otherwise the
        static catalogue (C# ToolCatalog)."""
        if self._tools is not None:
            names = getattr(self._tools, "list", None) or getattr(self._tools, "all", None)
            items = names() if callable(names) else names
            if items:
                out = []
                for item in items:
                    out.append(
                        (
                            str(_get(item, "name")),
                            str(_get(item, "description") or ""),
                            str(_get(item, "category") or "—"),
                        )
                    )
                return out
        return list(TOOL_CATALOG)

    def refresh(self) -> None:
        fetch = getattr(self._worker, "get_model_info", None) if self._worker else None
        if not callable(fetch):
            self._render(None, unknown=True)
            return
        self._show_loading()
        _run_in_thread(
            self,
            lambda: fetch(),
            lambda info: self._render(info),
            lambda e: self._render(None, error=f"Couldn't read model info — {e}"),
        )

    def _render(self, model_info: Any, unknown: bool = False, error: str | None = None) -> None:
        self._clear()
        if error:
            self._add(_body(error))
            self._add(_muted(RETRY_HINT_REOPEN))
        registered = None
        if model_info is not None:
            tools = _get(model_info, "tools", []) or []
            registered = {str(t) for t in tools}
        for name, description, category in self._catalog():
            card, body = _card(name)
            body.addWidget(_body(description))
            body.addWidget(_muted(f"Category: {category}"))
            if unknown or registered is None:
                body.addWidget(
                    _status_line(
                        "Availability unknown — couldn't read model info.", theme.MUTED
                    )
                )
            elif name in registered:
                body.addWidget(_status_line("Registered", "#4CAF50"))
            else:
                body.addWidget(_status_line("Not listed by the server", theme.MUTED))
            self._add(card)


# --- Devices -------------------------------------------------------------------------

class DevicesPanel(_PanelBase):
    """Paired Windows laptop, from the Companion heartbeat via the
    N2 device-status endpoint. Read-only: pairing and permissions
    stay in the Companion tray app on the laptop."""

    def __init__(
        self,
        worker: Any | None = None,
        send_device_command: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._worker = worker
        self._send_device_command = send_device_command
        self._action_buttons: list[QPushButton] = []

        refresh_button = QPushButton("Refresh")
        refresh_button.setStyleSheet(theme.GOLD_BUTTON_QSS)
        refresh_button.clicked.connect(self.refresh)
        self._add(refresh_button)
        self.refresh()

    def refresh(self) -> None:
        fetch = getattr(self._worker, "get_device_status", None) if self._worker else None
        if not callable(fetch):
            self._show_error(
                "Sign in first to see device status." if self._worker is None
                else NOT_ENABLED_DEVICE,
                RETRY_HINT_REFRESH,
            )
            return
        self._show_loading()
        _run_in_thread(
            self,
            lambda: fetch(),
            self._on_status,
            lambda e: self._show_error(f"Couldn't load device status — {e}", RETRY_HINT_REFRESH),
        )

    def _on_status(self, status: Any) -> None:
        self._clear()
        if status is None:
            self._add(_muted(NOT_ENABLED_DEVICE))
            return
        device = _get(status, "device") or _get(status, "name") or "Paired laptop"
        online = bool(_get(status, "online"))
        card, body = _card(str(device))
        body.addWidget(
            _status_line("Online" if online else "Offline", "#4CAF50" if online else "#E88A8A")
        )
        reason = _get(status, "reason")
        if not online and reason:
            body.addWidget(_body(f"Reason: {reason}"))
        cpu = _get(status, "cpu_percent")
        if cpu is not None:
            body.addWidget(_body(f"CPU: {float(cpu):.1f}%"))
        ram_used = _get(status, "ram_used_gb")
        ram_total = _get(status, "ram_total_gb")
        if ram_used is not None and ram_total is not None:
            body.addWidget(_body(f"RAM: {float(ram_used):.1f} GB used of {float(ram_total):.1f} GB"))
        disks = _get(status, "disks") or []
        for disk in disks:
            body.addWidget(
                _body(
                    f"Disk {_get(disk, 'drive')}: {float(_get(disk, 'free_gb') or 0):.1f} GB "
                    f"free of {float(_get(disk, 'total_gb') or 0):.1f} GB"
                )
            )
        network = _get(status, "network")
        if network:
            down = _get(network, "down_mbps")
            up = _get(network, "up_mbps")
            body.addWidget(
                _body(
                    f"Network: {_get(network, 'interface') or '—'} — down "
                    f"{f'{float(down):.1f} Mbps' if down is not None else '—'}, up "
                    f"{f'{float(up):.1f} Mbps' if up is not None else '—'}"
                )
            )
        uptime = _get(status, "uptime_hours")
        if uptime is not None:
            body.addWidget(_body(f"Uptime: {float(uptime):.1f} hours"))
        last_seen = _get(status, "last_seen_seconds_ago")
        body.addWidget(
            _body(
                "Last seen: unknown"
                if last_seen is None
                else f"Last seen: {format_age(last_seen)}"
            )
        )
        self._add(card)

        # One-tap device commands — through chat, so the model,
        # Worker and Companion enforce their allowlists exactly as
        # if the user had typed them.
        run_card, run_body = _card("Run on your laptop")
        run_body.addWidget(
            _muted(
                "These send a message in chat — ATHENA runs them on the "
                "laptop behind its allowlist. File writes still ask you "
                "on the laptop screen first."
            )
        )
        self._action_buttons = []
        for label, prompt in DEVICE_QUICK_ACTIONS:
            button = QPushButton(label)
            button.setStyleSheet(theme.GHOST_BUTTON_QSS)
            button.setEnabled(online and self._send_device_command is not None)
            button.clicked.connect(
                lambda _c, p=prompt, b=button: self._run_action(p, b)
            )
            self._action_buttons.append(button)
            run_body.addWidget(button)
        if not online:
            run_body.addWidget(
                _muted("The laptop is offline, so commands are disabled until it is back.")
            )
        self._add(run_card)

        # Unpair. The revoke needs the device secret, which lives
        # only in the Companion app — the site key cannot revoke.
        # This confirms the intent here and shows the exact steps
        # on the laptop. Nothing is revoked from this app.
        unpair_card, unpair_body = _card("Unpair this laptop")
        unpair_body.addWidget(
            _muted(
                "Revoking unpairs the laptop server-side: the Companion "
                "stops, queued commands are cleared, and the laptop must "
                "be paired again with a fresh code."
            )
        )
        unpair_button = QPushButton("Unpair this laptop…")
        unpair_button.setStyleSheet(theme.GOLD_BUTTON_QSS)
        unpair_button.clicked.connect(
            lambda: unpair_body.addWidget(_body(UNPAIR_STEPS))
        )
        unpair_body.addWidget(unpair_button)
        self._add(unpair_card)

        self._add(
            _muted(
                "Tip: saying “wolf” into the mic unpairs this laptop — "
                "there is no button for it."
            )
        )
        self._add(
            _muted(
                "Approvals appear as a popup on the laptop screen — "
                "ATHENA never approves anything by herself."
            )
        )
        self._add(
            _muted(
                "Pairing and permissions are managed in the Windows "
                "Companion tray app on the laptop."
            )
        )

    def _run_action(self, prompt: str, button: QPushButton) -> None:
        if not is_approved_device_prompt(prompt):
            return  # only approved prompts go down this path
        if self._send_device_command is None:
            return
        button.setEnabled(False)
        try:
            self._send_device_command(prompt)
        finally:
            # Re-check the laptop before re-enabling: the turn may
            # have ended in error and the laptop may have gone
            # offline while it ran. Never re-enable from stale
            # knowledge — refresh instead.
            self.refresh()


# --- Activity ----------------------------------------------------------------------------

class ActivityPanel(_PanelBase):
    """Read-only audit timeline from the device_actions table via
    the N2 activity endpoint. Rows are displayed, never modified."""

    def __init__(self, worker: Any | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._worker = worker
        refresh_button = QPushButton("Refresh")
        refresh_button.setStyleSheet(theme.GOLD_BUTTON_QSS)
        refresh_button.clicked.connect(self.refresh)
        self._add(refresh_button)
        self.refresh()

    def refresh(self) -> None:
        fetch = getattr(self._worker, "get_device_activity", None) if self._worker else None
        if not callable(fetch):
            self._show_error(
                "Sign in first to see activity." if self._worker is None
                else NOT_ENABLED_ACTIVITY,
                RETRY_HINT_REFRESH,
            )
            return
        self._show_loading()
        _run_in_thread(
            self,
            lambda: fetch(50),
            self._on_activity,
            lambda e: self._show_error(f"Couldn't load activity — {e}", RETRY_HINT_REFRESH),
        )

    def _on_activity(self, result: Any) -> None:
        self._clear()
        if result is None:
            self._add(_muted(NOT_ENABLED_ACTIVITY))
            return
        actions = _get(result, "actions", result) or []
        if not isinstance(actions, list):
            actions = []
        if not actions:
            self._add(_muted("No activity recorded yet."))
            return
        for action in actions:
            card, body = _card(str(_get(action, "kind") or "—"))
            body.addWidget(_body(str(_get(action, "detail") or "")))
            body.addWidget(
                _muted(format_relative_time(_get(action, "created_at")))
            )
            approved = bool(_get(action, "was_approved"))
            body.addWidget(
                _status_line("Approved" if approved else "Refused",
                            "#4CAF50" if approved else "#E88A8A")
            )
            self._add(card)
