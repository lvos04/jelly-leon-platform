"""Connectorcontract (contract §6). Alleen wat echt gebouwd is staat in ``capabilities``; de rest geeft
``501 capability_unavailable`` — nooit gesimuleerd succes."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import select

from lifecycle import APP_ID, SCHEMA_VERSION, __version__
from lifecycle.api_lifecycle import list_runs
from lifecycle.audit import audit_view
from lifecycle.auth import Principal, require_read, require_write
from lifecycle.errors import ApiError, capability_unavailable
from lifecycle.health import collect_health
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


class EventPage(BaseModel):
    items: list[dict[str, Any]]
    next_cursor: str | None


@router.get("/discover", response_model=Discover)
def discover(_: Annotated[Principal, Depends(require_read)]) -> dict[str, Any]:
    return {
        "app_id": APP_ID,
        "source_version": __version__,
        "schema_version": SCHEMA_VERSION,
        "capabilities": CAPABILITIES,
        "permissions": ["lifecycle_run:read", "events:read", "evidence:read"],
        "limits": {"max_page_size": 200, "rate_limit_per_minute": None, "execution": "none_in_p0"},
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
        raise ApiError(400, "unknown_item_type", "Onbekende itemsoort.", {"supported": ITEM_TYPES})
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
            raise ApiError(400, "invalid_cursor", "Ongeldige cursor.") from exc
    with request.app.state.session_factory() as session:
        rows = list(
            session.execute(select(OutboxEvent).where(OutboxEvent.id > after).order_by(OutboxEvent.id).limit(size))
            .scalars()
            .all()
        )
        items = [envelope(r) for r in rows]
    last = rows[-1].id if rows else after
    return {"items": items, "next_cursor": encode_cursor({"after": last})}
