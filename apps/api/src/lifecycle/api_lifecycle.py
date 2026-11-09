"""``/lifecycle/v1``: levenscyclus-runs (datamodel van de statusmachine), Hub-koppeling en P2-plaatshouders.

Fase P0 levert het datamodel en de regels. Een run aanmaken (toestand ``discovered``) en afbreken mag; alle andere
overgangen vereisen bewijs dat pas de uitvoeringsengine (P2) kan leveren en geven nu eerlijk ``capability_unavailable``.
Er wordt in P0 niets op de NAS uitgevoerd.
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from lifecycle.audit import audit
from lifecycle.auth import Principal, require_admin, require_read, require_write
from lifecycle.errors import ApiError, capability_unavailable, trace_id_of
from lifecycle.hublink import unlink
from lifecycle.models import LifecycleRun, LifecycleTransition
from lifecycle.outbox import emit
from lifecycle.pagination import clamp_limit, decode_cursor, encode_cursor
from lifecycle.redact import redact, redact_text
from lifecycle.statemachine import (
    LABELS_NL,
    MANUAL_ALLOWED_P0,
    TRANSITIONS,
    LifecycleState,
    RunKind,
    TransitionContext,
    TransitionError,
    check_transition,
    describe,
)
from lifecycle.timeutil import iso_z, utcnow

router = APIRouter(prefix="/lifecycle/v1", tags=["lifecycle"])

APP_ID_PATTERN = r"^[a-z]([-a-z0-9]*[a-z0-9])?$"
# Versie, tag of digest (bijv. 1.2.25, v1.4.35, sha256:…, ghcr.io/owner/app:1.0.0@sha256:…): geen spaties of
# opmaaktekens.
VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+:@/-]*$"
# 'latest' als versie of als tag (ook ``repo:latest`` en ``repo:latest@sha256:…``) is verboden (contract §1).
LATEST_TAG = re.compile(r"(^|[:/])latest($|@)", re.IGNORECASE)


class Actor(BaseModel):
    type: str
    id: str


class TransitionOut(BaseModel):
    seq: int
    from_state: str | None
    to_state: str
    actor: Actor
    reason: str | None
    evidence: dict[str, Any]
    at: str


class RunOut(BaseModel):
    id: uuid.UUID
    app_id: str
    kind: str
    state: str
    state_label_nl: str
    state_reason: str | None
    from_version: str | None
    target_version: str | None
    requested_by: Actor
    reason: str | None
    evidence: dict[str, Any]
    etag: str
    created_at: str
    updated_at: str
    allowed_next: list[str]
    manual_transitions_now: list[str] = Field(description="Overgangen die in deze fase handmatig via de API mogen.")
    transitions: list[TransitionOut]


class RunPage(BaseModel):
    items: list[RunOut]
    next_cursor: str | None


class RunCreate(BaseModel):
    app_id: str = Field(pattern=APP_ID_PATTERN, min_length=1, max_length=40, description="TrueNAS-appnaam")
    kind: RunKind
    from_version: str | None = Field(
        default=None, max_length=128, pattern=VERSION_PATTERN, description="Huidige versie (gemeten; mag 'latest' zijn)"
    )
    target_version: str | None = Field(
        default=None, max_length=128, pattern=VERSION_PATTERN, description="Exacte doelversie/digest; nooit 'latest'"
    )
    reason: str | None = Field(default=None, max_length=2000)


MAX_EVIDENCE_BYTES = 16 * 1024


class TransitionIn(BaseModel):
    to_state: LifecycleState
    reason: str = Field(min_length=3, max_length=2000)
    evidence: dict[str, Any] = Field(
        default_factory=dict,
        description="Verwijzingen naar bewijs (geen geheimen; wordt geredigeerd opgeslagen), max. 16 KiB als JSON.",
    )

    @field_validator("evidence")
    @classmethod
    def _evidence_bounded(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(json.dumps(value, ensure_ascii=False, default=str).encode()) > MAX_EVIDENCE_BYTES:
            raise ValueError(f"evidence is groter dan {MAX_EVIDENCE_BYTES} bytes")
        return value


class HubLinkOut(BaseModel):
    state: Literal["niet_gekoppeld", "gekoppeld", "fout"]
    state_label_nl: str
    hub_url: str | None
    service_id: str | None
    service_token_last4: str | None
    linked_via: str | None
    enrolled_at: str | None
    last_attempt_at: str | None
    last_error: str | None
    attempts: int


def etag_of(run: LifecycleRun) -> str:
    return f'"run-{run.id}-v{run.version}"'


def run_view(run: LifecycleRun) -> dict[str, Any]:
    state = LifecycleState(run.state)
    allowed = sorted(t.value for t in TRANSITIONS[state])
    return {
        "id": str(run.id),
        "app_id": run.app_id,
        "kind": run.kind,
        "state": run.state,
        "state_label_nl": LABELS_NL[state],
        "state_reason": run.state_reason,
        "from_version": run.from_version,
        "target_version": run.target_version,
        "requested_by": {"type": run.requested_by_type, "id": run.requested_by_id},
        "reason": run.reason,
        "evidence": run.evidence or {},
        "etag": etag_of(run),
        "created_at": iso_z(run.created_at),
        "updated_at": iso_z(run.updated_at),
        "allowed_next": allowed,
        "manual_transitions_now": sorted(t for t in allowed if LifecycleState(t) in MANUAL_ALLOWED_P0),
        "transitions": [
            {
                "seq": t.seq,
                "from_state": t.from_state,
                "to_state": t.to_state,
                "actor": {"type": t.actor_type, "id": t.actor_id},
                "reason": t.reason,
                "evidence": t.evidence or {},
                "at": iso_z(t.at),
            }
            for t in run.transitions
        ],
    }


def _parse_run_id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as exc:
        raise ApiError(404, "not_found", "Run niet gevonden.") from exc


@router.get("/statemachine", summary="Statusmachine (toestanden, overgangen, verplicht bewijs)")
def get_statemachine(_: Annotated[Principal, Depends(require_read)]) -> dict[str, Any]:
    return describe()


@router.get("/runs", response_model=RunPage, summary="Runs (nieuwste eerst, cursor-paginering)")
def list_runs(
    request: Request,
    _: Annotated[Principal, Depends(require_read)],
    app_id: Annotated[str | None, Query(pattern=APP_ID_PATTERN, max_length=40)] = None,
    state: LifecycleState | None = None,
    kind: RunKind | None = None,
    cursor: str | None = None,
    limit: Annotated[int | None, Query()] = None,
) -> dict[str, Any]:
    size = clamp_limit(limit)
    position = decode_cursor(cursor)
    stmt = select(LifecycleRun)
    if app_id:
        stmt = stmt.where(LifecycleRun.app_id == app_id)
    if state:
        stmt = stmt.where(LifecycleRun.state == state.value)
    if kind:
        stmt = stmt.where(LifecycleRun.kind == kind.value)
    if position:
        try:
            at = datetime.fromisoformat(str(position["c"]))
            rid = uuid.UUID(str(position["i"]))
        except (KeyError, ValueError) as exc:
            raise ApiError(400, "invalid_cursor", "Ongeldige cursor.") from exc
        stmt = stmt.where(
            or_(LifecycleRun.created_at < at, and_(LifecycleRun.created_at == at, LifecycleRun.id < rid))
        )
    stmt = stmt.order_by(LifecycleRun.created_at.desc(), LifecycleRun.id.desc()).limit(size + 1)
    with request.app.state.session_factory() as session:
        rows = list(session.execute(stmt).scalars().all())
        more = len(rows) > size
        rows = rows[:size]
        items = [run_view(r) for r in rows]
    next_cursor = encode_cursor({"c": rows[-1].created_at.isoformat(), "i": str(rows[-1].id)}) if more else None
    return {"items": items, "next_cursor": next_cursor}


@router.get("/runs/{run_id}", response_model=RunOut, summary="Eén run (met ETag)")
def get_run(run_id: str, request: Request, _: Annotated[Principal, Depends(require_read)]) -> JSONResponse:
    with request.app.state.session_factory() as session:
        run = session.get(LifecycleRun, _parse_run_id(run_id))
        if run is None:
            raise ApiError(404, "not_found", "Run niet gevonden.")
        return JSONResponse(run_view(run), headers={"ETag": etag_of(run)})


@router.post(
    "/runs",
    status_code=201,
    response_model=RunOut,
    summary="Run vastleggen in toestand 'discovered' (voert niets uit)",
    responses={400: {"description": "Idempotency-Key ontbreekt"}, 422: {"description": "Ongeldige invoer"}},
)
def create_run(
    body: RunCreate,
    request: Request,
    principal: Annotated[Principal, Depends(require_write)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    if body.target_version and LATEST_TAG.search(body.target_version):
        raise ApiError(
            422, "latest_forbidden", "Een versie 'latest' is verboden; geef een exacte versie of digest op."
        )
    trace = trace_id_of(request)

    def handler(session: Session) -> tuple[int, dict[str, Any], dict[str, str]]:
        now = utcnow()
        run = LifecycleRun(
            id=uuid.uuid4(),
            app_id=body.app_id,
            kind=body.kind.value,
            state=LifecycleState.DISCOVERED.value,
            from_version=body.from_version,
            target_version=body.target_version,
            requested_by_type=principal.actor["type"],
            requested_by_id=principal.subject,
            reason=redact_text(body.reason) if body.reason else None,
            trace_id=uuid.UUID(trace),
            evidence={},
            created_at=now,
            updated_at=now,
        )
        run.transitions.append(
            LifecycleTransition(
                id=uuid.uuid4(),
                seq=1,
                from_state=None,
                to_state=LifecycleState.DISCOVERED.value,
                actor_type=principal.actor["type"],
                actor_id=principal.subject,
                reason=redact_text(body.reason) if body.reason else None,
                evidence={},
                at=now,
            )
        )
        session.add(run)
        session.flush()
        emit(
            session,
            "lifecycle.run.created",
            "lifecycle_run",
            str(run.id),
            {"app_id": run.app_id, "kind": run.kind, "state": run.state, "target_version": run.target_version},
            actor=principal.actor,
            trace_id=uuid.UUID(trace),
        )
        audit(
            session,
            actor=principal.actor,
            action="lifecycle.run.create",
            target_type="lifecycle_run",
            target_id=str(run.id),
            outcome="ok",
            trace_id=uuid.UUID(trace),
            detail={"app_id": run.app_id, "kind": run.kind},
        )
        view = run_view(run)
        return 201, view, {"ETag": view["etag"], "Location": f"/lifecycle/v1/runs/{run.id}"}

    result: JSONResponse = request.app.state.idempotency.execute(
        principal=principal.key,
        raw_key=idempotency_key,
        method="POST",
        path="/lifecycle/v1/runs",
        body=body.model_dump(mode="json"),
        handler=handler,
        trace_id=trace,
    )
    return result


@router.post(
    "/runs/{run_id}/transitions",
    response_model=RunOut,
    summary="Toestandsovergang (in P0 alleen 'aborted'; overige → 501 capability_unavailable)",
)
def transition_run(
    run_id: str,
    body: TransitionIn,
    request: Request,
    principal: Annotated[Principal, Depends(require_write)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> JSONResponse:
    rid = _parse_run_id(run_id)
    trace = trace_id_of(request)

    def handler(session: Session) -> tuple[int, dict[str, Any], dict[str, str]]:
        run = session.get(LifecycleRun, rid, with_for_update=True)
        if run is None:
            raise ApiError(404, "not_found", "Run niet gevonden.")
        if not if_match:
            raise ApiError(428, "precondition_required", "Header If-Match (ETag van de run) is verplicht.")
        if if_match.strip() != etag_of(run):
            raise ApiError(
                412,
                "precondition_failed",
                "De run is intussen gewijzigd; haal hem opnieuw op en probeer het opnieuw.",
                {"current_etag": etag_of(run)},
            )
        current = LifecycleState(run.state)
        evidence: dict[str, Any] = {**body.evidence, "reason": body.reason}
        try:
            check_transition(current, body.to_state, TransitionContext(evidence))
        except TransitionError as exc:
            if exc.code == "transition_evidence_missing" and body.to_state not in MANUAL_ALLOWED_P0:
                raise capability_unavailable(f"overgang naar '{body.to_state.value}'") from exc
            raise ApiError(409, exc.code, exc.message, exc.details) from exc
        if body.to_state not in MANUAL_ALLOWED_P0:
            raise capability_unavailable(f"overgang naar '{body.to_state.value}'")
        now = utcnow()
        run.transitions.append(
            LifecycleTransition(
                id=uuid.uuid4(),
                seq=len(run.transitions) + 1,
                from_state=current.value,
                to_state=body.to_state.value,
                actor_type=principal.actor["type"],
                actor_id=principal.subject,
                reason=redact_text(body.reason),
                evidence=redact(body.evidence),  # contract §3: evidence nooit met geheimen
                at=now,
            )
        )
        run.state = body.to_state.value
        run.state_reason = redact_text(body.reason)
        run.updated_at = now
        session.flush()
        emit(
            session,
            "lifecycle.run.state_changed",
            "lifecycle_run",
            str(run.id),
            {"app_id": run.app_id, "from": current.value, "to": run.state},
            actor=principal.actor,
            trace_id=uuid.UUID(trace),
        )
        audit(
            session,
            actor=principal.actor,
            action="lifecycle.run.transition",
            target_type="lifecycle_run",
            target_id=str(run.id),
            outcome="ok",
            trace_id=uuid.UUID(trace),
            detail={"from": current.value, "to": run.state},
        )
        view = run_view(run)
        return 200, view, {"ETag": view["etag"]}

    result: JSONResponse = request.app.state.idempotency.execute(
        principal=principal.key,
        raw_key=idempotency_key,
        method="POST",
        path=f"/lifecycle/v1/runs/{rid}/transitions",
        body={**body.model_dump(mode="json"), "if_match": if_match},
        handler=handler,
        trace_id=trace,
    )
    return result


@router.get("/hub-link", response_model=HubLinkOut, summary="Koppelstatus met de Hub (zonder geheimen)")
def get_hub_link(request: Request, _: Annotated[Principal, Depends(require_read)]) -> dict[str, Any]:
    request.app.state.hub_link.invalidate()
    public: dict[str, Any] = request.app.state.hub_link.current().public()
    return public


@router.post("/hub-link/unlink", response_model=HubLinkOut, summary="Ontkoppelen (alleen admin)")
def unlink_hub(
    request: Request,
    principal: Annotated[Principal, Depends(require_admin)],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    trace = trace_id_of(request)

    def handler(session: Session) -> tuple[int, dict[str, Any], dict[str, str]]:
        unlink(session)
        audit(
            session,
            actor=principal.actor,
            action="hub_link.unlink",
            target_type="hub_link",
            target_id="1",
            outcome="ok",
            trace_id=uuid.UUID(trace),
        )
        return 200, {}, {}

    request.app.state.idempotency.execute(
        principal=principal.key,
        raw_key=idempotency_key,
        method="POST",
        path="/lifecycle/v1/hub-link/unlink",
        body={},
        handler=handler,
        trace_id=trace,
    )
    request.app.state.hub_link.invalidate()
    request.app.state.jwks_cache.clear()  # vanaf nu wordt elk JWT geweigerd tot er opnieuw gekoppeld is
    return JSONResponse(request.app.state.hub_link.current().public())


_P2 = {
    "/plans/install/preview": ("installatieplan met dry-run, poortconflicten en afhankelijkheden (B08-003)", "P2"),
    "/plans/update/preview": ("veilige update met snapshot en migratietest (B08-004)", "P2"),
    "/plans/rollback/preview": ("terugzetten naar vorige waarden + snapshot via eigenaar (B08-004)", "P2"),
    "/plans/uninstall/preview": ("verwijderen zonder data (B08-006)", "P2"),
    "/plans/move/preview": ("verhuizen naar andere host via export (B08-006)", "P2"),
}


def _make_placeholder(feature: str, phase: str) -> Any:
    def placeholder(_: Annotated[Principal, Depends(require_write)]) -> None:
        raise capability_unavailable(feature, phase)

    return placeholder


for _path, (_feature, _phase) in _P2.items():
    router.add_api_route(
        _path,
        _make_placeholder(_feature, _phase),
        methods=["POST"],
        status_code=501,
        summary=f"Nog niet gebouwd: {_feature}",
        responses={501: {"description": "capability_unavailable — er is niets uitgevoerd"}},
    )
