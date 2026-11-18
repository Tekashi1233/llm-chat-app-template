"""ATHENA Python v1 — interfaces package.

Every way to talk to ATHENA: the desktop app
(:mod:`athena.interfaces.desktop`) and the terminal chat
(:mod:`athena.interfaces.terminal.chat`) used for testing. Interfaces
own the user-facing side only — they hand text to an agent
(:mod:`athena.agents`) and render the reply. The brain never knows
which interface is active.
"""

from __future__ import annotations

__all__: list[str] = []
