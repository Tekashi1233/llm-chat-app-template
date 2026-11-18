"""ATHENA Python v1 — one logging setup used everywhere.

Call :func:`setup_logging` once at startup; everywhere else just
``logger = get_logger(__name__)``. Stdlib only.
"""

from __future__ import annotations

import logging
import sys

__all__ = ["get_logger", "setup_logging"]

_CONFIGURED = False


def setup_logging(level: int = logging.INFO) -> None:
    """Configure the root logger once (idempotent)."""
    global _CONFIGURED
    if _CONFIGURED:
        return
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """A module logger (call :func:`setup_logging` once at startup)."""
    return logging.getLogger(name)
