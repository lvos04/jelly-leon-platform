"""Foutformaat en foutcatalogus (contract 1.3 §5): HTTP-status + ``{code, message (NL), details, trace_id}``.

- 422 ``validation_error`` met ``details.fouten[]`` (``{veld, fout, type}``, zoals de Hub); ook te lange invoer, een
  ongeldige cursor en een onbekende itemsoort (één code per situatie).
- 401 alleen voor authenticatie. Authenticatie gaat vóór de body (§4.7, RV9): FastAPI ontleedt een JSON-body vóór de
  afhankelijkheden; is er voor dit verzoek nog geen aanmelding vastgesteld, dan authenticeert ``_auth_first`` eerst
  (inclusief het verbruiken van de ``jti`` bij een mutatie). Zonder geldige aanmelding dus altijd 401, ook bij een
  onleesbare body.
"""

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


def invalid_input(veld: str, fout: str, soort: str) -> ApiError:
    """422 ``validation_error`` voor één veld (zelfde vorm als de schemafouten van FastAPI)."""
    return ApiError(422, "validation_error", "De invoer is ongeldig; zie details.fouten.",
                    {"fouten": [{"veld": veld, "fout": fout, "type": soort}]})


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


# Routes waarop elke aanroep een geldige Hub-JWT vraagt; /, /koppelen, /healthz en /readyz niet.
AUTH_PREFIXES = ("/lifecycle/v1", "/connector/v1")


def _auth_first(request: Request) -> JSONResponse | None:
    """§4.7: zonder geldige authenticatie altijd 401, ook als FastAPI de body al vóór de afhankelijkheden afwees.
    Alleen als voor dit verzoek nog geen aanmelding is vastgesteld (``request.state.principal``)."""
    if getattr(request.state, "principal", None) is not None or not request.url.path.startswith(AUTH_PREFIXES):
        return None
    authenticator = getattr(request.app.state, "authenticator", None)
    if authenticator is None:
        return None
    try:
        request.state.principal = authenticator.authenticate(
            request.headers.get("authorization"), method=request.method, path=request.url.path,
            idempotency_key=request.headers.get("idempotency-key"))
    except ApiError as err:
        return error_response(request, err)
    return None


def validation_details(errors: list[dict[str, Any]] | Any) -> dict[str, Any]:
    fouten = [{"veld": ".".join(str(p) for p in e.get("loc", ()) if p != "body"), "fout": str(e.get("msg", "")),
               "type": str(e.get("type", ""))} for e in errors]
    return {"fouten": fouten}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request, exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        refused = _auth_first(request)
        if refused is not None:
            return refused
        return error_response(
            request, ApiError(422, "validation_error", "De invoer is ongeldig; zie details.fouten.",
                              validation_details(exc.errors()))
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        from lifecycle.limits import MAX_BODY_BYTES

        if exc.status_code == 400:  # o.a. een body die FastAPI niet kon ontleden: ook dan eerst authenticeren
            refused = _auth_first(request)
            if refused is not None:
                return refused
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
