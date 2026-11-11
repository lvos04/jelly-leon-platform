"""``/lifecycle/v1/plans``: installatiepreview, veilige update en terugzetten als **plan** (B08-003, B08-004).

Een plan is een voorstel, geen uitvoering. Deze dienst heeft geen TrueNAS-sleutel en voert niets uit: het plan levert
de diff ("Diff met vereiste goedkeuring"), de blokkades en een expliciete DAG van stappen. Elke stap is een sjabloon
voor een ``ActionProposal`` (contract §8); de Hub beoordeelt, vraagt goedkeuring, ondertekent en laat de executor
uitvoeren. Het resultaat per stap (``execution_id`` + readback) komt terug via ``…/steps/{id}/result``; pas daarna
geeft het plan de volgende stap vrij.

De inventaris en de catalogusbeschikbaarheid worden bij het maken van het plan **gemeten bij de Hub** (scopes
``inventaris.lezen`` en ``catalogus.lezen``, C1.3-JLP-14). Zonder koppeling, scope of verse meting is het plan
``geblokkeerd``: onbekend is niet goed.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from lifecycle import planner
from lifecycle.audit import audit
from lifecycle.auth import Principal, require_read, require_write
from lifecycle.errors import ApiError, trace_id_of
from lifecycle.hubread import Beschikbaarheid, HubLezer, Inventaris, geldig_tot
from lifecycle.models import LifecyclePlan
from lifecycle.outbox import emit
from lifecycle.redact import redact_text
from lifecycle.timeutil import iso_z, utcnow

router = APIRouter(prefix="/lifecycle/v1", tags=["plannen"])

# Een niet-gestart plan vervalt op het vroegste van (aanmaken + PLAN_TTL) en (de oudste gebruikte meting + 900 s,
# contract §6.2): daarna is de meting eronder te oud om nog een voorstel op te bouwen (review P2, R-TECH-2).
PLAN_TTL = timedelta(hours=1)
MAX_MANIFEST_BYTES = 200_000
MAX_PARAMETERS_BYTES = 16_384
APP_NAME = r"^[a-z]([-a-z0-9]*[a-z0-9])?$"
VERSION = r"^\d{1,6}(\.\d{1,6}){1,3}$"
UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

Beschikbaar = Callable[[str, str, str], Beschikbaarheid]


class _Strikt(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DoelIn(_Strikt):
    """Versies van de NAS waarvoor het plan geldt. De Hub-inventaris levert ze nog niet; de aanvrager geeft ze op."""

    truenas_version: str | None = Field(default=None, pattern=r"^[0-9A-Za-z][0-9A-Za-z._-]{0,39}$")
    ix_app_version: str | None = Field(default=None, pattern=VERSION)


def _begrensd(value: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(value, ensure_ascii=False, default=str).encode()) > MAX_PARAMETERS_BYTES:
        raise ValueError(f"parameters zijn groter dan {MAX_PARAMETERS_BYTES} bytes")
    return value


class InstallPlanIn(_Strikt):
    manifest_yaml: str = Field(min_length=1, max_length=MAX_MANIFEST_BYTES, description="Het manifest (YAML-tekst).")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Waarden voor de manifestparameters. Nooit "
                                       "geheimen: de standaard weigert geheime parameters in formulierwaarden.")
    app_name: str | None = Field(default=None, pattern=APP_NAME, max_length=40)
    doel: DoelIn = Field(default_factory=DoelIn)

    _parameters_begrensd = field_validator("parameters")(_begrensd)


class SchaduwIn(_Strikt):
    parameters: dict[str, Any] = Field(default_factory=dict, description="Afwijkende waarden voor de testkopie "
                                       "(minstens een vrije hostpoort); het datapad wijst het plan zelf naar de kloon.")

    _parameters_begrensd = field_validator("parameters")(_begrensd)


class UpdatePlanIn(InstallPlanIn):
    schaduw: SchaduwIn | None = None


class CatalogIn(_Strikt):
    train: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    app: str = Field(pattern=APP_NAME, max_length=40)
    version: str = Field(pattern=VERSION)


class RollbackPlanIn(_Strikt):
    update_plan_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    app_name: str | None = Field(default=None, pattern=APP_NAME, max_length=40)
    herstel_van: str | None = Field(default=None, pattern=UUID_PATTERN,
                                    description="execution_id van de update die wordt teruggedraaid (Custom App).")
    catalog: CatalogIn | None = None

    @model_validator(mode="after")
    def _precies_een_bron(self) -> RollbackPlanIn:
        bronnen = [self.update_plan_id is not None, self.herstel_van is not None, self.catalog is not None]
        if sum(bronnen) != 1:
            raise ValueError("geef precies één bron op: update_plan_id, herstel_van of catalog")
        if self.update_plan_id is None and self.app_name is None:
            raise ValueError("app_name is verplicht zonder update_plan_id")
        return self


class ReadbackIn(_Strikt):
    """De teruggelezen toestand van het doel na de stap, zoals de melder die doorgeeft (bedoeld: de readback van de
    executor, via de Hub). Deze dienst kan haar niet zelf nagaan; de Hub toetst elk volgend voorstel opnieuw met een
    eigen readback (ADR-0025). Geen vrije configuratie."""

    staat: str = Field(max_length=32)
    versie: str | None = Field(default=None, max_length=128)
    digests: list[str] = Field(default_factory=list, max_length=20)
    images: list[str] = Field(default_factory=list, max_length=20)
    etag: str | None = Field(default=None, max_length=80)


class StepResultIn(_Strikt):
    status: Literal["voltooid", "mislukt"]
    execution_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    readback: ReadbackIn | None = None
    reden: str | None = Field(default=None, max_length=500)


def vervaltijd(document: Mapping[str, Any], now: datetime) -> datetime:
    """Het vroegste van ``now + PLAN_TTL`` en het einde van de versheid van elke gebruikte meting (inventaris en
    catalogus met status ``ok``) uit het plan."""
    grens = now + PLAN_TTL
    bronnen = document.get("bronnen") or {}
    for bron in (bronnen.get("inventaris"), bronnen.get("catalogus")):
        if isinstance(bron, Mapping) and bron.get("status") == "ok":
            tot = geldig_tot(bron.get("gemeten_op"))
            if tot is not None and tot < grens:
                grens = tot
    return grens


# ------------------------------------------------------------------ weergave
def etag_of(plan: LifecyclePlan) -> str:
    return f'"plan-{plan.id}-v{plan.version}"'


def plan_view(plan: LifecyclePlan) -> dict[str, Any]:
    # Een vervallen plan biedt geen stap meer aan: de meting eronder is te oud om nog een voorstel op te bouwen.
    vervallen = plan.status == "klaar_voor_voorstel" and plan.expires_at <= utcnow()
    return {
        "id": str(plan.id), "soort": plan.soort, "app_id": plan.app_id, "app_name": plan.app_name,
        "status": plan.status,
        "vervallen": vervallen,
        "preview_hash": plan.preview_hash, "manifest_sha256": plan.manifest_sha256,
        "plan": plan.document, "voortgang": plan.stappen,
        "vrijgegeven_stappen": [] if vervallen else [s["id"] for s in plan.stappen
                                                     if s.get("status") == "vrijgegeven"],
        "created_by": {"type": plan.created_by_type, "id": plan.created_by_id},
        "etag": etag_of(plan), "created_at": iso_z(plan.created_at), "updated_at": iso_z(plan.updated_at),
        "expires_at": iso_z(plan.expires_at),
        "uitvoering_nl": "Dit plan voert niets uit. Elke vrijgegeven stap is een voorstel voor de Hub (contract §8); "
                         "de Hub beslist, vraagt goedkeuring en laat de executor uitvoeren.",
    }


def _parse_plan_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError:
        raise ApiError(404, "not_found", "Plan niet gevonden.") from None


# ------------------------------------------------------------------ bronnen bij de Hub
def _met_bronnen(request: Request, bouw: Callable[[Inventaris, Beschikbaar], dict[str, Any]]) -> dict[str, Any]:
    """Meet de inventaris en biedt de catalogusmeting aan de planner aan. Niet gekoppeld → alles ``unknown``."""
    link = request.app.state.hub_link.read()
    if not link.linked or not link.hub_url or not link.service_token:
        def onbekend(train: str, app: str, version: str) -> Beschikbaarheid:
            return Beschikbaarheid("onbekend", "unknown", "niet_gekoppeld", None, train, app, version)

        return bouw(Inventaris("unknown", "niet_gekoppeld"), onbekend)
    with request.app.state.http_client_factory() as client:
        lezer = HubLezer(client, link.hub_url, link.service_token)
        return bouw(lezer.inventaris(), lezer.beschikbaarheid)


def _bewaar(request: Request, principal: Principal, idempotency_key: str | None, pad: str, body: BaseModel,
            plan_id: uuid.UUID, maak_document: Callable[[], dict[str, Any]]) -> JSONResponse:
    """Legt het plan idempotent vast. Het document wordt pas gemaakt (en de Hub pas gemeten) ná de claim van de
    Idempotency-Key: een verzoek zonder sleutel of een herhaling meet niets en maakt geen tweede plan. De meting loopt
    vóór de eerste databasevraag van de handler, dus er staat geen transactie open tijdens de HTTP-aanroepen."""
    trace = trace_id_of(request)

    def handler(session: Session) -> tuple[int, dict[str, Any], dict[str, str]]:
        document = maak_document()
        now = utcnow()
        stappen = planner.begin(document)
        vast = document.get("vastgepind") or {}
        manifest = document.get("manifest") or {}
        plan = LifecyclePlan(
            id=plan_id, soort=document["soort"], app_id=document.get("app_id"), app_name=document.get("app_name"),
            status=planner.plan_status(document, stappen), preview_hash=vast.get("preview_hash"),
            manifest_sha256=manifest.get("manifest_sha256"), document=document, stappen=stappen,
            created_by_type=principal.actor["type"], created_by_id=principal.subject, trace_id=uuid.UUID(trace),
            created_at=now, updated_at=now, expires_at=vervaltijd(document, now),
        )
        session.add(plan)
        session.flush()
        codes = sorted({b["code"] for b in document.get("blokkades") or []})
        emit(session, "lifecycle.plan.created", "lifecycle_plan", str(plan.id),
             {"soort": plan.soort, "app_id": plan.app_id, "app_name": plan.app_name, "status": plan.status,
              "blokkades": codes, "stappen": [s["id"] for s in document.get("stappen") or []]},
             actor=principal.actor, trace_id=uuid.UUID(trace))
        audit(session, actor=principal.actor, action="lifecycle.plan.create", target_type="lifecycle_plan",
              target_id=str(plan.id), outcome="geblokkeerd" if codes else "ok", trace_id=uuid.UUID(trace),
              detail={"soort": plan.soort, "app_name": plan.app_name, "blokkades": codes})
        view = plan_view(plan)
        return 201, view, {"ETag": view["etag"], "Location": f"/lifecycle/v1/plans/{plan.id}"}

    result: JSONResponse = request.app.state.idempotency.execute(
        principal=principal.key, raw_key=idempotency_key, method="POST", path=pad,
        body=body.model_dump(mode="json"), handler=handler, trace_id=trace)
    return result


_ANTWOORDEN: dict[int | str, dict[str, Any]] = {
    201: {"description": "Het plan is vastgelegd. `status: geblokkeerd` betekent: geen enkele stap vrijgegeven."},
}


# ------------------------------------------------------------------ routes
@router.post("/plans/install/preview", status_code=201, responses=_ANTWOORDEN,
             summary="Installatieplan: dry-run, diff, poortconflicten en afhankelijkheden (voert niets uit)")
def preview_install(body: InstallPlanIn, request: Request, principal: Annotated[Principal, Depends(require_write)],
                    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> JSONResponse:
    plan_id = uuid.uuid4()
    doel = planner.Doel(body.doel.truenas_version, body.doel.ix_app_version)

    def maak() -> dict[str, Any]:
        return _met_bronnen(request, lambda inventaris, beschikbaar: planner.plan_installatie(
            plan_id=plan_id, manifest_yaml=body.manifest_yaml, parameters=body.parameters, app_name=body.app_name,
            doel=doel, inventaris=inventaris, beschikbaar=beschikbaar, now=utcnow()))

    return _bewaar(request, principal, idempotency_key, "/lifecycle/v1/plans/install/preview", body, plan_id, maak)


@router.post("/plans/update/preview", status_code=201, responses=_ANTWOORDEN,
             summary="Updateplan: herstelpunt, schaduwtest bij migraties, update met digest-readback (voert niets uit)")
def preview_update(body: UpdatePlanIn, request: Request, principal: Annotated[Principal, Depends(require_write)],
                   idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> JSONResponse:
    plan_id = uuid.uuid4()
    doel = planner.Doel(body.doel.truenas_version, body.doel.ix_app_version)

    def maak() -> dict[str, Any]:
        return _met_bronnen(request, lambda inventaris, beschikbaar: planner.plan_update(
            plan_id=plan_id, manifest_yaml=body.manifest_yaml, parameters=body.parameters, app_name=body.app_name,
            doel=doel, schaduw_parameters=body.schaduw.parameters if body.schaduw else None, inventaris=inventaris,
            beschikbaar=beschikbaar, now=utcnow()))

    return _bewaar(request, principal, idempotency_key, "/lifecycle/v1/plans/update/preview", body, plan_id, maak)


@router.post("/plans/rollback/preview", status_code=201, responses=_ANTWOORDEN,
             summary="Terugzetplan: vorige waarden (Custom App) of gemeten catalogusversie (voert niets uit)")
def preview_rollback(body: RollbackPlanIn, request: Request, principal: Annotated[Principal, Depends(require_write)],
                     idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> JSONResponse:
    plan_id = uuid.uuid4()

    def maak() -> dict[str, Any]:
        bron_document: dict[str, Any] | None = None
        bron_stappen: list[Any] | None = None
        if body.update_plan_id is not None:
            with request.app.state.session_factory() as session:
                bron = session.get(LifecyclePlan, uuid.UUID(body.update_plan_id))
                if bron is None:
                    raise ApiError(404, "not_found", "Het updateplan is niet gevonden.")
                bron_document, bron_stappen = dict(bron.document), list(bron.stappen)
        return _met_bronnen(request, lambda inventaris, beschikbaar: planner.plan_rollback(
            plan_id=plan_id, app_name=body.app_name, herstel_van=body.herstel_van,
            catalog=body.catalog.model_dump() if body.catalog else None, update_plan=bron_document,
            update_stappen=bron_stappen, inventaris=inventaris, beschikbaar=beschikbaar, now=utcnow()))

    return _bewaar(request, principal, idempotency_key, "/lifecycle/v1/plans/rollback/preview", body, plan_id, maak)


@router.get("/plans/{plan_id}", summary="Eén plan: diff, blokkades, stappen en voortgang (met ETag)")
def get_plan(plan_id: str, request: Request, _: Annotated[Principal, Depends(require_read)]) -> JSONResponse:
    with request.app.state.session_factory() as session:
        plan = session.get(LifecyclePlan, _parse_plan_id(plan_id))
        if plan is None:
            raise ApiError(404, "not_found", "Plan niet gevonden.")
        return JSONResponse(plan_view(plan), headers={"ETag": etag_of(plan)})


@router.post("/plans/{plan_id}/steps/{step_id}/result",
             summary="Resultaat van één vrijgegeven stap melden (execution_id + readback); geeft de volgende stap vrij",
             responses={409: {"description": "stap_niet_vrijgegeven, plan_geblokkeerd of plan_vervallen"}})
def report_step(plan_id: str, step_id: str, body: StepResultIn, request: Request,
                principal: Annotated[Principal, Depends(require_write)],
                idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> JSONResponse:
    trace = trace_id_of(request)
    pid = _parse_plan_id(plan_id)

    def handler(session: Session) -> tuple[int, dict[str, Any], dict[str, str]]:
        plan = session.get(LifecyclePlan, pid, with_for_update=True)
        if plan is None:
            raise ApiError(404, "not_found", "Plan niet gevonden.")
        if plan.status == "geblokkeerd":
            raise ApiError(409, "plan_geblokkeerd", "Dit plan is geblokkeerd; er is geen stap vrijgegeven.")
        if plan.status == "klaar_voor_voorstel" and plan.expires_at <= utcnow():
            raise ApiError(409, "plan_vervallen", "Dit plan is vervallen voordat het is gestart; maak een nieuw plan "
                           "op een verse meting.")
        try:
            stappen = planner.meld(
                plan.document, plan.stappen, step_id, status=body.status, execution_id=body.execution_id,
                readback=body.readback.model_dump() if body.readback else None,
                reden=redact_text(body.reden) if body.reden else None, gemeld_op=iso_z(utcnow()) or "")
        except planner.ResultaatFout as exc:
            raise ApiError(exc.status, exc.code, exc.melding) from None
        plan.stappen = stappen
        plan.status = planner.plan_status(plan.document, stappen)
        plan.updated_at = utcnow()
        session.flush()
        stap = next(s for s in stappen if s["id"] == step_id)
        emit(session, "lifecycle.plan.step_reported", "lifecycle_plan", str(plan.id),
             {"soort": plan.soort, "app_name": plan.app_name, "stap": step_id, "stap_status": stap["status"],
              "reden": stap["reden"], "execution_id": stap["execution_id"], "status": plan.status},
             actor=principal.actor, trace_id=uuid.UUID(trace))
        audit(session, actor=principal.actor, action="lifecycle.plan.step_result", target_type="lifecycle_plan",
              target_id=str(plan.id), outcome=stap["status"], trace_id=uuid.UUID(trace),
              detail={"stap": step_id, "execution_id": stap["execution_id"], "reden": stap["reden"],
                      "plan_status": plan.status})
        view = plan_view(plan)
        return 200, view, {"ETag": view["etag"]}

    result: JSONResponse = request.app.state.idempotency.execute(
        principal=principal.key, raw_key=idempotency_key, method="POST",
        path=f"/lifecycle/v1/plans/{pid}/steps/{step_id}/result", body=body.model_dump(mode="json"),
        handler=handler, trace_id=trace)
    return result
