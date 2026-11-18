"""ATHENA Python v1 — local Ollama client (Slot 2 fallback).

Plain ``httpx`` calls to a local Ollama server — no ``ollama``
package needed, so there is nothing extra to install for the
fallback to work. The default model is ``llama3.1:8b`` (the model
Friday's Ollama slot uses).

Ollama is the always-available last resort: when it is not
running, :meth:`OllamaClient.generate` raises
:class:`OllamaError` with a plain-language message so the slotted
provider can report an honest failure instead of crashing.
"""

from __future__ import annotations

import httpx

__all__ = ["OllamaClient", "OllamaError"]

DEFAULT_MODEL = "llama3.1:8b"
DEFAULT_BASE_URL = "http://localhost:11434"


class OllamaError(Exception):
    """Ollama could not answer (not running, timed out, bad reply)."""


class OllamaClient:
    """Minimal client for Ollama's ``/api/chat`` endpoint.

    Args:
        model: Ollama model tag. Defaults to ``llama3.1:8b``.
        base_url: Ollama origin. Defaults to ``http://localhost:11434``.
        client: Optional ``httpx.Client`` (inject a ``MockTransport``
            client in tests).
    """

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        # trust_env=False: never inherit proxy env vars — a bracketed
        # no_proxy entry (e.g. "[::1]") makes httpx raise InvalidURL
        # at construction, and localhost traffic must never be proxied.
        self._http = client or httpx.Client(base_url=self.base_url, trust_env=False)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def is_running(self, timeout: float = 2.0) -> bool:
        """Cheap local check: True when Ollama answers at all.

        Never raises — a "no" here just means the fallback is down.
        """
        try:
            res = self._http.get("/api/tags", timeout=timeout)
            return res.status_code == 200
        except httpx.TransportError:
            return False
        except Exception:
            return False

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        timeout: float = 60.0,
    ) -> str:
        """One chat completion from the local model.

        Raises :class:`OllamaError` on any failure — the caller
        (the slotted provider) decides what the user sees.
        """
        options: dict[str, object] = {"temperature": temperature}
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": options,
        }
        try:
            res = self._http.post("/api/chat", json=payload, timeout=timeout)
        except httpx.ConnectError as exc:
            raise OllamaError(
                "Ollama isn't running on this laptop — start Ollama "
                "and try again, or check your connection to ATHENA."
            ) from exc
        except httpx.TimeoutException as exc:
            raise OllamaError(
                "The local model took too long to answer — try again "
                "or use a simpler phrasing."
            ) from exc
        except httpx.TransportError as exc:
            raise OllamaError(
                "Couldn't reach Ollama on this laptop — is it running?"
            ) from exc
        if res.status_code != 200:
            raise OllamaError(
                f"Ollama answered with HTTP {res.status_code} — "
                "the local model may not be downloaded yet "
                f"(try: ollama pull {self.model})."
            )
        try:
            data = res.json()
        except ValueError as exc:
            raise OllamaError("Ollama sent a reply I couldn't read.") from exc
        message = data.get("message") if isinstance(data, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise OllamaError("Ollama answered, but the reply was empty.")
        return content
