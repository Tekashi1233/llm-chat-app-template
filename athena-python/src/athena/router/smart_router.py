"""ATHENA Python v1 — tiered smart router (Friday-style).

Four tiers, cheapest first:

* SIMPLE — greeting / small talk / single fact: answer directly, at
  most one local tool.
* MEDIUM — one intent needing a tool: 1–3 tool calls via the agent loop.
* COMPLEX — multi-step ("and then", "first … then", plans, schedules):
  hand to the execution engine.
* CLARIFY — too vague to act on: ask exactly one question. After
  three clarifications with no progress, auto-escalate to COMPLEX
  instead of interrogating the user forever.

Classification is rules first (regex/keywords over the text plus the
category router), with an optional LLM call through the provider as
a second opinion — the LLM is never on the critical path: any
failure, timeout-shaped error, or unparsable answer falls back to
the rules. ``classify`` never raises and never blocks on the LLM.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from athena.router import category_router

__all__ = ["MAX_CLARIFICATIONS", "RouteDecision", "SmartRouter", "Tier"]

logger = logging.getLogger(__name__)

#: Ask at most this many clarifying questions, then escalate.
MAX_CLARIFICATIONS = 3

_GREETING_RE = re.compile(
    r"^\s*(hi|hello|hey|good\s+(morning|afternoon|evening)|salam|assalamu\s+alaikum)\b",
    re.IGNORECASE,
)
_SMALLTALK_RE = re.compile(
    r"^\s*(how are you|who are you|what can you do|thank(s| you)|bye|goodbye)\b",
    re.IGNORECASE,
)
_MULTI_STEP_RE = re.compile(
    r"\b(and then|then\b.*\bthen\b|first\b.*\bthen\b|step by step|plan\b.*\bfor\b|"
    r"schedule\b|every (day|week|morning)|remind me.*and\b|do .* and .*)\b",
    re.IGNORECASE,
)
_VAGUE_RE = re.compile(
    r"^\s*(help|do something|do it|do that|something|anything|hmm+|ok|okay|yes|no)\s*[.?!]?\s*$",
    re.IGNORECASE,
)
_TIER_WORD_RE = re.compile(r"\b(SIMPLE|MEDIUM|COMPLEX|CLARIFY)\b", re.IGNORECASE)


class Tier(Enum):
    """Routing tiers, cheapest first."""

    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"
    CLARIFY = "clarify"


@dataclass
class RouteDecision:
    """One routing outcome."""

    tier: Tier
    category: str = "general"
    reason: str = ""
    #: For CLARIFY: the single question to ask the user.
    question: str | None = None
    #: How many clarifications have already been asked this conversation.
    clarifications_asked: int = 0


class SmartRouter:
    """Tiered router: rules first, optional LLM second opinion.

    Args:
        provider: optional LLM provider (any object with
            ``generate(messages) -> str``). Used only as a second
            opinion when the rules are unsure; never required.
        category_route: keyword category router (defaults to
            :mod:`athena.router.category_router`).
    """

    def __init__(self, provider: Any | None = None, *, category_route: Any = None) -> None:
        self._provider = provider
        self._route = category_route or category_router.route

    def classify(
        self, text: str, *, clarifications_asked: int = 0, use_llm: bool = True
    ) -> RouteDecision:
        """Classify ``text`` into a tier. Never raises, never blocks
        on the LLM (any LLM failure falls back to the rules)."""
        try:
            return self._classify(text, clarifications_asked=clarifications_asked, use_llm=use_llm)
        except Exception:  # noqa: BLE001 — classification must not break a turn
            logger.warning("[SmartRouter] classify failed; defaulting to MEDIUM", exc_info=True)
            return RouteDecision(Tier.MEDIUM, "general", "classifier error — default")

    def _classify(
        self, text: str, *, clarifications_asked: int, use_llm: bool
    ) -> RouteDecision:
        raw = (text or "").strip()
        if not raw:
            return self._clarify_or_escalate(
                "I didn't catch that — what would you like me to do?", clarifications_asked
            )
        if _GREETING_RE.match(raw) or _SMALLTALK_RE.match(raw):
            return RouteDecision(Tier.SIMPLE, "general", "greeting/small talk")
        if _VAGUE_RE.match(raw) or len(raw.split()) <= 1:
            return self._clarify_or_escalate(
                "Could you say a little more about what you'd like me to do?",
                clarifications_asked,
            )
        if _MULTI_STEP_RE.search(raw):
            return RouteDecision(Tier.COMPLEX, self._route(raw), "multi-step request")
        category = self._route(raw)
        if category != "general":
            return RouteDecision(Tier.MEDIUM, category, f"category {category!r}")
        if use_llm and self._provider is not None:
            llm_tier = self._llm_tier(raw)
            if llm_tier is not None:
                if llm_tier is Tier.CLARIFY:
                    return self._clarify_or_escalate(
                        "Could you say a little more about what you'd like me to do?",
                        clarifications_asked,
                    )
                return RouteDecision(llm_tier, category, "LLM second opinion")
        return RouteDecision(Tier.SIMPLE, "general", "plain question — answer directly")

    def _clarify_or_escalate(self, question: str, clarifications_asked: int) -> RouteDecision:
        if clarifications_asked >= MAX_CLARIFICATIONS:
            return RouteDecision(
                Tier.COMPLEX,
                "general",
                f"clarified {clarifications_asked}x with no progress — escalated",
                clarifications_asked=clarifications_asked,
            )
        return RouteDecision(
            Tier.CLARIFY, "general", "too vague to act on",
            question=question, clarifications_asked=clarifications_asked,
        )

    def _llm_tier(self, text: str) -> Tier | None:
        """Ask the provider for a tier word. None on any failure."""
        assert self._provider is not None
        try:
            reply = self._provider.generate(
                [
                    {
                        "role": "system",
                        "content": (
                            "Classify the user request with exactly one word: "
                            "SIMPLE (greeting or single fact), MEDIUM (one tool "
                            "task), COMPLEX (multi-step plan), or CLARIFY (too "
                            "vague to act on). Reply with only that word."
                        ),
                    },
                    {"role": "user", "content": text},
                ]
            )
        except Exception:  # noqa: BLE001 — LLM is never on the critical path
            logger.warning("[SmartRouter] LLM classify failed; using rules", exc_info=True)
            return None
        if isinstance(reply, tuple):  # slotted providers return (text, slot, note)
            reply = reply[0] if reply else ""
        if not isinstance(reply, str):
            return None
        match = _TIER_WORD_RE.search(reply)
        if match is None:
            return None
        try:
            return Tier(match.group(1).lower())
        except ValueError:
            return None
