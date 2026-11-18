"""Thin re-export shim: policies moved to :mod:`athena.config.policies`.

Kept so ``from athena.policies import ...`` keeps working for
existing importers (voice, UI, tests). New code should import
from :mod:`athena.config.policies` directly.
"""

from __future__ import annotations

from athena.config.policies import GREETING_TEXT, ListeningControl, WakeWordPolicy

__all__ = ["GREETING_TEXT", "ListeningControl", "WakeWordPolicy"]
