"""P2-uitbreiding van de Hub-simulator (alleen voor tests — géén productiecode).

Wat de levenscyclusdienst en de flows in fase P2 van de Hub nodig hebben:

- **Inventaris** ``GET /api/v1/apps`` en ``GET /api/v1/apps/{id}`` in de vorm van de echte Hub (router ``catalog`` in
  ai-server-manager, gelezen op commit ``7d3a8ce``): Bearer service-token met scope ``inventaris.lezen`` (anders 403
  ``scope_ontbreekt``), cursor = base64url van ``{"o": offset, "s": snapshot}``, 400 ``invalid_cursor``, 409
  ``snapshot_changed`` als de meting tussen twee pagina's wisselt.
- **Catalogusbeschikbaarheid** (contract 1.3 §4.4, scope ``catalogus.lezen``): ``{train, app, version}`` →
  ``{beschikbaar: ja|nee|onbekend, gemeten_op}``. Het contract noemt **geen pad** en de echte Hub heeft de route nog
  niet; ``GET /api/v1/catalog/availability`` is hier een **voorlopige** keuze (ADR-0022). ``catalogus_route = False``
  geeft 404, precies zoals de echte Hub nu.
- **Beleid, goedkeuring en ondertekening** (§8) als stand-in: ``propose`` → risicoklasse volgens de contracttabel
  (``risk_claim`` telt nooit mee), ``approve`` → ``Approval`` gebonden aan de diff, ``execution_request`` → ondertekend
  met de policy-sleutel. De echte Hub heeft hiervoor nog geen routes; ``POST /api/v1/actions/proposals`` is
  **voorlopig** en dwingt voor een dienst-token de actor ``flow`` af (een flow kiest nooit zelf wie hij is).

Sim-getest; tegen de echte Hub NIET GEVALIDEERD.
"""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import contract8 as c8
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse, Response

if TYPE_CHECKING:
    from hub_sim import HubState

SCOPE_INVENTARIS = "inventaris.lezen"
SCOPE_CATALOGUS = "catalogus.lezen"
SCOPE_VOORSTEL = "acties.voorstellen"  # voorlopig: het contract kent nog geen scope voor voorstellen van een dienst
APPROVAL_TTL_S = 900
REQUEST_TTL_S = 300
STATUS_NL = {"toegestaan": "Toegestaan (R0/R1, geen goedkeuring nodig)",
             "wacht_op_goedkeuring": "Wacht op goedkeuring van de eigenaar", "goedgekeurd": "Goedgekeurd",
             "afgewezen": "Afgewezen", "uitgegeven": "Uitvoeringsverzoek uitgegeven"}


