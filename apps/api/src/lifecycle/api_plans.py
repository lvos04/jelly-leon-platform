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

import hmac
import json
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from appstore.manifest.canonical import sha256_digest
from lifecycle import planner
from lifecycle.audit import audit
from lifecycle.auth import Principal, require_read, require_write
from lifecycle.errors import ApiError, trace_id_of
from lifecycle.hubread import Beschikbaarheid, HubLezer, Inventaris, geldig_tot
from lifecycle.idempotency import parse_key
from lifecycle.models import LifecyclePlan
from lifecycle.outbox import emit
from lifecycle.redact import redact_text
from lifecycle.timeutil import iso_z, utcnow
from lifecycle.verhuizen import Verhuisexport, Verhuisplan, plan_verhuizen
from lifecycle.verwijderen import Herstelpunt

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


class _ManifestIn(_Strikt):
    manifest_yaml: str = Field(min_length=1, max_length=MAX_MANIFEST_BYTES, description="Het manifest (YAML-tekst).")
    parameters: dict[str, Any] = Field(default_factory=dict, description="Waarden voor de manifestparameters. Nooit "
                                       "geheimen: de standaard weigert geheime parameters in formulierwaarden.")
    app_name: str | None = Field(default=None, pattern=APP_NAME, max_length=40)
    doel: DoelIn = Field(default_factory=DoelIn)

    _parameters_begrensd = field_validator("parameters")(_begrensd)


class CatalogIn(_Strikt):
    train: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    app: str = Field(pattern=APP_NAME, max_length=40)
    version: str = Field(pattern=VERSION)


class RecordIn(BaseModel):
    """Alleen de velden van het door de Hub geverifieerde record die de planner zelf narekent (§8.2)."""

    app_id: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=64)
    manifest_sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ProvenanceIn(BaseModel):
    record: RecordIn


class ReleaseIn(_Strikt):
    catalog: CatalogIn
    image_digests: list[Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]] = Field(min_length=1, max_length=20)


class InstanceIn(_Strikt):
    host_id: str = Field(min_length=1, max_length=64)
    instance_id: str = Field(pattern=UUID_PATTERN)
    registry_revision: str = Field(min_length=1, max_length=128)
    app_name: str = Field(pattern=APP_NAME, max_length=40)
    expected_version: Literal["absent"]  # alleen installatie (§8.1): de naam bestaat nog niet


class CatalogBindingIn(_Strikt):
    """``catalog_binding`` van de Hub (contract 1.3 §8.2): precies deze vijf velden. Bewaard en teruggegeven wordt
    het ontvangen object zelf, niet deze weergave."""

    appstore_service_id: str = Field(pattern=UUID_PATTERN)
    source_approval_revision: str = Field(min_length=1, max_length=128)
    provenance: ProvenanceIn
    release: ReleaseIn
    instance: InstanceIn


class InstallPlanIn(_ManifestIn):
    catalog_binding: CatalogBindingIn | None = Field(default=None, description="Alleen van de Hub als dienst, met "
                                                     "claim catalog_request_sha256 in de dienst-JWT (§8.2).")


class SchaduwIn(_Strikt):
    parameters: dict[str, Any] = Field(default_factory=dict, description="Afwijkende waarden voor de testkopie "
                                       "(minstens een vrije hostpoort); het datapad wijst het plan zelf naar de kloon.")

    _parameters_begrensd = field_validator("parameters")(_begrensd)


class UpdatePlanIn(_ManifestIn):
    schaduw: SchaduwIn | None = None


class UninstallPlanIn(_Strikt):
    app_name: str = Field(pattern=APP_NAME, max_length=40)
    herstelpunt: Herstelpunt | None = Field(default=None, description="Alleen de aangemelde Hub mag een geverifieerd "
                                            "B12-herstelpunt, RPO uit het beleid en herstelarchief aanleveren.")


class MovePlanIn(UninstallPlanIn):
    export: Verhuisexport | None = Field(default=None, description="Alleen de Hub levert het manifest, openbare "
                                         "parameters en datasetmetingen uit de geverifieerde export.")


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
    herstelgrens = document.get("herstelpunt_geldig_tot")
    if herstelgrens:
        grens = min(grens, datetime.fromisoformat(herstelgrens))
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
        "plan": {k: v for k, v in plan.document.items() if k != "catalog_binding"}, "voortgang": plan.stappen,
        "catalog_binding": plan.document.get("catalog_binding"),
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
    if principal.catalog_request_sha256 is not None and pad != INSTALL_PREVIEW:
        raise _niet_vertrouwd("claim_onverwacht")  # §8.2: alleen de installatiepreview kent deze variant

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


