"""ATHENA Python v1 — tool registry with auto-discovery.

Importing this module does NOT discover tools: call :func:`discover`
(or use the module-level :data:`REGISTRY`) so the UI integrator
controls when the scan happens. Discovery walks every module in the
``athena.tools`` package, registers every :class:`BaseTool` subclass
it finds, and never overrides an already-registered name — the first
tool with a name wins and later duplicates are recorded in
:attr:`Registry.duplicates` instead of silently replacing it.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Iterable

from .base_tool import BaseTool

__all__ = ["Registry", "REGISTRY", "discover", "list_tools", "get", "tools_by_category"]


class Registry:
    """A name -> tool instance map with first-wins duplicate handling."""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        #: (name, module) pairs that were skipped as duplicates.
        self.duplicates: list[tuple[str, str]] = []

    def register(self, tool: BaseTool) -> bool:
        """Register one tool instance. Returns ``False`` (and records
        the duplicate) when the name is already taken — never overrides."""
        if not tool.name:
            raise ValueError(f"{type(tool).__name__} has no name; cannot register")
        if tool.name in self._tools:
            self.duplicates.append((tool.name, type(tool).__module__))
            return False
        self._tools[tool.name] = tool
        return True

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        return sorted(self._tools.values(), key=lambda t: t.name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def tools_by_category(self, category: str) -> list[BaseTool]:
        return [t for t in self.list_tools() if t.category == category]

    def categories(self) -> list[str]:
        return sorted({t.category for t in self._tools.values()})


def discover(package_name: str = __package__ or "athena.tools") -> Registry:
    """Import every module in the tools package and register all
    :class:`BaseTool` subclasses found in them."""
    registry = Registry()
    package = importlib.import_module(package_name)
    for info in pkgutil.iter_modules(package.__path__):
        module = importlib.import_module(f"{package_name}.{info.name}")
        for _, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, BaseTool)
                and obj is not BaseTool
                and obj.__module__ == module.__name__
            ):
                try:
                    registry.register(obj())
                except Exception:
                    # A tool whose constructor needs something this
                    # machine lacks must not break discovery of the rest.
                    continue
    return registry


#: The process-wide registry, discovered once on first import of this
#: module's users (call :func:`discover` again for a fresh scan).
REGISTRY: Registry = discover()


def list_tools() -> list[BaseTool]:
    return REGISTRY.list_tools()


def get(name: str) -> BaseTool | None:
    return REGISTRY.get(name)


def tools_by_category(category: str) -> list[BaseTool]:
    return REGISTRY.tools_by_category(category)
