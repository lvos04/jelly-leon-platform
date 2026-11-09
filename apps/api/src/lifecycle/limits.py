"""Maximale verzoekgrootte: 1 MiB (1 048 576 bytes) → ``413 payload_too_large`` (contract 1.2 §5).

Een ASGI-laag **vóór routering en authenticatie**:

- een te grote ``Content-Length`` wordt direct geweigerd zonder de body te lezen; een ongeldige ``Content-Length``
  geeft ``400 bad_request``;
- anders leest deze laag de body tellend in (ook zonder ``Content-Length``/chunked; hooguit 1 MiB in het geheugen)
  **vóórdat** de app iets doet, en breekt af met 413 zodra er meer binnenkomt. Zo krijgt ook een route die zelf geen
  body leest, een onbekende route/methode of een verzoek zonder (of mét geldige) aanmelding altijd 413 — nooit eerst
  401/404/405 of zelfs een uitgevoerde mutatie;
- daarna krijgt de app de gebufferde body; een verbroken verbinding vóór het einde van de body voert niets uit.

Antwoord in het contractformaat ``{code, message, details, trace_id}`` met ``details.max_bytes``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 1024 * 1024
MESSAGE_NL = "Het verzoek is te groot (maximaal 1 MiB)."


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            declared = _content_length(scope)
        except ValueError:
            await _send_error(scope, send, 400, "bad_request", "Ongeldige Content-Length.", {})
            return
        if declared is not None and declared > self.max_bytes:
            await _reject(scope, send, self.max_bytes)
            return
        chunks: list[bytes] = []
        total, more = 0, True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return  # client weg vóór de body compleet was: niets uitvoeren
            part = message.get("body", b"")
            total += len(part)
            if total > self.max_bytes:
                await _reject(scope, send, self.max_bytes)
                return
            chunks.append(part)
            more = bool(message.get("more_body", False))
        buffered: Message = {"type": "http.request", "body": b"".join(chunks), "more_body": False}
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if not delivered:
                delivered = True
                return buffered
            return await receive()  # daarna alleen nog http.disconnect

        await self.app(scope, replay, send)


def _content_length(scope: Scope) -> int | None:
    """``None`` zonder header; ``ValueError`` bij een ongeldige of dubbele waarde."""
    values = [value for name, value in scope.get("headers", []) if name == b"content-length"]
    if not values:
        return None
    text = values[0].decode("latin-1").strip()
    if len(values) > 1 or not text.isdigit():
        raise ValueError("ongeldige Content-Length")
    return int(text)


def _trace(scope: Scope) -> str:
    state: Any = scope.get("state") or {}
    value = state.get("trace_id") if isinstance(state, dict) else None
    return str(value or uuid.uuid4())


async def _send_error(scope: Scope, send: Send, status: int, code: str, message: str, details: dict[str, Any]) -> None:
    trace = _trace(scope)
    body = json.dumps(
        {"code": code, "message": message, "details": details, "trace_id": trace}, ensure_ascii=False
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
                (b"x-trace-id", trace.encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _reject(scope: Scope, send: Send, max_bytes: int) -> None:
    await _send_error(scope, send, 413, "payload_too_large", MESSAGE_NL, {"max_bytes": max_bytes})