class P2Error(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details or {}


def app_record(naam: str, *, repository: str = "ghcr.io/voorbeeld/app", tag: str = "1.0.0",
               digest: str | None = None, catalogus_app: str = "ix-app", trein: str = "stable",
               versie: str = "1.4.8", staat: str = "RUNNING", app_id: str | None = None,
               poorten: list[tuple[int, int, str]] | None = None,
               opslag: list[tuple[str, str, str | None]] | None = None) -> dict[str, Any]:
    """Eén app in de detailvorm van de echte Hub. ``poorten`` = (container, host, protocol); ``opslag`` = (bron op de
    host, doel in de container, dataset)."""
    custom = catalogus_app == "ix-app"
    image = {"repository": repository, "tag": tag, "digest": digest}
    return {
        "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"hub-sim/app/{naam}")), "runtime_id": naam, "naam": naam,
        "titel": naam, "app_id": app_id, "adaptertype": "generic", "soort": "third_party",
        "installatiewijze": "custom_app" if custom else "catalog_app", "image": image,
        "versie": tag, "runtime_staat": staat, "gezondheid": {"status": "unknown"}, "deep_link": None,
        "eigenaar": {"repo": None, "soort": "third_party"}, "aanwezig": True, "eerst_gezien": None,
        "laatst_gezien": None, "verdwenen_sinds": None, "laatst_getest": None, "appstore_status": "unknown",
        "versie_etag": 1,
        "catalogus": {"app": catalogus_app, "trein": trein, "versie": versie, "app_versie": tag,
                      "nieuwste_versie": None, "upgrade_beschikbaar": None, "image_updates_beschikbaar": None},
        "images": [{**image, "digest_bron": "tagveld" if digest else None,
                    "readback": {"status": "ok" if digest else "unknown", "reden": None, "digests": []}}],
        "image_historie": [],
        "poorten": [{"container_poort": c, "host_poort": h, "host_ip": "0.0.0.0", "protocol": p}  # noqa: S104 - simdata
                    for c, h, p in poorten or []],
        "opslag": [{"bron": b, "doel": d, "type": "bind", "dataset": ds, "soort": "hostpad"}
                   for b, d, ds in opslag or []],
        "datasets": sorted({ds for _b, _d, ds in opslag or [] if ds}), "portals": [],
        "resourcegrenzen": {"status": "unknown", "reden": "config_niet_gelezen"}, "documentatie": None,
        "gezondheidsprofiel": {}, "dienst": None, "appstore": {"status": "unknown"}, "bron": "truenas",
        "acties": [], "alleen_lezen": True,
    }


SUMMARY_KEYS = ("id", "runtime_id", "naam", "titel", "app_id", "adaptertype", "soort", "installatiewijze", "image",
                "versie", "runtime_staat", "gezondheid", "deep_link", "eigenaar", "aanwezig", "eerst_gezien",
                "laatst_gezien", "verdwenen_sinds", "laatst_getest", "appstore_status", "versie_etag")


@dataclass
class Verzoek:
    request_id: str
    proposal: dict[str, Any]
    risk_class: str
    rule_ids: list[str]
    preview_hash: str
    status: str
    approval: dict[str, Any] | None = None
    uitgegeven: int = 0

    def view(self) -> dict[str, Any]:
        return {"request_id": self.request_id, "status": self.status, "status_nl": STATUS_NL[self.status],
                "risk_class": self.risk_class, "rule_ids": self.rule_ids, "policy_hash": c8.POLICY_HASH,
                "preview_hash": self.preview_hash, "action_id": self.proposal["action_id"],
                "target": self.proposal["target"], "actor": self.proposal["actor"],
                "goedkeuring_nodig": c8.RISK_ORDER[self.risk_class] >= 2}


@dataclass
class P2State:
    clock: Callable[[], float]
    scopes: dict[str, set[str]] = field(
        default_factory=lambda: {"jelly-leon-platform": {SCOPE_INVENTARIS, SCOPE_CATALOGUS}})
    apps: dict[str, dict[str, Any]] = field(default_factory=dict)  # per TrueNAS-appnaam
    snapshot_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    inventaris_status: str = "ok"
    inventaris_reden: str | None = None
    verouderd: bool = False
    gemeten_op: float | None = None  # None = nu
    catalogus: dict[tuple[str, str, str], str] = field(default_factory=dict)
    catalogus_gemeten_op: float | None = None
    catalogus_route: bool = True
    wissel_na_pagina: int = 0  # zo vaak wisselt de meting ná een eerste pagina (→ 409 snapshot_changed)
    inventaris_storing: tuple[int, str, int | None] | None = None  # (status, code, Retry-After)
    kritiek: set[str] = field(default_factory=lambda: set(c8.KRITIEKE_APPS))
    verzoeken: dict[str, Verzoek] = field(default_factory=dict)
    per_sleutel: dict[str, str] = field(default_factory=dict)
    etag_lezer: Callable[[Mapping[str, Any]], str] | None = None  # readback van een app-doel (in het echt: executor)

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), UTC).replace(microsecond=0)

    # ------------------------------------------------------------------ inventaris en catalogus
    def zet_apps(self, *records: dict[str, Any]) -> None:
        self.apps = {r["naam"]: r for r in records}
        self.snapshot_id = str(uuid.uuid4())

    def zet_beschikbaar(self, train: str, app: str, version: str, beschikbaar: str) -> None:
        if beschikbaar not in ("ja", "nee", "onbekend"):
            raise ValueError("beschikbaar: ja, nee of onbekend")
        self.catalogus[(train, app, version)] = beschikbaar

    def _iso(self, ts: float | None) -> str:
        return c8.iso(datetime.fromtimestamp(self.clock() if ts is None else ts, UTC))

    # ------------------------------------------------------------------ beleid (§8)
    def vul_expected_version(self, proposal: Mapping[str, Any]) -> dict[str, Any]:
        """Zoals de Hub bij de preview doet: de ETag van het doel uit een verse readback (nooit van de aanvrager)."""
        out = dict(proposal)
        if out.get("expected_version") is None:
            doel = c8.ACTIES.get(str(out.get("action_id")), ("", None, "", False))[2]
            vast = c8.doel_etag(doel, out.get("target") or {})
            if vast is None and self.etag_lezer is not None:
                vast = self.etag_lezer(out.get("target") or {})
            out["expected_version"] = vast
        return out

    def propose(self, proposal: Mapping[str, Any]) -> Verzoek:
        p = dict(proposal)
        onbekend = sorted(set(p) - c8.PROPOSAL_KEYS)
        ontbreekt = [k for k in ("idempotency_key", "action_id", "target", "parameters", "actor", "expected_version")
                     if p.get(k) is None]
        if onbekend or ontbreekt:
            raise P2Error(422, "validation_error", "Het voorstel volgt het contract niet.",
                          {"onbekend": onbekend, "ontbreekt": ontbreekt})
        actor = p["actor"]
        if not isinstance(actor, Mapping) or actor.get("type") not in c8.ACTOR_TYPES or not actor.get("id") \
                or not c8.UUID_RE.fullmatch(str(p["idempotency_key"])) \
                or not c8.ETAG_RE.fullmatch(str(p["expected_version"])):
            raise P2Error(422, "validation_error", "Het voorstel volgt het contract niet.", {"veld": "actor/sleutel"})
        try:
            c8.canoniek(p)
        except ValueError as exc:
            raise P2Error(422, "validation_error", str(exc)) from exc
        besluit = c8.classificeer(str(p["action_id"]), p["target"] if isinstance(p["target"], Mapping) else {},
                                  self.kritiek)
        if besluit is None:
            raise P2Error(403, "actie_niet_toegestaan", "Deze actie staat niet op de allowlist.",
                          {"action_id": p["action_id"]})
        fouten = c8.parameter_fouten(str(p["action_id"]), p["target"], p["parameters"])
        if fouten:
            raise P2Error(422, "parameters_ongeldig", "Doel of parameters vallen buiten de grenzen.",
                          {"fouten": fouten})
        key = str(p["idempotency_key"])
        bekend = self.per_sleutel.get(key)
        if bekend:
            verzoek = self.verzoeken[bekend]
            if c8.sha(verzoek.proposal) != c8.sha(p):
                raise P2Error(409, "idempotency_conflict", "Deze Idempotency-Key hoort bij een ander voorstel.")
            return verzoek
        klasse, regels = besluit
        verzoek = Verzoek(str(uuid.uuid4()), p, klasse, regels, c8.preview_hash(p),
                          "toegestaan" if c8.RISK_ORDER[klasse] < 2 else "wacht_op_goedkeuring")
        self.verzoeken[verzoek.request_id] = verzoek
        self.per_sleutel[key] = verzoek.request_id
        return verzoek

    def _verzoek(self, request_id: str) -> Verzoek:
        verzoek = self.verzoeken.get(request_id)
        if verzoek is None:
            raise P2Error(404, "not_found", "Voorstel niet gevonden.")
        return verzoek

    def approve(self, request_id: str, approved_by: str = "eigenaar-1", *,
                backup_checkpoint_id: str | None = None, ttl_s: int = APPROVAL_TTL_S) -> dict[str, Any]:
        """Menselijke goedkeuring, gebonden aan precies deze diff (actie, doel, parameters, ETag, policy-hash)."""
        verzoek = self._verzoek(request_id)
        if verzoek.status not in ("wacht_op_goedkeuring", "goedgekeurd"):
            raise P2Error(409, "goedkeuring_niet_nodig", "Dit voorstel wacht niet op goedkeuring.")
        now = self.now()
        verzoek.approval = {
            "approval_id": str(uuid.uuid4()), "request_id": request_id, "preview_hash": verzoek.preview_hash,
            "resource_etag": verzoek.proposal["expected_version"], "policy_hash": c8.POLICY_HASH,
            "risk_class": verzoek.risk_class, "approved_by": approved_by, "approved_at": c8.iso(now),
            "expires_at": c8.iso(now + timedelta(seconds=min(ttl_s, APPROVAL_TTL_S))),
            "one_time_nonce": str(uuid.uuid4()), "backup_checkpoint_id": backup_checkpoint_id,
            "action_scope": c8.action_scope(verzoek.proposal),
        }
        verzoek.status = "goedgekeurd"
        return verzoek.approval

    def reject(self, request_id: str) -> None:
        self._verzoek(request_id).status = "afgewezen"

    def execution_request(self, request_id: str, key: Ed25519PrivateKey, *, ttl_s: int = REQUEST_TTL_S
                          ) -> dict[str, Any]:
        """Ondertekend ``ExecutionRequest``; R2/R3 nooit zonder goedkeuring."""
        verzoek = self._verzoek(request_id)
        nodig = c8.RISK_ORDER[verzoek.risk_class] >= 2
        if verzoek.status == "afgewezen" or (nodig and verzoek.approval is None):
            raise P2Error(409, "goedkeuring_ontbreekt", "Zonder goedkeuring wordt dit verzoek niet uitgegeven.")
        now = self.now()
        body = {"proposal": verzoek.proposal, "approval": verzoek.approval if nodig else None,
                "policy_decision": {"risk_class": verzoek.risk_class, "rule_ids": verzoek.rule_ids,
                                    "policy_hash": c8.POLICY_HASH},
                "issued_at": c8.iso(now), "expires_at": c8.iso(now + timedelta(seconds=ttl_s)),
                "nonce": str(uuid.uuid4())}
        verzoek.status, verzoek.uitgegeven = "uitgegeven", verzoek.uitgegeven + 1
        return c8.onderteken(body, key)