# ------------------------------------------------------------------ vertrouwde plannerinvoer (§8.2)
INSTALL_PREVIEW = "/lifecycle/v1/plans/install/preview"


async def _ruwe_body(request: Request) -> bytes:
    return await request.body()  # Starlette bewaart de body; de modelvalidatie leest dezelfde bytes


def _niet_vertrouwd(reden: str) -> ApiError:
    return ApiError(403, "forbidden", "Deze catalogusbinding wordt niet vertrouwd; er wordt niets gepland.",
                    {"reason": reden})


def _vertrouwde_binding(body: InstallPlanIn, raw: bytes, principal: Principal,
                        idempotency_key: str | None) -> dict[str, Any] | None:
    """Fail-closed: een binding alleen van ``service:hub`` (dienst-JWT al geverifieerd: gepinde sleutel, audience,
    service-ID, vervaltijd, eenmalige ``jti``) met een claim die past over ``[POST, pad, Idempotency-Key, body]``.
    Geeft het ontvangen object onveranderd terug; een claim zonder binding is onverwacht bewijs."""
    if body.catalog_binding is None:
        if principal.catalog_request_sha256 is not None:
            raise _niet_vertrouwd("claim_zonder_binding")
        return None
    if principal.kind != "service" or principal.subject != "hub":
        raise _niet_vertrouwd("alleen_hub_dienst")
    if principal.catalog_request_sha256 is None:
        raise _niet_vertrouwd("claim_ontbreekt")
    parse_key(idempotency_key)  # zonder geldige sleutel 400, zoals elke mutatie
    try:
        verzoek = json.loads(raw)
        verwacht = sha256_digest(["POST", INSTALL_PREVIEW, idempotency_key, verzoek])
    except ValueError:
        raise _niet_vertrouwd("claim_past_niet") from None
    if not hmac.compare_digest(verwacht, principal.catalog_request_sha256):
        raise _niet_vertrouwd("claim_past_niet")
    if body.app_name is None or body.catalog_binding.instance.app_name != body.app_name:
        raise _niet_vertrouwd("doel_past_niet")
    binding: dict[str, Any] = verzoek["catalog_binding"]
    return binding


def _toets_record(document: Mapping[str, Any], binding: Mapping[str, Any]) -> None:
    """``app_id``, versie en ``manifest_sha256`` van het zelf ingelezen manifest tegen het record van de Hub."""
    record = binding["provenance"]["record"]
    manifest = document.get("manifest") or {}
    anders = sorted(k for k in ("app_id", "version", "manifest_sha256") if manifest.get(k) != record.get(k))
    if anders:
        raise ApiError(409, "catalogusbinding_past_niet", "Het manifest past niet bij het catalogusbewijs van de "
                       "Hub; er wordt niets gepland.", {"velden": anders})


# shortcut: de Hub heeft nog geen gebonden update-catalogusroute en de updatepreview kent geen catalog_binding, dus
# elke updatepreview is los (§8.2) en geeft niets vrij; pas omzetten met die route (VERSIE 2, contracteigenaar).
GEBONDEN_UPDATEROUTE = False


def _losse_preview(document: dict[str, Any], voorstel_nl: str) -> None:
    """§8.2: losse manifest_yaml geeft alleen een onvertrouwde preview, zonder uitvoerbare stap."""
    document["blokkades"].append({
        "code": "catalogusbinding_ontbreekt", "melding_nl": "Dit is een losse preview zonder catalogusbewijs van de "
        "Hub; er wordt geen stap vrijgegeven.", "voorstel_nl": voorstel_nl, "details": {}})
    for stap in document["stappen"]:
        stap["geblokkeerd"] = True  # zoals planner._document bij elke blokkade


# ------------------------------------------------------------------ routes
@router.post("/plans/install/preview", status_code=201, responses=_ANTWOORDEN,
             summary="Installatieplan: dry-run, diff, poortconflicten en afhankelijkheden (voert niets uit)")
