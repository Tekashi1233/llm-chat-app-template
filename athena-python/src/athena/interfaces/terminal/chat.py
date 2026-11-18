"""ATHENA Python v1 — terminal chat (CLI interface for testing).

Reads a line, runs it through the agent loop, prints the reply —
Friday's ``terminal_chat.py`` shape. No Qt, no audio: the loop is
fully testable with a fake agent via :func:`run_chat`.

Exit commands (case-insensitive, any surrounding whitespace):
``/quit``, ``quit``, ``exit``. An empty line is ignored.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

__all__ = ["EXIT_COMMANDS", "build_agent", "main", "run_chat"]

logger = logging.getLogger(__name__)

#: Inputs that end the chat loop.
EXIT_COMMANDS = frozenset({"/quit", "quit", "exit"})


def run_chat(
    agent: Any,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = print,
    prompt: str = "you> ",
    greeting: str = "ATHENA (terminal). Type /quit to exit.",
) -> None:
    """Run the read → agent → print loop until an exit command.

    ``agent`` is any object with ``run_turn(text) -> str`` (a real
    :class:`athena.agents.agent.AgentLoop` or a fake in tests).
    ``input_fn``/``output_fn`` are injected so tests can drive the
    loop without a real terminal. Never raises for an agent error —
    the agent itself returns honest text — but an ``EOFError`` /
    ``KeyboardInterrupt`` from the input ends the loop cleanly.
    """
    if greeting:
        output_fn(greeting)
    while True:
        try:
            line = input_fn(prompt)
        except (EOFError, KeyboardInterrupt):
            output_fn("Goodbye.")
            return
        if line is None:
            output_fn("Goodbye.")
            return
        text = line.strip()
        if not text:
            continue
        if text.lower() in EXIT_COMMANDS:
            output_fn("Goodbye.")
            return
        try:
            reply = agent.run_turn(text)
        except Exception as exc:  # noqa: BLE001 — a broken agent ends the loop honestly
            output_fn(f"Sorry, the agent failed ({type(exc).__name__}: {exc}).")
            return
        output_fn(str(reply) if reply is not None else "")


def build_agent() -> Any:
    """Build a real agent: SlottedProvider (Worker -> Ollama).

    Lazy imports so importing this module never needs a key, a
    network connection, or Ollama running. Raises
    :class:`RuntimeError` with a plain-language reason when no
    provider can be built at all (missing httpx is the only hard
    failure — both slots degrade gracefully otherwise).
    """
    from athena.agents.agent import AgentLoop
    from athena.awareness.live_context import today_info
    from athena.config.secure_store import SecureStore
    from athena.llm.ollama_client import OllamaClient
    from athena.llm.slotted_provider import OllamaSlot, SlottedProvider, WorkerSlot
    from athena.llm.worker_client import WorkerClient

    store = SecureStore()
    client: Any = None
    try:
        api_key = store.require_key()
        client = WorkerClient(api_key=api_key)
    except Exception:
        try:
            token = store.require_token()
            client = WorkerClient(auth_token=token)
        except Exception:
            client = None
    slot1 = None
    if client is not None:
        try:
            slot1 = WorkerSlot(client, store.get_or_create_conversation_id())
        except Exception:
            slot1 = None
    provider = SlottedProvider(slot1, OllamaSlot(OllamaClient()))
    return AgentLoop(provider, live_context=lambda: today_info())


def main() -> None:
    """Entry point: build the real agent and run the terminal chat."""
    try:
        agent = build_agent()
    except Exception as exc:  # noqa: BLE001 — report honestly, don't traceback on users
        print(f"Could not start ATHENA: {exc}")
        raise SystemExit(1) from exc
    run_chat(agent)


if __name__ == "__main__":
    main()
