"""ATHENA Python v1 — router package.

Two layers: :mod:`athena.router.category_router` (keyword category
dispatch — the model sees categories first, then one category's
schemas) and :mod:`athena.router.smart_router` (tiered SIMPLE /
MEDIUM / COMPLEX / CLARIFY routing on top).
"""

from __future__ import annotations

from athena.router.category_router import (
    CATEGORIES,
    CATEGORY_DESCRIPTIONS,
    category_overview,
    category_schemas,
    dispatch,
    route,
)
from athena.router.smart_router import MAX_CLARIFICATIONS, RouteDecision, SmartRouter, Tier

__all__ = [
    "CATEGORIES",
    "CATEGORY_DESCRIPTIONS",
    "MAX_CLARIFICATIONS",
    "RouteDecision",
    "SmartRouter",
    "Tier",
    "category_overview",
    "category_schemas",
    "dispatch",
    "route",
]
