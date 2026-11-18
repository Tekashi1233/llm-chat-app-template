"""Layering tests: the Friday/MemOS module layout, enforced by parsing imports.

Allowed import direction (top-level package segments under ``athena``):

    interfaces -> router -> agents -> {tools, memory, reasoning, llm,
    awareness, execution, background, search, security} -> {config, utils, voice}

Concretely forbidden (and asserted here):

* anything outside ``interfaces`` importing ``athena.interfaces.*``;
* anything in tools / memory / reasoning / llm / awareness /
  execution / background / search / security / config / utils /
  voice importing ``athena.router.*`` or ``athena.agents.*``;
* anything in ``agents`` importing ``athena.interfaces.*`` or
  ``athena.voice.*``;
* ``config`` / ``utils`` / ``voice`` importing anything ``athena.*``
  outside themselves (voice may import ``config`` only).

``tests/`` themselves are excluded — test files may import anything.
Relative imports are resolved to their absolute ``athena.*`` form.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "athena"

# Top-level segments under athena.* (a "leaf" segment is the first
# component; interfaces/desktop/* still counts as "interfaces").
LEAF_PACKAGES = {
    "tools", "memory", "reasoning", "llm", "awareness", "execution",
    "background", "search", "security", "config", "utils", "voice",
}
NO_ROUTER_AGENTS = LEAF_PACKAGES  # none of these may import router/agents


def _segment(module: str) -> str:
    """First package segment of an ``athena.*`` module ('athena' itself -> '')."""
    parts = module.split(".")
    if parts[0] != "athena":
        return ""
    return parts[1] if len(parts) > 1 else ""


def _resolve_imports(path: Path) -> set[str]:
    """Absolute ``athena.*`` modules imported by ``path`` (any level)."""
    rel = path.relative_to(SRC).with_suffix("")
    parts = list(rel.parts)  # e.g. ['router', 'category_router'] or ['__init__']
    is_init = parts[-1] == "__init__"
    if is_init:
        parts = parts[:-1]
    # The containing package: for __init__.py it is the package
    # itself (athena.config), otherwise the parent (athena.router).
    pkg_parts = parts if is_init else parts[:-1]
    tree = ast.parse(path.read_text())
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    out.add(node.module)
            else:
                # relative: go up (level-1) from the containing package
                base = ["athena", *pkg_parts]
                up = node.level - 1
                base = base[: len(base) - up] if up else base
                if node.module:
                    out.add(".".join([*base, node.module]))
                else:
                    for alias in node.names:
                        out.add(".".join([*base, alias.name]))
    return {m for m in out if m == "athena" or m.startswith("athena.")}


def _all_modules() -> list[tuple[Path, str, set[str]]]:
    found = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).with_suffix("")
        parts = list(rel.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        module = "athena" + ("." + ".".join(parts) if parts else "")
        found.append((path, module, _resolve_imports(path)))
    return found


def test_no_module_outside_interfaces_imports_interfaces():
    bad = []
    for path, module, imports in _all_modules():
        if _segment(module) == "interfaces":
            continue
        for imp in sorted(imports):
            if _segment(imp) == "interfaces":
                bad.append(f"{module} -> {imp}")
    assert not bad, "layering violation (interfaces imported from below):\n" + "\n".join(bad)


def test_leaf_packages_never_import_router_or_agents():
    bad = []
    for path, module, imports in _all_modules():
        if _segment(module) not in NO_ROUTER_AGENTS:
            continue
        for imp in sorted(imports):
            if _segment(imp) in {"router", "agents"}:
                bad.append(f"{module} -> {imp}")
    assert not bad, "layering violation (leaf imported router/agents):\n" + "\n".join(bad)


def test_agents_never_import_interfaces_or_voice():
    bad = []
    for path, module, imports in _all_modules():
        if _segment(module) != "agents":
            continue
        for imp in sorted(imports):
            if _segment(imp) in {"interfaces", "voice"}:
                bad.append(f"{module} -> {imp}")
    assert not bad, "layering violation (agents imported interfaces/voice):\n" + "\n".join(bad)


def test_config_utils_voice_import_only_stdlib_and_each_other():
    bad = []
    for path, module, imports in _all_modules():
        seg = _segment(module)
        if seg == "config":
            allowed = {"config", "utils"}
        elif seg == "utils":
            allowed = {"config", "utils"}
        elif seg == "voice":
            allowed = {"config", "voice"}
        else:
            continue
        for imp in sorted(imports):
            imp_seg = _segment(imp)
            if imp_seg and imp_seg not in allowed:
                bad.append(f"{module} -> {imp}")
    assert not bad, "layering violation (config/utils/voice imported upward):\n" + "\n".join(bad)


def test_router_never_imports_interfaces():
    bad = []
    for path, module, imports in _all_modules():
        if _segment(module) != "router":
            continue
        for imp in sorted(imports):
            if _segment(imp) == "interfaces":
                bad.append(f"{module} -> {imp}")
    assert not bad, "layering violation (router imported interfaces):\n" + "\n".join(bad)
