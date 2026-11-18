"""ATHENA Python v1 — Cloudflare Worker API client.

Port of the C# ``Athena.Core.ChatClient`` + ``DataClient`` (N1/N2/N6):

* POST ``/api/chat`` with the site key (``X-Vesper-Key``) or, when
  one is stored, a Bearer owner token — token is always preferred
  and the key is never sent alongside it. The server loads the
  conversation history itself, so only the new user message is sent.
* The reply is a Server-Sent Events stream: one JSON object per
  ``data:`` line carrying ``choices[0].delta.content`` (or
  ``choices[0].message.content``, or top-level ``response``),
  ending with ``data: [DONE]``. See :func:`parse_sse`.
* GET ``/api/model-info`` (public), ``/api/memories``,
  ``/api/devices/status``, ``/api/devices/activity``.
* The 8000-character message cap is enforced before sending
  (``MAX_MESSAGE_LENGTH``, mirrors C# ``MessageValidation``).
* Errors are plain language, never a bare HTTP status. HTTP 500
  deliberately claims NO cause: it can be the daily AI quota
  being used up or a real fault, and the client cannot tell which.

No Qt / audio imports here. ``httpx`` is the only dependency; a
custom ``httpx.Client`` (e.g. with a ``MockTransport``) can be
injected for tests.
"""

from __future__ import annotations

import json
from enum import Enum

import httpx

__all__ = [
    "AthenaApiError",
    "KeyValidationResult",
    "MAX_MESSAGE_LENGTH",
    "WORKER_BASE",
    "WorkerClient",
    "parse_sse",
]

WORKER_BASE = "https://llm-chat-app-template.thanosabdulla.workers.dev"

#: Longest user message the app will send (characters). Mirrors the
#: server rule; the server remains authoritative.
MAX_MESSAGE_LENGTH = 8000

_CHAT_PATH = "/api/chat"
_MODEL_INFO_PATH = "/api/model-info"
_MEMORIES_PATH = "/api/memories"
_DEVICE_STATUS_PATH = "/api/devices/status"
_DEVICE_ACTIVITY_PATH = "/api/devices/activity"

_EMPTY_REPLY_FALLBACK = "Sorry, I didn't manage to answer that. Could you try again?"
_SERVICE_PROBLEM_CHAT = (
    "The service had a problem and your message was not answered — try again."
)
_SERVICE_PROBLEM_REQUEST = (
    "The service had a problem and the request was not answered — try again."
)


class AthenaApiError(Exception):
    """An error from the Worker, with its HTTP status (0 = no response)."""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class KeyValidationResult(Enum):
    """Result of checking auth without spending a model call."""

    VALID = "valid"
    INVALID = "invalid"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


def parse_sse(sse_body: str) -> str:
    """Parse the Worker's SSE stream into plain reply text.

    Pure function, no network, no state. A malformed chunk is
    skipped — it must not lose the rest of the reply.
    """
    parts: list[str] = []
    for raw_line in sse_body.split("\n"):
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            root = json.loads(payload)
        except ValueError:
            continue
        if not isinstance(root, dict):
            continue
        text: str | None = None
        choices = root.get("choices")
        if isinstance(choices, list) and choices:
            first = choices[0]
            if isinstance(first, dict):
                delta = first.get("delta")
                if isinstance(delta, dict) and isinstance(
                    delta.get("content"), str
                ):
                    text = delta["content"]
                else:
                    message = first.get("message")
                    if isinstance(message, dict) and isinstance(
                        message.get("content"), str
                    ):
                        text = message["content"]
        if text is None:
            response = root.get("response")
            if isinstance(response, str):
                text = response
        if text:
            parts.append(text)
    return "".join(parts)


def _auth_headers(
    token: str | None, api_key: str | None
) -> dict[str, str]:
    """The single auth header: Bearer token when stored, else site key."""
    if token and token.strip():
        return {"Authorization": "Bearer " + token.strip()}
    if api_key and api_key.strip():
        return {"X-Vesper-Key": api_key.strip()}
    # 401, not 0: a missing key is an auth/configuration problem, and
    # the provider only falls back to Ollama on status 0 (transport
    # failure) and 429/5xx. Raising 0 here made a missing/unreadable
    # key silently fall back to Ollama, so the user saw the misleading
    # "Ollama isn't running" message instead of the real problem.
    raise AthenaApiError(401, "No token and no site key to authenticate with.")


def _map_chat_error(status: int) -> AthenaApiError:
    if status == 401:
        return AthenaApiError(
            status,
            "Not authorised — sign in again, or the site key is missing or wrong.",
        )
    if status == 429:
        return AthenaApiError(
            status, "Too many requests — wait a minute and try again."
        )
    if status == 500:
        return AthenaApiError(status, _SERVICE_PROBLEM_CHAT)
    return AthenaApiError(status, f"The service returned HTTP {status}.")


def _map_read_error(status: int) -> AthenaApiError:
    if status == 401:
        return AthenaApiError(
            status,
            "Not authorised — sign in again, or the site key is missing or wrong.",
        )
    if status == 429:
        return AthenaApiError(
            status, "Too many requests — wait a minute and try again."
        )
    if status == 500:
        return AthenaApiError(status, _SERVICE_PROBLEM_REQUEST)
    return AthenaApiError(status, f"The service returned HTTP {status}.")


