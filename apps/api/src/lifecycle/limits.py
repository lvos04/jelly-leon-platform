"""Maximale verzoekgrootte: 1 MiB (1 048 576 bytes) → ``413 payload_too_large``.

Contract 1.1 noemt geen grens; zonder grens leest de app elke body (ook vóór de authenticatie, want FastAPI parseert
de body vóór de afhankelijkheden) volledig in het geheugen — met de geheugenlimiet uit het Custom App-formulier een
eenvoudige manier om de app onderuit te halen. We volgen daarom alvast contract 1.2 §5: een ASGI-laag telt de body
tijdens het lezen (ook zonder ``Content-Length``/chunked) en breekt af; een te grote ``Content-Length`` wordt direct
geweigerd zonder de body te lezen. Antwoord in het contractformaat ``{code, message, details, trace_id}``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY_BYTES = 1024 * 1024
MESSAGE_NL = "Het verzoek is te groot (maximaal 1 MiB)."


class BodyTooLarge(HTTPException):
    """Wordt tijdens het lezen van de body gegooid; de foutafhandeling maakt er een 413 in contractformaat van."""

    def __init__(self) -> None:
        super().__init__(413, MESSAGE_NL)


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = _content_length(scope)
        if declared is not None and declared > self.max_bytes:
            await _reject(scope, send, self.max_bytes)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLarge()
            return message

        await self.app(scope, limited_receive, send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _reject(scope: Scope, send: Send, max_bytes: int) -> None:
    state: dict[str, Any] = scope.get("state") or {}
    trace = str(state.get("trace_id") or uuid.uuid4())
    body = json.dumps(
        {"code": "payload_too_large", "message": MESSAGE_NL, "details": {"max_bytes": max_bytes}, "trace_id": trace},
        ensure_ascii=False,
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
                (b"connection", b"close"),
                (b"x-trace-id", trace.encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