def preview_install(body: InstallPlanIn, request: Request, principal: Annotated[Principal, Depends(require_write)],
                    raw: Annotated[bytes, Depends(_ruwe_body)],
                    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> JSONResponse:
    plan_id = uuid.uuid4()
    doel = planner.Doel(body.doel.truenas_version, body.doel.ix_app_version)
    binding = _vertrouwde_binding(body, raw, principal, idempotency_key)

    def maak() -> dict[str, Any]:
        document = _met_bronnen(request, lambda inventaris, beschikbaar: planner.plan_installatie(
            plan_id=plan_id, manifest_yaml=body.manifest_yaml, parameters=body.parameters, app_name=body.app_name,
            doel=doel, inventaris=inventaris, beschikbaar=beschikbaar, now=utcnow()))
        if binding is not None:
            _toets_record(document, binding)
            document["catalog_binding"] = binding  # onveranderlijk: niets schrijft het document na aanmaken
        else:
            _losse_preview(document, "Installeer via de catalogus van de Hub; die maakt het plan met een "
                           "geverifieerde catalogusbinding.")
        return document

    return _bewaar(request, principal, idempotency_key, INSTALL_PREVIEW, body, plan_id, maak)


@router.post("/plans/update/preview", status_code=201, responses=_ANTWOORDEN,
             summary="Updateplan: herstelpunt, schaduwtest bij migraties, update met digest-readback (voert niets uit)")
def preview_update(body: UpdatePlanIn, request: Request, principal: Annotated[Principal, Depends(require_write)],
                   idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> JSONResponse:
    plan_id = uuid.uuid4()
    doel = planner.Doel(body.doel.truenas_version, body.doel.ix_app_version)

    def maak() -> dict[str, Any]:
        document = _met_bronnen(request, lambda inventaris, beschikbaar: planner.plan_update(
            plan_id=plan_id, manifest_yaml=body.manifest_yaml, parameters=body.parameters, app_name=body.app_name,
            doel=doel, schaduw_parameters=body.schaduw.parameters if body.schaduw else None, inventaris=inventaris,
            beschikbaar=beschikbaar, now=utcnow()))
        if not GEBONDEN_UPDATEROUTE:
            _losse_preview(document, "Een update wordt pas vrijgegeven via een gebonden catalogusroute van de Hub; "
                           "die bestaat nog niet.")
        return document

    return _bewaar(request, principal, idempotency_key, "/lifecycle/v1/plans/update/preview", body, plan_id, maak)


def _herstelbewijs_van_hub(principal: Principal) -> None:
    if principal.kind != "service" or principal.subject != "hub":
        raise ApiError(403, "forbidden", "Alleen de Hub kan een herstelpunt bevestigen.",
                       {"reason": "herstelpunt_niet_vertrouwd"})


@router.post("/plans/move/preview", response_model=Verhuisplan,
             summary="Handmatig verhuisplan via export, met datasetrollen, herstelkaart en behoud van de bron-app",
             responses={403: {"description": "Onvoldoende rechten voor de preview of het aanleveren van exportbewijs."},
                        503: {"description": "Het beschermbeleid is onleesbaar."}})
def preview_move(body: MovePlanIn, request: Request, principal: Annotated[Principal, Depends(require_write)],
                 idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> JSONResponse:
    if body.herstelpunt is not None or body.export is not None:
        _herstelbewijs_van_hub(principal)
    if principal.catalog_request_sha256 is not None:
        raise _niet_vertrouwd("claim_onverwacht")
    trace = trace_id_of(request)

    def beschrijving(session: Session) -> tuple[int, dict[str, Any], dict[str, str]]:
        plan = _met_bronnen(request, lambda inventaris, _: plan_verhuizen(
            body.app_name, inventaris, body.herstelpunt, body.export, utcnow()).model_dump(mode="json"))
        audit(session, actor=principal.actor, action="lifecycle.move.preview", target_type="app",
              target_id=body.app_name, outcome=plan["status"], trace_id=uuid.UUID(trace),
              detail={"reden": plan["reden"]})
        return 200, plan, {}

    antwoord: JSONResponse = request.app.state.idempotency.execute(
        principal=principal.key, raw_key=idempotency_key, method="POST", path="/lifecycle/v1/plans/move/preview",
        body=body.model_dump(mode="json"), handler=beschrijving, trace_id=trace)
    return antwoord


@router.post("/plans/uninstall/preview", status_code=201,
             responses={**_ANTWOORDEN, 403: {"description": "De app is beschermd of het herstelbewijs is niet van "
                                                            "de Hub."},
                        503: {"description": "De beschermlijst is niet leesbaar of wijkt af van de beleidspin."}},
             summary="Alleen de app verwijderen met behoud van alle opslag en een herstelarchief")
def preview_uninstall(body: UninstallPlanIn, request: Request, principal: Annotated[Principal, Depends(require_write)],
                      idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> JSONResponse:
    if body.herstelpunt is not None:
        _herstelbewijs_van_hub(principal)
    plan_id = uuid.uuid4()
    return _bewaar(request, principal, idempotency_key, "/lifecycle/v1/plans/uninstall/preview", body, plan_id,
                   lambda: _met_bronnen(request, lambda inventaris, _: planner.plan_verwijderen(
                       plan_id=plan_id, app_name=body.app_name, inventaris=inventaris,
                       herstelpunt=body.herstelpunt, now=utcnow())))


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
