"""Foutformaat (contract §5): HTTP-status + ``{code, message (NL), details, trace_id}``."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

log = logging.getLogger("lifecycle")

TRACE_HEADER = "X-Trace-Id"


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        self.headers = headers or {}


def capability_unavailable(feature: str, phase: str = "P2") -> ApiError:
    return ApiError(
        501,
        "capability_unavailable",
        f"Deze functie ({feature}) is nog niet gebouwd; uitvoering volgt in fase {phase}. Er is niets uitgevoerd.",
        {"feature": feature, "planned_phase": phase},
    )


def trace_id_of(request: Request) -> str:
    value = getattr(request.state, "trace_id", None)
    return str(value) if value else str(uuid.uuid4())


def error_body(code: str, message: str, details: dict[str, Any] | None, trace_id: str) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details or {}, "trace_id": trace_id}


def error_response(request: Request, err: ApiError) -> JSONResponse:
    headers = {TRACE_HEADER: trace_id_of(request), **err.headers}
    return JSONResponse(
        error_body(err.code, err.message, err.details, trace_id_of(request)), status_code=err.status, headers=headers
    )


_HTTP_CODES = {
    400: ("bad_request", "Ongeldig verzoek."),
    401: ("unauthorized", "Niet aangemeld of ongeldige aanmelding."),
    403: ("forbidden", "Geen toestemming voor deze actie."),
    404: ("not_found", "Niet gevonden."),
    405: ("method_not_allowed", "Deze methode is hier niet toegestaan."),
    406: ("not_acceptable", "Dit antwoordformaat wordt niet ondersteund."),
    409: ("conflict", "Conflict met de huidige toestand."),
    413: ("payload_too_large", "Het verzoek is te groot (maximaal 1 MiB)."),
    415: ("unsupported_media_type", "Dit inhoudstype wordt niet ondersteund."),
    429: ("rate_limited", "Te veel verzoeken; probeer het later opnieuw."),
}


class TraceIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(TRACE_HEADER, "")
        try:
            trace = uuid.UUID(incoming)
        except ValueError:
            trace = uuid.uuid4()
        request.state.trace_id = trace
        response = await call_next(request)
        response.headers.setdefault(TRACE_HEADER, str(trace))
        return response


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        problems = [
            {"loc": [str(p) for p in e.get("loc", ())], "msg": str(e.get("msg", "")), "type": str(e.get("type", ""))}
            for e in exc.errors()
        ]
        return error_response(
            request, ApiError(422, "validation_error", "Ongeldige invoer; zie details.", {"errors": problems})
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        from lifecycle.limits import MAX_BODY_BYTES

        code, message = _HTTP_CODES.get(exc.status_code, ("http_error", "Verzoek mislukt."))
        headers = dict(exc.headers or {})
        details: dict[str, Any] = {"max_bytes": MAX_BODY_BYTES} if exc.status_code == 413 else {}
        if exc.status_code == 413:
            headers.setdefault("Connection", "close")
        return error_response(request, ApiError(exc.status_code, code, message, details, headers))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("onverwachte fout (trace %s)", trace_id_of(request))
        return error_response(
            request,
            ApiError(500, "internal_error", "Interne fout; er is niets bevestigd. Zie de app-log met dit trace-id."),
        )
