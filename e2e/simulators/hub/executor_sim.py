"""Executor-stand-in voor de ketentest (contract 1.3 §8) — géén productiecode.

De echte executor (ai-server-manager, ``apps/executor``) weigert in zijn huidige stand nog elke actie: zijn allowlist
is leeg tot fase P2 daar is gebouwd. Voor de ketentest plan → Hub → executor → NAS staat hier een kleine eigen
uitvoerder die hetzelfde contract volgt:

1. hij controleert het ondertekende ``ExecutionRequest`` zelf opnieuw, in de volgorde en met de codes van
   ``docs/EXECUTION_REQUEST.md`` van de Hub: vorm, handtekening, tijden, policy-hash, nonce, allowlist, doel- en
   parameterschema, risicoklasse, gebonden goedkeuring, herstelpunt, eenmalige goedkeuring en daarna de precondities
   uit een eigen readback (ETag van het doel, pool gezond, herstelpunt bestaat). Een goedkeuring is verbruikt zodra
   ze is gecontroleerd — ook als een preconditie daarna niet klopt (veiligste lezing; het contract zegt het niet);
2. hij voert alleen de vaste acties van de allowlist uit, via JSON-RPC tegen de TrueNAS-simulator — nooit compose,
   nooit een shell, nooit iets destructiefs (geen dataset of snapshot verwijderen of terugrollen; bij verwijderen van
   een app blijft de data staan);
3. hij bewaart per uitvoering de vorige waarden, zodat ``app.rollback`` met ``herstel_van`` een ``app.update`` met
   precies die waarden is (§2.2);
4. hij leest terug en antwoordt ``{status, execution_id, readback}``; ``voltooid`` alleen als de readback klopt.

De vorm van dat antwoord en de codes ``herstelbron_onbekend`` en ``idempotency_conflict`` zijn eigen keuzes: het
contract legt het resultaat van ``POST /api/v1/execute`` niet vast (ADR-0022, open punt). Alles wat hiermee is
aangetoond is Sim-getest; tegen de echte executor en een echte NAS NIET GEVALIDEERD.
"""

from __future__ import annotations

import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import contract8 as c8
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

CLOCK_SKEW_S = 30
MAX_REQUEST_S = 15 * 60
MAX_APPROVAL_S = 15 * 60
# Acties die naar een pool schrijven: alleen op een gezonde pool.
SCHRIJFT_NAAR_POOL = frozenset({"app.install", "app.update", "app.rollback", "zfs.snapshot.create",
                                "zfs.snapshot.clone_to_sandbox"})
VERWACHTE_STAAT = {"app.stop": "STOPPED", "app.start": "RUNNING", "app.restart": "RUNNING", "app.install": "RUNNING",
                   "app.update": "RUNNING", "app.rollback": "RUNNING", "app.uninstall": "ABSENT",
                   "zfs.snapshot.create": "AANWEZIG", "zfs.snapshot.clone_to_sandbox": "AANWEZIG"}


class Nas(Protocol):
    """JSON-RPC naar de NAS (in de tests: de TrueNAS-simulator, ingelogd met een sleutel met schrijfrechten)."""

    def call(self, method: str, *params: Any) -> Any: ...

    def job(self, method: str, *params: Any) -> Any: ...


