"""ATHENA Python v1 — local tools package.

Public interface for the UI integrator:

* :mod:`athena.tools.base_tool` — :class:`BaseTool`, :class:`ToolContext`,
  :class:`ToolResult`, :data:`ConfirmCallback`.
* :mod:`athena.tools.registry` — :func:`discover`, :func:`list_tools`,
  :func:`get`, :func:`tools_by_category`, :data:`REGISTRY`.
* :mod:`athena.tools.local_tools` — the concrete tools.
* :mod:`athena.tools.perception` — on-demand screen perception.

Routing moved to :mod:`athena.router` (``route``, ``dispatch``,
``category_overview``, ``category_schemas``) — tools must not import
the router (layering rule), so it is no longer re-exported here.

NOT built here, by design: Telegram bot (user closed Telegram),
cloud wake-word, MCP client (future only), LiveKit, Google Workspace
bridge (future optional only).
"""

from __future__ import annotations

from .base_tool import BaseTool, ConfirmCallback, ToolContext, ToolResult
from .registry import REGISTRY, discover, get, list_tools, tools_by_category

__all__ = [
    "BaseTool",
    "ConfirmCallback",
    "ToolContext",
    "ToolResult",
    "REGISTRY",
    "discover",
    "get",
    "list_tools",
    "tools_by_category",
]