def _decode_cursor(cursor: str) -> tuple[int, str]:
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return int(raw["o"]), str(raw["s"])
    except (binascii.Error, ValueError, KeyError, TypeError) as exc:
        raise P2Error(400, "invalid_cursor", "Ongeldige cursor.") from exc


def attach(app: FastAPI, state: HubState) -> None:
    """Hangt de P2-routes aan de Hub-simulator; de toestand staat in ``state.p2``."""
    p2 = state.p2

    def err(status: int, code: str, message: str, details: dict[str, Any] | None = None,
            headers: dict[str, str] | None = None) -> JSONResponse:
        return JSONResponse({"code": code, "message": message, "details": details or {},
                             "trace_id": str(uuid.uuid4())}, status_code=status, headers=headers)

    def dienst(authorization: str | None, scope: str) -> Any:
        found = state.authenticate(authorization)
        if found is None:
            return err(401, "unauthorized", "Onbekend of ingetrokken service-token")
        if scope not in p2.scopes.get(found[0].app_id, set()):
            return err(403, "scope_ontbreekt", f"Deze dienst heeft de scope {scope} niet.", {"scope": scope})
        return found[0]

    @app.get("/api/v1/apps")
    def apps(authorization: str | None = Header(default=None), limit: int = 50, cursor: str | None = None,
             alleen_aanwezig: bool = True) -> Response:
        state.calls["apps"] += 1
        svc = dienst(authorization, SCOPE_INVENTARIS)
        if isinstance(svc, JSONResponse):
            return svc
        if p2.inventaris_storing:
            status, code, retry = p2.inventaris_storing
            return err(status, code, "inventaris (sim) niet beschikbaar",
                       headers={"Retry-After": str(retry)} if retry is not None else None)
        offset = 0
        try:
            if cursor:
                offset, snap = _decode_cursor(cursor)
                if snap != p2.snapshot_id:
                    return err(409, "snapshot_changed", "De meting is gewisseld; begin opnieuw bij de eerste pagina.",
                               {"snapshot_id": p2.snapshot_id})
        except P2Error as exc:
            return err(exc.status, exc.code, exc.message)
        size = max(1, min(int(limit), 200))
        rows = sorted((a for a in p2.apps.values() if a.get("aanwezig", True) or not alleen_aanwezig),
                      key=lambda a: str(a["naam"]))
        page = rows[offset:offset + size]
        snapshot = p2.snapshot_id
        volgende = None
        if offset + size < len(rows):
            volgende = base64.urlsafe_b64encode(json.dumps({"o": offset + size, "s": snapshot}).encode()
                                                ).rstrip(b"=").decode()
            if p2.wissel_na_pagina > 0:
                p2.wissel_na_pagina -= 1
                p2.snapshot_id = str(uuid.uuid4())
        gemeten = p2._iso(p2.gemeten_op)
        return JSONResponse({
            "items": [{k: a.get(k) for k in SUMMARY_KEYS} for a in page], "totaal": len(rows),
            "per_status": {"healthy": 0, "degraded": 0, "down": 0, "unknown": len(rows)},
            "snapshot_id": snapshot, "meting_observed_at": gemeten, "next_cursor": volgende,
            "inventaris": {"status": p2.inventaris_status, "reason_code": p2.inventaris_reden,
                           "observed_at": gemeten, "verouderd": p2.verouderd},
            "observed_at": p2._iso(None), "alleen_lezen": True})

    @app.get("/api/v1/apps/{app_uuid}")
    def app_detail(app_uuid: str, authorization: str | None = Header(default=None)) -> Response:
        state.calls["app_detail"] += 1
        svc = dienst(authorization, SCOPE_INVENTARIS)
        if isinstance(svc, JSONResponse):
            return svc
        for record in p2.apps.values():
            if record["id"] == app_uuid:
                return JSONResponse(record)
        return err(404, "not_found", "App niet gevonden.")

    @app.get("/api/v1/catalog/availability")
    def availability(train: str, app: str, version: str,
                     authorization: str | None = Header(default=None)) -> Response:
        state.calls["catalog_availability"] += 1
        if not p2.catalogus_route:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        svc = dienst(authorization, SCOPE_CATALOGUS)
        if isinstance(svc, JSONResponse):
            return svc
        waarde = p2.catalogus.get((train, app, version), "onbekend")
        return JSONResponse({"train": train, "app": app, "version": version, "beschikbaar": waarde,
                             "gemeten_op": None if waarde == "onbekend" else p2._iso(p2.catalogus_gemeten_op)})

    @app.post("/api/v1/actions/proposals")
    async def propose(request: Request, authorization: str | None = Header(default=None)) -> Response:
        state.calls["proposals"] += 1
        svc = dienst(authorization, SCOPE_VOORSTEL)
        if isinstance(svc, JSONResponse):
            return svc
        body = await request.json()
        if not isinstance(body, dict):
            return err(422, "validation_error", "verwacht een JSON-object")
        flow_id = str((body.get("actor") or {}).get("id") or "onbekend") if isinstance(body.get("actor"), dict) \
            else "onbekend"
        body["actor"] = {"type": "flow", "id": f"{svc.app_id}:{flow_id}"[:128]}  # nooit door de aanvrager te kiezen
        try:
            verzoek = p2.propose(p2.vul_expected_version(body))
        except P2Error as exc:
            return err(exc.status, exc.code, exc.message, exc.details)
        return JSONResponse(verzoek.view(), status_code=201)

    @app.get("/api/v1/actions/proposals/{request_id}")
    def proposal_status(request_id: str, authorization: str | None = Header(default=None)) -> Response:
        svc = dienst(authorization, SCOPE_VOORSTEL)
        if isinstance(svc, JSONResponse):
            return svc
        verzoek = p2.verzoeken.get(request_id)
        return JSONResponse(verzoek.view()) if verzoek else err(404, "not_found", "Voorstel niet gevonden.")

    # ------------------------------------------------------------------ testbediening
    @app.post("/_sim/p2/scopes")
    async def set_scopes(request: Request) -> dict[str, list[str]]:
        body = await request.json()
        p2.scopes[str(body["app_id"])] = {str(s) for s in body.get("scopes", [])}
        return {k: sorted(v) for k, v in p2.scopes.items()}

    @app.post("/_sim/p2/services")
    async def add_service(request: Request) -> dict[str, str]:
        """Registreert een dienst (bijvoorbeeld de flow-runtime) en geeft het service-token terug — alleen test."""
        body = await request.json()
        service_id, token = state.register_service(str(body["app_id"]))
        p2.scopes[str(body["app_id"])] = {str(s) for s in body.get("scopes", [])}
        return {"service_id": service_id, "service_token": token}

    @app.post("/_sim/p2/apps")
    async def set_apps(request: Request) -> dict[str, Any]:
        body = await request.json()
        p2.zet_apps(*[app_record(**r) for r in body.get("apps", [])])
        return {"snapshot_id": p2.snapshot_id, "apps": sorted(p2.apps)}

    @app.post("/_sim/p2/catalogus")
    async def set_catalog(request: Request) -> dict[str, int]:
        body = await request.json()
        for item in body.get("items", []):
            p2.zet_beschikbaar(str(item["train"]), str(item["app"]), str(item["version"]), str(item["beschikbaar"]))
        if "route" in body:
            p2.catalogus_route = bool(body["route"])
        return {"aantal": len(p2.catalogus)}

    @app.get("/_sim/p2/verzoeken")
    def list_requests() -> list[dict[str, Any]]:
        return [{**v.view(), "proposal": v.proposal} for v in p2.verzoeken.values()]

    @app.post("/_sim/p2/verzoeken/{request_id}/goedkeuren")
    async def approve(request_id: str, request: Request) -> Response:
        body = await request.json() if await request.body() else {}
        try:
            approval = p2.approve(request_id, str(body.get("approved_by", "eigenaar-1")),
                                  backup_checkpoint_id=body.get("backup_checkpoint_id"))
        except P2Error as exc:
            return err(exc.status, exc.code, exc.message, exc.details)
        return JSONResponse(approval)
