"""Connectorcontract (contract §6). Alleen wat echt gebouwd is staat in ``capabilities``; de rest geeft
``501 capability_unavailable`` — nooit gesimuleerd succes.

Nieuw in 1.2 (alleen eigen diensten): ``GET /connector/v1/hub-link`` (witte lijst van §3.5 + ``pinned_kids`` +
``rotation_sequence``; dienst-JWT of gebruikers-JWT viewer+) en ``POST /connector/v1/hub-link/unlink`` (dienst-JWT of
gebruikers-JWT admin, ``Idempotency-Key``): ``state: ontkoppeld``, token gewist, pins naar ``previous_pins``; daarna
weigert de dienst elke Hub-JWT (401 ``not_linked``). Een herhaalde ``unlink`` krijgt dus 401 ``not_linked``; dat telt
voor de Hub als geslaagd (§3.8)."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from lifecycle import APP_ID, SCHEMA_VERSION, __version__
from lifecycle.api_lifecycle import list_runs
from lifecycle.audit import audit, audit_view
from lifecycle.auth import Principal, require_read, require_unlink, require_write
from lifecycle.errors import ApiError, capability_unavailable, invalid_input, trace_id_of
from lifecycle.health import DEFAULT_MAX_AGE_S, collect_health, worker_max_age_s
from lifecycle.hublink import clear_link
from lifecycle.models import AuditEntry, OutboxEvent
from lifecycle.outbox import envelope
from lifecycle.pagination import clamp_limit, decode_cursor, encode_cursor
from lifecycle.timeutil import iso_z, utcnow

router = APIRouter(prefix="/connector/v1", tags=["connector"])

CAPABILITIES: list[str] = ["read"]
ITEM_TYPES = ["lifecycle_run"]


class Discover(BaseModel):
    app_id: str
    source_version: str
    schema_version: str
    capabilities: list[str]
    permissions: list[str]
    limits: dict[str, Any]
    item_types: list[str]
    timestamp_utc: str


class HealthCheck(BaseModel):
    name: str
    status: Literal["healthy", "degraded", "down", "unknown"]
    observed_at: str
    detail: str


class Health(BaseModel):
    status: Literal["healthy", "degraded", "down", "unknown"]
    observed_at: str
    reason_code: str | None
    checks: list[HealthCheck]


class LinkError(BaseModel):
    code: str
    message_nl: str
    at: str


class PinnedKids(BaseModel):
    jwt: list[str]
    policy: list[str]


class HubLinkOut(BaseModel):
    """Witte lijst van contract 1.3 §3.5 + ``pinned_kids`` en ``rotation_sequence`` (§6). ``state`` is ``null`` als het
    koppelbestand onleesbaar is (dan ``last_error.code = koppelstatus_onleesbaar``)."""

    state: Literal["nooit_gekoppeld", "gekoppeld", "ontkoppeld", "ingetrokken"] | None
    koppelen_bezig: bool
    hub_url: str | None
    service_id: str | None
    via: str | None
    linked_at: str | None
    first_linked_at: str | None
    keyset_fingerprint: str | None
    service_token_last4: str | None
    last_error: LinkError | None
    pinned_kids: PinnedKids
    rotation_sequence: int


class EventPage(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None


@router.get("/discover", response_model=Discover)
def discover(request: Request, _: Annotated[Principal, Depends(require_read)]) -> dict[str, Any]:
    return {
        "app_id": APP_ID,
        "source_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "capabilities": CAPABILITIES,
        "permissions": ["lifecycle_run:read", "events:read", "evidence:read"],
        "limits": {"max_page_size": 200, "rate_limit_per_minute": None, "execution": "none_in_p0",
                   # §6.2: versheid per capability/itemtype; lifecycle_run is opgeslagen data (geen meting)
                   "max_age_s": {"health": DEFAULT_MAX_AGE_S, "health.worker": worker_max_age_s(request.app),
                                 "read": DEFAULT_MAX_AGE_S}},
        "item_types": ITEM_TYPES,
        "timestamp_utc": iso_z(utcnow()),
    }


@router.get("/health", response_model=Health)
def health(request: Request, _: Annotated[Principal, Depends(require_read)]) -> dict[str, Any]:
    return collect_health(request.app)


@router.get("/items")
def items(
    request: Request,
    principal: Annotated[Principal, Depends(require_read)],
    type: Annotated[str, Query(description="Itemsoort; alleen 'lifecycle_run'.")] = "lifecycle_run",
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    if type not in ITEM_TYPES:
        raise invalid_input("type", f"Onbekende itemsoort; mogelijk: {', '.join(ITEM_TYPES)}.", "unknown_item_type")
    page: dict[str, Any] = list_runs(request, principal, cursor=cursor, limit=limit)
    return {"type": type, **page}


@router.post("/propose", status_code=501, responses={501: {"description": "capability_unavailable"}})
def propose(_: Annotated[Principal, Depends(require_write)]) -> None:
    raise capability_unavailable("connector propose (installatie-/update-voorstel)", "P2")


@router.post("/apply", status_code=501, responses={501: {"description": "capability_unavailable"}})
def apply(_: Annotated[Principal, Depends(require_write)]) -> None:
    raise capability_unavailable("connector apply (uitvoering met ondertekend ExecutionRequest)", "P2")


@router.post("/rollback", status_code=501, responses={501: {"description": "capability_unavailable"}})
def rollback(_: Annotated[Principal, Depends(require_write)]) -> None:
    raise capability_unavailable("connector rollback", "P2")


@router.get("/evidence/{evidence_id}")
def evidence(evidence_id: str, request: Request, _: Annotated[Principal, Depends(require_read)]) -> dict[str, Any]:
    try:
        eid = uuid.UUID(evidence_id)
    except ValueError as exc:
        raise ApiError(404, "not_found", "Bewijs niet gevonden.") from exc
    with request.app.state.session_factory() as session:
        entry = session.get(AuditEntry, eid)
        if entry is None:
            raise ApiError(404, "not_found", "Bewijs niet gevonden.")
        return audit_view(entry)


@router.get("/events", response_model=EventPage)
def events(
    request: Request,
    _: Annotated[Principal, Depends(require_read)],
    cursor: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    size = clamp_limit(limit)
    position = decode_cursor(cursor)
    after = 0
    if position is not None:
        try:
            after = int(position["after"])
        except (KeyError, ValueError, TypeError) as exc:
            raise invalid_input("cursor", "Ongeldige cursor.", "invalid_cursor") from exc
    with request.app.state.session_factory() as session:
        rows = list(
            session.execute(select(OutboxEvent).where(OutboxEvent.id > after).order_by(OutboxEvent.id).limit(size))
            .scalars()
            .all()
        )
        items = [envelope(r) for r in rows]
    last = rows[-1].id if rows else after
    return {"items": items, "next_cursor": encode_cursor({"after": last})}


@router.get("/hub-link", response_model=HubLinkOut, summary="Koppelstatus met de Hub (witte lijst, zonder geheimen)")
def get_hub_link(request: Request, _: Annotated[Principal, Depends(require_read)]) -> dict[str, Any]:
    view: dict[str, Any] = request.app.state.hub_link.read().connector_view()
    return view


@router.post(
    "/hub-link/unlink",
    response_model=HubLinkOut,
    summary="Ontkoppelen (dienst-JWT of admin, Idempotency-Key)",
    responses={401: {"description": "niet (meer) gekoppeld: voor de Hub telt een herhaalde unlink als geslaagd"}},
)
def unlink_hub(
    request: Request,
    principal: Annotated[Principal, Depends(require_unlink)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    trace = trace_id_of(request)
    store = request.app.state.hub_link

    def handler(session: Session) -> tuple[int, dict[str, Any], dict[str, str]]:
        link = store.update(lambda doc: clear_link(doc, "ontkoppeld") if doc.get("state") == "gekoppeld" else None)
        audit(
            session,
            actor=principal.actor,
            action="hub_link.unlink",
            target_type="hub_link",
            target_id=link.service_id or "onbekend",
            outcome="ok",
            trace_id=uuid.UUID(trace),
        )
        view: dict[str, Any] = link.connector_view()
        return 200, view, {}

    result: JSONResponse = request.app.state.idempotency.execute(
        principal=principal.key,
        raw_key=idempotency_key,
        method="POST",
        path="/connector/v1/hub-link/unlink",
        body={},
        handler=handler,
        trace_id=trace,
    )
    return result