class WorkerClient:
    """Talks to the ATHENA backend (the Cloudflare Worker) over HTTPS.

    Args:
        api_key: Site key (``X-Vesper-Key``). May be None when a
            token is used instead.
        auth_token: Owner session token (Bearer). Preferred over the
            key when present; the key is never sent alongside it.
        client: Optional ``httpx.Client`` (inject a ``MockTransport``
            client in tests). Defaults to a 120 s timeout client for
            chat (streaming a reply can take a while) and 30 s for
            reads — the values the C# app uses.
        base_url: Worker origin. Defaults to ``WORKER_BASE``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        auth_token: str | None = None,
        client: httpx.Client | None = None,
        base_url: str = WORKER_BASE,
    ) -> None:
        self.api_key = api_key
        self.auth_token = auth_token
        self.base_url = base_url.rstrip("/")
        self._http = client or httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(120.0, connect=30.0),
            # Never inherit proxy env vars: a bracketed no_proxy entry
            # (e.g. "[::1]") makes httpx raise InvalidURL at
            # construction, and ATHENA must not route its own traffic
            # through an ambient proxy anyway.
            trust_env=False,
        )
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._http.close()

    def __enter__(self) -> "WorkerClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- chat -----------------------------------------------------------

    def send_message(
        self,
        text: str,
        conversation_id: str,
        *,
        via_voice: bool = False,
    ) -> str:
        """Send one user message; return the assistant's full reply.

        ``via_voice`` must be True ONLY when the text came from the
        microphone — the Worker enforces the voice-only revoke guard
        on it, so a wrong tag is a security hole, not a cosmetic bug.
        The tag is omitted entirely (not false) for typed messages.

        Raises:
            ValueError: empty message or over the 8000-char cap.
            AthenaApiError: HTTP failure, or the service unreachable.
        """
        if not text or not text.strip():
            raise ValueError("Message is empty.")
        if len(text) > MAX_MESSAGE_LENGTH:
            raise ValueError(
                f"Message is too long — keep it under {MAX_MESSAGE_LENGTH} characters."
            )
        message: dict[str, object] = {"role": "user", "content": text.strip()}
        if via_voice:
            message["via_voice"] = True
        payload = {"messages": [message], "conversation_id": conversation_id}
        try:
            res = self._http.post(
                _CHAT_PATH, headers=self._headers(), json=payload
            )
        except httpx.TransportError as exc:
            raise AthenaApiError(
                0, "Couldn't reach the service — check your connection and try again."
            ) from exc
        if res.status_code != 200:
            raise _map_chat_error(res.status_code)
        reply = parse_sse(res.text)
        return reply.strip() or _EMPTY_REPLY_FALLBACK

    def validate_auth(
        self,
        token: str | None = None,
        api_key: str | None = None,
    ) -> KeyValidationResult:
        """Validate auth WITHOUT a model call and WITHOUT creating rows.

        The server authenticates first, then rejects the empty
        request with HTTP 400 — so 400 means the key/token is good
        and 401 means it is wrong.
        """
        token = token if token is not None else self.auth_token
        api_key = api_key if api_key is not None else self.api_key
        try:
            res = self._http.post(
                _CHAT_PATH,
                headers=_auth_headers(token, api_key),
                json={"messages": [], "conversation_id": "key-check"},
            )
        except httpx.TransportError:
            return KeyValidationResult.ERROR
        if res.status_code == 400:
            return KeyValidationResult.VALID
        if res.status_code == 401:
            return KeyValidationResult.INVALID
        if res.status_code == 429:
            return KeyValidationResult.RATE_LIMITED
        return KeyValidationResult.ERROR

    # -- reads ----------------------------------------------------------

    def get_model_info(self) -> dict:
        """GET /api/model-info. Public — no key needed."""
        return self._get_json(_MODEL_INFO_PATH, auth=False)

    def get_memories(self, conversation_id: str) -> dict | None:
        """GET /api/memories?conversation_id=…

        Returns None when the endpoint is not deployed yet (HTTP 404)
        so the UI can show an honest "not enabled yet" state.
        """
        return self._get_json(
            f"{_MEMORIES_PATH}?conversation_id={conversation_id}", allow_404=True
        )

    def get_device_status(self) -> dict | None:
        """GET /api/devices/status. None when not deployed yet (404)."""
        return self._get_json(_DEVICE_STATUS_PATH, allow_404=True)

    def get_device_activity(self, limit: int = 50) -> dict | None:
        """GET /api/devices/activity?limit=…. None when not deployed yet (404)."""
        return self._get_json(
            f"{_DEVICE_ACTIVITY_PATH}?limit={limit}", allow_404=True
        )

    # -- internals ------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return _auth_headers(self.auth_token, self.api_key)

    def _get_json(
        self, path: str, *, auth: bool = True, allow_404: bool = False
    ) -> dict | None:
        try:
            res = self._http.get(
                path,
                headers=self._headers() if auth else None,
                timeout=30.0,
            )
        except httpx.TransportError as exc:
            raise AthenaApiError(
                0, "Couldn't reach the service — check your connection and try again."
            ) from exc
        if allow_404 and res.status_code == 404:
            return None
        if res.status_code != 200:
            raise _map_read_error(res.status_code)
        try:
            data = res.json()
        except ValueError as exc:
            raise AthenaApiError(0, "The service sent a reply I couldn't read.") from exc
        if not isinstance(data, dict):
            raise AthenaApiError(0, "The service sent a reply I couldn't read.")
        return data