class Weigering(Exception):
    """De uitvoerder weigert; er is niets uitgevoerd. ``status`` is de HTTP-status van het echte endpoint."""

    def __init__(self, status: int, code: str, melding: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(melding)
        self.status, self.code, self.melding, self.details = status, code, melding, details or {}


@dataclass
class ExecutorSim:
    nas: Nas
    keys: list[Ed25519PublicKey]  # bij het koppelen gepinde policy-sleutels van de Hub
    clock: Callable[[], float] = time.time
    policy_hash: str = c8.POLICY_HASH
    kritiek: set[str] = field(default_factory=lambda: set(c8.KRITIEKE_APPS))
    nonces: set[str] = field(default_factory=set)
    goedkeuringen: set[str] = field(default_factory=set)
    journaal: dict[str, dict[str, Any]] = field(default_factory=dict)  # execution_id → vorige waarden
    resultaten: dict[str, tuple[str, dict[str, Any]]] = field(default_factory=dict)  # idempotency_key → (hash, antw.)
    aanroepen: list[str] = field(default_factory=list)  # uitgevoerde (muterende) NAS-methoden, voor de tests

    def now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), UTC).replace(microsecond=0)

    # ------------------------------------------------------------------ readback
    def _snapshots(self) -> str:
        """25.04: ``zfs.snapshot.*``; vanaf 25.10: ``pool.snapshot.*``."""
        gevonden = re.search(r"(\d+)\.(\d+)", str(self.nas.call("system.version")))
        nieuw = gevonden is not None and (int(gevonden.group(1)), int(gevonden.group(2))) >= (25, 10)
        return "pool.snapshot" if nieuw else "zfs.snapshot"

    def _app(self, naam: str) -> dict[str, Any] | None:
        rijen = self.nas.call("app.query", [["id", "=", naam]])
        return dict(rijen[0]) if rijen else None

    def etag(self, target: Mapping[str, Any]) -> str:
        """ETag van een app-doel uit een verse readback (de Hub bindt hieraan bij de preview)."""
        return c8.app_etag(self._app(str(target.get("app_id") or "")), self.nas.call("app.image.query"))

    def readback(self, naam: str) -> dict[str, Any]:
        app = self._app(naam)
        if app is None:
            return {"staat": "ABSENT", "versie": None, "images": [], "digests": [], "etag": "absent"}
        images = self.nas.call("app.image.query")
        beeld = c8.app_beeld(app, images)
        return {"staat": beeld["staat"], "versie": beeld["versie"] or None, "images": beeld["images"],
                "digests": beeld["digests"], "etag": c8.app_etag(app, images)}

    def _teruglezen(self, action_id: str, target: Mapping[str, Any], params: Mapping[str, Any]) -> dict[str, Any]:
        """Eigen readback van het doel na de actie: de app, de gemaakte snapshot of de gemaakte kloon."""
        if "app_id" in target:
            return self.readback(str(target["app_id"]))
        if action_id == "zfs.snapshot.create":
            naam = f"{target['dataset']}@{params['naam']}"
            gevonden = self.nas.call(f"{self._snapshots()}.query", [["id", "=", naam]])
        else:
            gevonden = self.nas.call("pool.dataset.query", [["id", "=", params["doel_dataset"]]])
        return {"staat": "AANWEZIG" if gevonden else "ABSENT", "versie": None, "images": [], "digests": [],
                "etag": None}

    # ------------------------------------------------------------------ controles (zelfde volgorde als de Hub-doc)
    def _envelop(self, request: Any) -> None:
        fouten = c8.vorm_fouten(request)
        if fouten:
            raise Weigering(422, "execution_request_ongeldig", "Het uitvoeringsverzoek heeft niet de juiste vorm.",
                            {"fouten": fouten[:20]})
        if not c8.handtekening_geldig(request, self.keys):
            raise Weigering(403, "handtekening_ongeldig",
                            "De handtekening van de Hub klopt niet met de gepinde policy-sleutel.")
        now, skew = self.now(), timedelta(seconds=CLOCK_SKEW_S)
        issued, expires = c8.parse_ts(request["issued_at"]), c8.parse_ts(request["expires_at"])
        if issued is None or expires is None:  # al gecontroleerd in vorm_fouten
            raise Weigering(422, "execution_request_ongeldig", "Ongeldige tijd.")
        if issued > now + skew:
            raise Weigering(403, "verzoek_nog_niet_geldig", "Het uitvoeringsverzoek is in de toekomst uitgegeven.")
        if expires <= now:
            raise Weigering(403, "verzoek_verlopen", "Het uitvoeringsverzoek is verlopen.")
        if expires <= issued or (expires - issued).total_seconds() > MAX_REQUEST_S:
            raise Weigering(403, "verzoek_geldigheid_ongeldig", "Een uitvoeringsverzoek is hooguit 15 minuten geldig.")
        if request["policy_decision"]["policy_hash"] != self.policy_hash:
            raise Weigering(409, "policy_hash_verschilt",
                            "Het beleid van de Hub is niet hetzelfde als dat van de uitvoerder; er is niets "
                            "uitgevoerd.", {"eigen_policy_hash": self.policy_hash})
        if request["nonce"] in self.nonces:
            raise Weigering(409, "nonce_al_gebruikt", "Dit uitvoeringsverzoek is al eens aangeboden.")
        self.nonces.add(str(request["nonce"]))

    def _besluit(self, request: Mapping[str, Any]) -> tuple[str, bool]:
        """Eigen herberekening; geeft (risicoklasse, goedkeuring nodig)."""
        prop = request["proposal"]
        action_id, target, params = str(prop["action_id"]), prop["target"], prop["parameters"]
        eigen = c8.classificeer(action_id, target if isinstance(target, Mapping) else {}, self.kritiek)
        if eigen is None:
            raise Weigering(403, "actie_niet_toegestaan", "Deze actie staat niet op de allowlist.")
        if c8.ACTIES[action_id][2] == "geen":
            raise Weigering(403, "actie_niet_toegestaan", "Deze actie hoort niet bij deze uitvoerder.")
        fouten = c8.parameter_fouten(action_id, target, params)
        if fouten:
            raise Weigering(422, "parameters_ongeldig", "Doel of parameters voldoen niet aan het schema.",
                            {"fouten": fouten[:20]})
        klasse = eigen[0]
        if c8.RISK_ORDER[str(request["policy_decision"]["risk_class"])] < c8.RISK_ORDER[klasse]:
            raise Weigering(403, "risicoklasse_te_laag",
                            "De risicoklasse in het verzoek is lager dan de eigen herberekening.",
                            {"eigen_risk_class": klasse})
        return klasse, c8.RISK_ORDER[klasse] >= 2

    def _goedkeuring(self, request: Mapping[str, Any], klasse: str) -> Mapping[str, Any]:
        prop, approval = request["proposal"], request.get("approval")
        if not isinstance(approval, Mapping):
            raise Weigering(403, "goedkeuring_ontbreekt", "Deze actie vraagt een menselijke goedkeuring.")
        now, skew = self.now(), timedelta(seconds=CLOCK_SKEW_S)
        approved, expires = c8.parse_ts(approval.get("approved_at")), c8.parse_ts(approval.get("expires_at"))
        if approved is None or expires is None:
            raise Weigering(422, "execution_request_ongeldig", "Ongeldige tijd in de goedkeuring.")
        if approved > now + skew:
            raise Weigering(403, "goedkeuring_ongeldig", "De goedkeuring ligt in de toekomst.")
        if expires <= now:
            raise Weigering(403, "goedkeuring_verlopen", "De goedkeuring is verlopen (hooguit 15 minuten geldig).")
        if expires <= approved or (expires - approved).total_seconds() > MAX_APPROVAL_S:
            raise Weigering(403, "goedkeuring_ongeldig", "Een goedkeuring is hooguit 15 minuten geldig.")
        gebonden = {
            "policy_hash": approval.get("policy_hash") == self.policy_hash,
            "resource_etag": approval.get("resource_etag") == prop.get("expected_version"),
            "action_scope": approval.get("action_scope") == c8.action_scope(prop),
            "preview_hash": approval.get("preview_hash") == c8.preview_hash(prop),
            "risk_class": c8.RISK_ORDER.get(str(approval.get("risk_class")), -1) >= c8.RISK_ORDER[klasse],
        }
        anders = sorted(k for k, ok in gebonden.items() if not ok)
        if anders:
            raise Weigering(409, "goedkeuring_past_niet",
                            "De goedkeuring hoort niet (meer) bij deze actie; keur opnieuw goed.",
                            {"gewijzigd": anders})
        if c8.ACTIES[str(prop["action_id"])][3] and not approval.get("backup_checkpoint_id"):
            raise Weigering(409, "backup_checkpoint_ontbreekt",
                            "Deze actie vraagt een geverifieerd herstelpunt in de goedkeuring.")
        if approval["one_time_nonce"] in self.goedkeuringen:
            raise Weigering(409, "goedkeuring_al_gebruikt", "Deze goedkeuring is al één keer gebruikt.")
        # Eenmalig: de goedkeuring is hiermee verbruikt, ook als een preconditie hierna niet klopt. Dan is er toch een
        # nieuwe preview en een nieuwe goedkeuring nodig (de ETag is veranderd).
        self.goedkeuringen.add(str(approval["one_time_nonce"]))
        return approval

    def _precondities(self, prop: Mapping[str, Any], approval: Mapping[str, Any] | None) -> None:
        action_id, target, params = str(prop["action_id"]), prop["target"], prop["parameters"]
        doel, verwacht = c8.ACTIES[action_id][2], str(prop["expected_version"])
        if doel in ("app", "app_nieuw"):
            gemeten = self.etag(target)
            if doel == "app_nieuw" and (verwacht != "absent" or gemeten != "absent"):
                raise Weigering(409, "doel_gewijzigd", "Er bestaat al een app met deze naam.", {"gemeten": gemeten})
            if doel == "app" and (gemeten == "absent" or gemeten != verwacht):
                raise Weigering(409, "doel_gewijzigd", "De app is gewijzigd sinds de preview; maak een nieuw voorstel.",
                                {"verwacht": verwacht, "gemeten": gemeten})
        elif doel == "dataset":
            if verwacht != f"dataset:{target['dataset']}" \
                    or not self.nas.call("pool.dataset.query", [["id", "=", target["dataset"]]]):
                raise Weigering(409, "doel_gewijzigd", "De dataset bestaat niet (meer).")
        elif doel == "snapshot" and (
                verwacht != f"snapshot:{target['snapshot']}"
                or not self.nas.call(f"{self._snapshots()}.query", [["id", "=", target["snapshot"]]])):
            raise Weigering(409, "doel_gewijzigd", "De snapshot bestaat niet (meer).")
        if action_id in SCHRIJFT_NAAR_POOL:
            ziek = sorted(str(p.get("name")) for p in self.nas.call("pool.query") if not p.get("healthy"))
            if ziek:
                raise Weigering(409, "pool_niet_gezond", "Een pool is niet gezond; er wordt niets gewijzigd.",
                                {"pools": ziek})
        if approval is not None and c8.ACTIES[action_id][3]:
            punt = str(approval.get("backup_checkpoint_id") or "")
            if not self.nas.call(f"{self._snapshots()}.query", [["snapshot_name", "=", punt]]):
                raise Weigering(409, "backup_checkpoint_ontbreekt",
                                "Het herstelpunt uit de goedkeuring bestaat niet op de NAS.", {"herstelpunt": punt})
        if action_id == "app.rollback" and "herstel_van" in params:
            bron = self.journaal.get(str(params["herstel_van"]))
            if bron is None or bron["app"] != target.get("app_id") or "image" not in bron["vorige"]:
                raise Weigering(409, "herstelbron_onbekend",
                                "Van deze uitvoering zijn geen vorige waarden bewaard voor deze app.")

    # ------------------------------------------------------------------ uitvoeren
    def _job(self, method: str, *params: Any) -> Any:
        self.aanroepen.append(method)
        return self.nas.job(method, *params)

    def _call(self, method: str, *params: Any) -> Any:
        self.aanroepen.append(method)
        return self.nas.call(method, *params)

    def _voer_uit(self, execution_id: str, action_id: str, target: Mapping[str, Any], params: Mapping[str, Any]
                  ) -> None:
        naam = str(target.get("app_id") or "")
        if action_id == "app.stop":
            self._job("app.stop", naam)
        elif action_id == "app.start":
            self._job("app.start", naam)
        elif action_id == "app.restart":
            self._job("app.stop", naam)
            self._job("app.start", naam)
        elif action_id == "app.install":
            self._job("app.create", dict(params["app_create"]))
        elif action_id == "app.update" and "image" in params:
            vorige = dict(self.nas.call("app.config", naam).get("image") or {})
            self.journaal[execution_id] = {"action_id": action_id, "app": naam, "vorige": {"image": vorige}}
            nieuw = {**vorige, "repository": params["image"]["repository"], "tag": params["image"]["tag"]}
            self._job("app.update", naam, {"values": {"image": nieuw}})
        elif action_id == "app.update":
            app = self._app(naam) or {}
            self.journaal[execution_id] = {"action_id": action_id, "app": naam,
                                           "vorige": {"catalog_version": app.get("version")}}
            self._job("app.upgrade", naam, {"app_version": str(params["catalog_version"])})
        elif action_id == "app.rollback" and "herstel_van" in params:
            # Custom App: géén app.rollback van TrueNAS (die kent alleen catalogusversies) maar app.update met de
            # bewaarde vorige waarden; de data wordt nooit teruggerold.
            self._job("app.update", naam, {"values": {"image": self.journaal[str(params["herstel_van"])]["vorige"]
                                                      ["image"]}})
        elif action_id == "app.rollback":
            self._job("app.rollback", naam, {"app_version": str(params["catalog_version"]),
                                             "rollback_snapshot": False})
        elif action_id == "app.uninstall":
            self._job("app.delete", naam, {"remove_ix_volumes": False})
        elif action_id == "zfs.snapshot.create":
            self._call(f"{self._snapshots()}.create", {"dataset": target["dataset"], "name": params["naam"],
                                                       "recursive": bool(params.get("recursief", False))})
        elif action_id == "zfs.snapshot.clone_to_sandbox":
            self._call(f"{self._snapshots()}.clone", {"snapshot": target["snapshot"],
                                                      "dataset_dst": params["doel_dataset"]})

    def execute(self, request: Any) -> dict[str, Any]:
        """Controleert, voert uit en leest terug. Een weigering is een :class:`Weigering` (niets uitgevoerd)."""
        self._envelop(request)
        prop = request["proposal"]
        klasse, nodig = self._besluit(request)
        approval = self._goedkeuring(request, klasse) if nodig else None
        # Een herhaald voorstel zonder goedkeuring (R0/R1, nieuw verzoek met dezelfde idempotency_key) wordt niet nog
        # eens uitgevoerd; met goedkeuring is een herhaling hierboven al geweigerd (goedkeuring_al_gebruikt).
        sleutel, vingerafdruk = str(prop["idempotency_key"]), c8.sha(prop)
        eerder = self.resultaten.get(sleutel)
        if eerder is not None:
            if eerder[0] != vingerafdruk:
                raise Weigering(409, "idempotency_conflict", "Deze Idempotency-Key hoort bij een ander voorstel.")
            return {**eerder[1], "herhaling": True}
        self._precondities(prop, approval)
        action_id, target, params = str(prop["action_id"]), prop["target"], prop["parameters"]
        execution_id, reden = str(uuid.uuid4()), None
        try:
            self._voer_uit(execution_id, action_id, target, params)
        except Exception as exc:  # de NAS weigert of de job mislukt: eerlijk melden, nooit als succes
            reden = f"nas_fout: {str(exc)[:300]}"
        readback = self._teruglezen(action_id, target, params)
        staat = VERWACHTE_STAAT[action_id]
        if reden is None and readback["staat"] != staat:
            reden = f"readback_afwijking: toestand is {readback['staat']}, verwacht {staat}"
        antwoord = {"status": "mislukt" if reden else "voltooid", "execution_id": execution_id,
                    "action_id": action_id, "target": dict(target), "risk_class": klasse, "readback": readback,
                    "reden": reden, "uitgevoerd_op": c8.iso(self.now())}
        self.resultaten[sleutel] = (vingerafdruk, antwoord)
        return antwoord


__all__ = ["ExecutorSim", "Nas", "Weigering"]
