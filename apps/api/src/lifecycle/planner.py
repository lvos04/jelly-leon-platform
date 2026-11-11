"""Planner voor installeren, bijwerken en terugzetten (B08-003, B08-004; contract 1.3 §2.2, §8, §9).

**Puur:** geen netwerk, geen database, geen NAS. De planner maakt uit een manifest, parameters en gemeten bronnen (de
inventaris en de catalogusbeschikbaarheid van de Hub, ``lifecycle.hubread``) een onveranderlijk **plandocument**: een
diff, blokkades en een expliciete DAG van stappen. Elke stap is een sjabloon voor een ``ActionProposal`` (§8). De
dienst voert zelf niets uit en heeft geen TrueNAS-sleutel: de Hub beoordeelt elke stap met zijn eigen policy, vraagt
goedkeuring per gebonden diff en ondertekent het ``ExecutionRequest``; de executor voert uit en leest terug.

- De manifestregels komen uit de byte-gelijke kopie van de standaard (``appstore.manifest``, ADR-0021): valideren,
  vertalen naar formulierwaarden en conflictanalyse. Hier staan geen eigen manifestregels.
- Eén fout uit validatie, vertaling of conflictanalyse (poort in gebruik, vereiste afhankelijkheid ontbreekt, versie
  niet in de catalogus) of een eigen blokkade → het plan is ``geblokkeerd`` en geeft **geen enkele stap vrij**.
- Onbekend is niet goed: zonder gemeten inventaris of catalogusbeschikbaarheid wordt niets vrijgegeven. Een meting
  ouder dan 900 s (§6.2) is onbekend, voor elk soort plan — ook een update of terugzetplan zonder conflictanalyse.
- Een stap telt pas als ``voltooid`` wanneer de teruggelezen toestand en digest kloppen met wat het plan verwacht.
  Terugzetten: de digest van vóór de update moet terug zijn; is die niet gemeten, dan mag in elk geval de digest van
  vóór het terugzetten er niet meer staan en zegt het plan dat de vorige versie niet bewezen is.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from appstore.manifest import Finding, Severity, validate_text
from appstore.manifest.conflicts import analyse
from appstore.manifest.model import Manifest, ParamRef
from appstore.manifest.translate import Target, translate
from lifecycle.custom_app import (
    DIGEST_RE,
    IX_APP,
    Blocker,
    RecipeError,
    catalog_version_blocker,
    check_digest_readback,
    form_tag,
    split_form_tag,
)
from lifecycle.hubread import Beschikbaarheid, Inventaris, verse_inventaris, verse_meting

SCHEMA = "jlp-plan/1"
SANDBOX = "asm-sandbox"  # vaste sandbox-dataset van de policy: <pool>/asm-sandbox/<naam>
SCHADUW_ACHTERVOEGSEL = "-test"
APP_NAME_RE = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
# Verwachte risicoklasse per actie (contract §8). Alleen ter voorbereiding van het scherm: de Hub beslist.
RISICO = {"app.install": "R2", "app.update": "R2", "app.rollback": "R2", "app.uninstall": "R2", "app.stop": "R1",
          "app.start": "R1", "zfs.snapshot.create": "R1", "zfs.snapshot.clone_to_sandbox": "R1", "handmatig": "R3"}
TERMINAAL = frozenset({"voltooid", "mislukt", "geblokkeerd", "overgeslagen"})
UITGAAND_NL = ("Het Custom App-formulier van TrueNAS kent geen regels voor uitgaand verkeer; het plan kan uitgaand "
               "verkeer dus niet afdwingen. Beperken kan alleen in de app zelf of op de router (NIET GEVALIDEERD).")
DOWNLOAD_NL = ("TrueNAS haalt de image pas op bij app.install/app.update (pull_policy 'missing'), dus alleen na "
               "goedkeuring en een ondertekend ExecutionRequest. Het platform beheert geen registry-inloggegevens; een "
               "image die een login vraagt, kan zo niet worden geïnstalleerd.")

Beschikbaar = Callable[[str, str, str], Beschikbaarheid]


@dataclass(frozen=True)
class Doel:
    """TrueNAS- en ix-app-versie waarvoor het plan geldt. De Hub-inventaris levert deze nog niet; ze komen van de
    aanvrager (wizard/Hub) en staan zo in het plan (``bron: aanvrager``). Onbekend → de vertaling blokkeert."""

    truenas_version: str | None = None
    ix_app_version: str | None = None

    def view(self) -> dict[str, Any]:
        return {"truenas_version": self.truenas_version, "ix_app_version": self.ix_app_version, "bron": "aanvrager"}


@dataclass
class _Bouw:
    soort: str
    plan_id: uuid.UUID
    bevindingen: list[dict[str, Any]] = field(default_factory=list)
    blokkades: list[dict[str, Any]] = field(default_factory=list)
    stappen: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ref(self) -> str:
        return self.plan_id.hex[:8]

    def vind(self, findings: Iterable[Finding], bron: str, voorvoegsel: str = "") -> bool:
        """Neemt bevindingen over; ``True`` als er een (niet-geaccepteerde) fout bij zit."""
        fout = False
        for f in findings:
            is_fout = f.severity is Severity.ERROR
            fout = fout or is_fout
            self.bevindingen.append({"code": f.code, "ernst": f.severity.value, "pad": voorvoegsel + f.path,
                                     "melding": f.message, "bron": bron, "geaccepteerd": f.accepted})
            if is_fout:
                self.blokkades.append({"code": f.code, "melding_nl": f.message, "voorstel_nl": None,
                                       "details": {"pad": voorvoegsel + f.path, "bron": bron}})
        return fout

    def blokkeer(self, code: str, melding: str, voorstel: str | None = None, **details: Any) -> None:
        self.blokkades.append({"code": code, "melding_nl": melding, "voorstel_nl": voorstel, "details": details})

    def blokkade(self, blocker: Blocker) -> None:
        self.blokkeer(blocker.code, blocker.message_nl, blocker.proposal_nl, **blocker.details)

    def waarschuw(self, code: str, melding: str, pad: str = "") -> None:
        self.bevindingen.append({"code": code, "ernst": "warning", "pad": pad, "melding": melding, "bron": "planner",
                                 "geaccepteerd": False})

    def stap(self, step_id: str, titel: str, *, actie: str | None = None, target: dict[str, Any] | None = None,
             parameters: dict[str, Any] | None = None, expected_version: str | None = None,
             na: Iterable[str] = (), opruimen: bool = False, herstelt: str | None = None,
             verwacht: dict[str, Any] | None = None, herstelpunt_van: Iterable[str] = (),
             herstelpunt: str | None = None, uitleg: str | None = None) -> str:
        voorstel = None
        if actie is not None:
            voorstel = {
                "idempotency_key": str(uuid.uuid5(self.plan_id, step_id)), "action_id": actie, "target": target or {},
                "parameters": parameters or {}, "expected_version": expected_version,
                "expected_version_bron": "vast" if expected_version else "hub_readback",
                "reason": f"{titel} (plan {self.plan_id}, stap {step_id})",
            }
        self.stappen.append({
            "id": step_id, "titel_nl": titel, "soort": "actie" if actie else "handmatig",
            "hangt_af_van": list(na), "opruimen": opruimen, "herstelt": herstelt,
            "risicoklasse_verwacht": RISICO[actie or "handmatig"], "voorstel": voorstel, "verwacht": verwacht,
            "herstelpunt_van": list(herstelpunt_van), "herstelpunt": herstelpunt, "uitleg_nl": uitleg,
        })
        return step_id


# ------------------------------------------------------------------ bouwstenen
def _lees_manifest(b: _Bouw, manifest_yaml: str) -> tuple[Any, Manifest] | None:
    report, manifest, _raw = validate_text(manifest_yaml)
    b.vind(report.findings, "validator")
    if not report.valid or manifest is None:
        if not any(x["details"].get("bron") == "validator" for x in b.blokkades):
            b.blokkeer("manifest_ongeldig", "Het manifest is niet geldig volgens de manifeststandaard.")
        return None
    return report, manifest


def _manifest_view(report: Any, manifest: Manifest) -> dict[str, Any]:
    return {"app_id": manifest.app_id, "naam": manifest.name, "version": manifest.version,
            "install_method": manifest.install.method, "manifest_sha256": report.manifest_sha256,
            "schema_sha256": report.schema_sha256, "validator_version": report.validator_version}


def _bron_blokkade(b: _Bouw, inventaris: Inventaris) -> None:
    if inventaris.inventory is not None:
        return
    if inventaris.status == "capability_unavailable":
        b.blokkeer("capability_unavailable",
                   "De inventaris van de Hub is voor deze dienst niet beschikbaar (scope inventaris.lezen of de route "
                   "ontbreekt).", "Laat de eigenaar de scope in de Hub toekennen; tot dan wordt niets vrijgegeven.",
                   bron="inventaris", reden=inventaris.reden)
    else:
        b.blokkeer("inventaris_onbekend", "De inventaris van de Hub is niet gemeten of niet bereikbaar.",
                   "Probeer het opnieuw zodra de Hub een verse meting heeft; zonder meting wordt niets vrijgegeven.",
                   bron="inventaris", reden=inventaris.reden, retry_after_s=inventaris.retry_after_s)


def _meet(b: _Bouw, manifest: Manifest, doel: Doel, beschikbaar: Beschikbaar, now: datetime, *, verplicht: bool
          ) -> Beschikbaarheid | None:
    """Vraagt de gemeten beschikbaarheid van de vastgezette catalogusversie (§2.2): voor een catalogusapp de versie uit
    het manifest, voor een nieuwe Custom App de ``ix-app``-versie. ``nee`` → ``version_unavailable``; ``onbekend``
    blokkeert als de versie voor dit plan nodig is (nooit stil een nieuwere versie of ``latest``). Een verouderde
    meting (§6.2) is ``onbekend``, ook als ze ``ja`` zei."""
    if manifest.install.method == "catalog_app" and manifest.catalog is not None:
        train, app, version = manifest.catalog.train, manifest.catalog.app, manifest.catalog.version
    elif doel.ix_app_version:
        train, app, version = "stable", IX_APP, doel.ix_app_version
    else:
        return None
    meting = verse_meting(beschikbaar(train, app, version), now)
    if not verplicht:
        return meting
    if meting.beschikbaar == "nee":
        blocker = catalog_version_blocker(catalog_app=app, train=train, pinned_version=version, available_versions=[])
        if blocker is not None:
            b.blokkeer(blocker.code, blocker.message_nl, blocker.proposal_nl, **blocker.details,
                       gemeten_op=meting.gemeten_op, bron="catalogus")
    elif meting.beschikbaar != "ja":
        code = "capability_unavailable" if meting.status == "capability_unavailable" else "version_availability_unknown"
        b.blokkeer(code, f"Onbekend of versie {version} van '{app}' nog in de trein '{train}' staat "
                         f"({meting.reden or 'niet gemeten'}).",
                   "Wacht op een meting van de Hub (scope catalogus.lezen); zonder meting wordt niets geïnstalleerd en "
                   "nooit stil een nieuwere versie gekozen.", bron="catalogus", reden=meting.reden,
                   gemeten_op=meting.gemeten_op, catalog_app=app, train=train, pinned_version=version)
    return meting


def _vertaal(b: _Bouw, report: Any, manifest: Manifest, parameters: Mapping[str, Any], doel: Doel,
             meting: Beschikbaarheid | None, app_name: str, voorvoegsel: str = "") -> Any:
    target = Target(truenas_version=doel.truenas_version, ix_app_version=doel.ix_app_version,
                    available_versions=meting.available_versions if meting else None)
    res = translate(manifest, dict(parameters), target, app_name=app_name, manifest_sha256=report.manifest_sha256)
    fout = b.vind(res.findings, "vertaling", voorvoegsel)
    if (not res.ready_for_install or res.payload is None) and not fout:
        b.blokkeer("vertaling_niet_gereed", "De vertaling naar formulierwaarden is niet gereed voor installatie.",
                   pad=voorvoegsel)
    return res


def _conflicten(b: _Bouw, manifest: Manifest, payload: Any, inventaris: Inventaris, now: datetime,
                voorvoegsel: str = "") -> None:
    rapport = analyse(manifest, payload, inventaris.inventory, now=now)
    fout = b.vind(rapport.findings, "conflict", voorvoegsel)
    if rapport.status != "ok" and not fout:
        b.blokkeer("inventaris_onbekend", "De conflictanalyse kon niet met een verse inventaris worden gedaan.",
                   "Probeer het opnieuw zodra de Hub een verse meting heeft.", status=rapport.status, pad=voorvoegsel)


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _app_naam(b: _Bouw, manifest: Manifest, app_name: str | None) -> str:
    naam = app_name or manifest.app_id
    if not APP_NAME_RE.fullmatch(naam) or len(naam) > 40:
        b.blokkeer("app_naam_ongeldig", f"'{naam}' is geen geldige TrueNAS-appnaam.")
    return naam


def _install_parameters(report: Any, manifest: Manifest, res: Any) -> dict[str, Any]:
    payload = res.payload.model_dump(mode="json")
    meta: dict[str, Any] = {"app_id": manifest.app_id, "version": manifest.version,
                            "manifest_sha256": report.manifest_sha256}
    if manifest.image is not None:
        meta["image_digest"] = manifest.image.digest
    if manifest.install.method == "catalog_app":
        meta["install_reason"] = manifest.install.reason
    if manifest.network.host_network:
        meta["host_network_reason"] = manifest.network.host_network_reason
    return {"app_create": payload, "manifest": meta, "appstore_preview_hash": res.preview_hash}


def _verwacht(manifest: Manifest, staat: str = "RUNNING") -> dict[str, Any]:
    out: dict[str, Any] = {"staat": staat}
    if manifest.install.method == "custom_app" and manifest.image is not None:
        out["digest"] = {"repository": manifest.image.repository, "expected": manifest.image.digest}
    elif manifest.catalog is not None:
        out["versie"] = manifest.catalog.version
    return out


def _afhankelijkheden(manifest: Manifest, inventaris: Inventaris) -> list[dict[str, Any]]:
    out = []
    for dep in manifest.dependencies:
        status = "onbekend"
        if inventaris.inventory is not None:
            aanwezig = any(dep.app_id in (a.platform_app_id, a.name) for a in inventaris.inventory.apps)
            status = "aanwezig" if aanwezig else "ontbreekt"
        out.append({"app_id": dep.app_id, "soort": dep.kind, "min_version": dep.min_version, "status": status,
                    "reden_nl": dep.reason_nl})
    return out


def _diff(manifest: Manifest, payload: Mapping[str, Any] | None, inventaris: Inventaris,
          huidig: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """De diff voor het scherm 'Diff met vereiste goedkeuring': wat komt erbij of verandert, zonder geheimen."""
    values: Mapping[str, Any] = (payload or {}).get("values") or {}
    image: dict[str, Any] = {}
    if manifest.image is not None:
        image["naar"] = {"repository": manifest.image.repository, "versie": manifest.image.tag,
                         "digest": manifest.image.digest,
                         "formulier_tag": form_tag(manifest.image.tag, manifest.image.digest)}
    if manifest.catalog is not None:
        image["catalogus_naar"] = manifest.catalog.model_dump(mode="json")
    if huidig is not None:
        image["van"] = dict(huidig.get("image") or {})
        image["catalogus_van"] = dict(huidig.get("catalogus") or {})
    opslag = [{"mount": s.get("mount_path"), "type": s.get("type"), "read_only": bool(s.get("read_only")),
               "hostpad": (s.get("host_path_config") or {}).get("path"),
               "dataset_naam": (s.get("ix_volume_config") or {}).get("dataset_name")}
              for s in values.get("storage") or [] if isinstance(s, Mapping)]
    poorten = [{"host_poort": p.get("port_number"), "container_poort": p.get("container_port"),
                "protocol": p.get("protocol")} for p in values.get("ports") or [] if isinstance(p, Mapping)]
    geheimen = sorted({p.name for p in manifest.parameters if p.secret or p.type == "secret_ref"})
    return {
        "image": image,
        "datasets": {"aangekoppeld": opslag,
                     "gedeclareerd": [{"naam": s.name, "mount": s.mount, "soort": s.kind,
                                       "dataset_rol": s.required_dataset_role} for s in manifest.storage],
                     "snapshot_datasets": list(manifest.backup.snapshot_datasets)},
        "privileges": {**manifest.privileges.model_dump(mode="json"), "reden_nl": manifest.privilege_reason,
                       "run_as": values.get("run_as"), "docker_socket": False},
        "poorten": poorten or [{"naam": p.name, "container_poort": p.container_port, "protocol": p.protocol,
                                "host_poort": p.host_port if isinstance(p.host_port, int) else None}
                               for p in manifest.network.ports],
        "netwerk": {"host_network": manifest.network.host_network,
                    "reden_nl": manifest.network.host_network_reason,
                    "uitgaand": {"afdwingbaar": False, "toelichting_nl": UITGAAND_NL}},
        "geheimen": {"parameters": geheimen, "waarden_in_plan": False,
                     "toelichting_nl": "Het plan bevat nooit geheime waarden; de standaard weigert ze in parameters."},
        "afhankelijkheden": _afhankelijkheden(manifest, inventaris),
    }


def _document(b: _Bouw, *, app_id: str | None, app_name: str | None, manifest: dict[str, Any] | None,
              inventaris: Inventaris, meting: Beschikbaarheid | None, doel: Doel | None, diff: dict[str, Any],
              extra: dict[str, Any] | None = None) -> dict[str, Any]:
    if b.blokkades:  # een geblokkeerd plan toont de stappen wel, maar geeft er geen vrij
        for s in b.stappen:
            s["geblokkeerd"] = True
    r2 = [s["id"] for s in b.stappen if s["risicoklasse_verwacht"] in ("R2", "R3")]
    klassen = sorted({s["risicoklasse_verwacht"] for s in b.stappen})
    return {
        "schema": SCHEMA, "plan_id": str(b.plan_id), "soort": b.soort, "app_id": app_id, "app_name": app_name,
        "manifest": manifest,
        "bronnen": {"inventaris": inventaris.view(), "catalogus": meting.view() if meting else None,
                    "doel": doel.view() if doel else None},
        "bevindingen": b.bevindingen, "blokkades": b.blokkades, "diff": diff,
        "goedkeuring": {"vereist": bool(r2), "hoogste_risicoklasse_verwacht": klassen[-1] if klassen else None,
                        "stappen": r2,
                        "toelichting_nl": "De Hub bepaalt de risicoklasse met zijn eigen policy en vraagt goedkeuring "
                                          "per stap, gebonden aan precies deze diff; een risicoclaim telt nooit."},
        "stappen": b.stappen, "image_download": {"toelichting_nl": DOWNLOAD_NL},
        "uitvoering": {"door": "executor via de Hub (contract §8)", "deze_dienst_voert_uit": False},
        **(extra or {}),
    }


# ------------------------------------------------------------------ installeren (B08-003)
def plan_installatie(*, plan_id: uuid.UUID, manifest_yaml: str, parameters: Mapping[str, Any], app_name: str | None,
                     doel: Doel, inventaris: Inventaris, beschikbaar: Beschikbaar, now: datetime) -> dict[str, Any]:
    b = _Bouw("install", plan_id)
    inventaris = verse_inventaris(inventaris, now)
    gelezen = _lees_manifest(b, manifest_yaml)
    _bron_blokkade(b, inventaris)
    if gelezen is None:
        return _document(b, app_id=None, app_name=app_name, manifest=None, inventaris=inventaris, meting=None,
                         doel=doel, diff={})
    report, manifest = gelezen
    naam = _app_naam(b, manifest, app_name)
    if any(x["code"] == "app_naam_ongeldig" for x in b.blokkades):  # met een ongeldige naam valt niets te vertalen
        return _document(b, app_id=manifest.app_id, app_name=None, manifest=_manifest_view(report, manifest),
                         inventaris=inventaris, meting=None, doel=doel, diff=_diff(manifest, None, inventaris))
    meting = _meet(b, manifest, doel, beschikbaar, now, verplicht=True)
    res = _vertaal(b, report, manifest, parameters, doel, meting, naam)
    payload = res.payload.model_dump(mode="json") if res.payload is not None else None
    if res.payload is not None and res.ready_for_install:
        _conflicten(b, manifest, res.payload, inventaris, now)
        b.stap("installeren", f"{manifest.name} installeren als '{naam}'", actie="app.install",
               target={"app_id": naam}, parameters=_install_parameters(report, manifest, res),
               expected_version="absent", verwacht=_verwacht(manifest))
    return _document(b, app_id=manifest.app_id, app_name=naam, manifest=_manifest_view(report, manifest),
                     inventaris=inventaris, meting=meting, doel=doel, diff=_diff(manifest, payload, inventaris),
                     extra={"vastgepind": {"preview_hash": res.preview_hash,
                                           "digest_readback": res.digest_readback.model_dump(mode="json")
                                           if res.digest_readback else None},
                            "controles_na_installatie": [c.model_dump(mode="json")
                                                         for c in manifest.post_install_checks]})


# ------------------------------------------------------------------ bijwerken (B08-004)
def _snapshot_naam(b: _Bouw, versie: str) -> str:
    return f"jlp-{b.ref}-voor-{re.sub(r'[^A-Za-z0-9_.-]', '-', versie)}"[:64]


def _datasets(b: _Bouw, manifest: Manifest, record: Mapping[str, Any]) -> list[tuple[str, str]]:
    """``(opslagnaam, dataset)`` per ``backup.snapshot_datasets``: de dataset komt uit de **gemeten** opslag van de
    geïnstalleerde app (aankoppelpunt uit het manifest ↔ ``opslag[].doel`` in de inventaris), nooit uit een aanname."""
    mounts = {s.name: s.mount for s in manifest.storage}
    gemeten = {str(o.get("doel")): o.get("dataset") for o in record.get("opslag") or [] if isinstance(o, Mapping)}
    out: list[tuple[str, str]] = []
    for naam in manifest.backup.snapshot_datasets:
        dataset = gemeten.get(str(mounts.get(naam)))
        if not dataset:
            b.blokkeer("dataset_onbekend",
                       f"De dataset achter opslag '{naam}' ({mounts.get(naam)}) is niet gemeten in de inventaris.",
                       "Zonder bekende dataset kan er geen herstelpunt worden gemaakt; de update wordt niet "
                       "vrijgegeven.", opslag=naam, mount=mounts.get(naam))
        else:
            out.append((naam, str(dataset)))
    return out


def _schaduw(b: _Bouw, report: Any, manifest: Manifest, parameters: Mapping[str, Any],
             schaduw_parameters: Mapping[str, Any] | None, doel: Doel, meting: Beschikbaarheid | None,
             inventaris: Inventaris, now: datetime, naam: str, snapshots: list[tuple[str, str, str, str]],
             na: list[str]) -> tuple[str | None, list[str], list[str]]:
    """Schaduwstack: kloon van het herstelpunt in de sandbox + een tweede app ``<naam>-test`` met de nieuwe image op
    die kloon. Mislukt de migratie daar, dan komt de productie-update nooit vrij (oude app en data intact)."""
    if schaduw_parameters is None:
        b.blokkeer("schaduwtest_parameters_ontbreken",
                   "Dit manifest meldt databasemigraties (upgrade.migrations); de update vraagt eerst een schaduwtest.",
                   "Geef onder 'schaduw.parameters' de afwijkende waarden voor de testkopie op (minstens een vrije "
                   "hostpoort).")
        return None, [], []
    schaduw_naam = naam + SCHADUW_ACHTERVOEGSEL
    if not APP_NAME_RE.fullmatch(schaduw_naam) or len(schaduw_naam) > 40:
        b.blokkeer("app_naam_ongeldig", f"'{schaduw_naam}' is geen geldige naam voor de testkopie.")
        return None, [], []
    samen = {**parameters, **schaduw_parameters}
    bindingen = {s.name: s for s in (manifest.custom_app.storage if manifest.custom_app else [])}
    klonen: list[str] = []
    kloon_datasets: list[str] = []
    for i, (opslag, dataset, snapshot, snap_stap) in enumerate(snapshots):
        binding = bindingen.get(opslag)
        if binding is None or not isinstance(binding.host_path, ParamRef):
            b.blokkeer("schaduwtest_niet_mogelijk",
                       f"Opslag '{opslag}' is geen hostpad-parameter; de testkopie kan niet naar de kloon wijzen.",
                       opslag=opslag)
            continue
        pool = dataset.split("/", 1)[0]
        kloon = f"{pool}/{SANDBOX}/{naam}-{b.ref}" + (f"-{i}" if len(snapshots) > 1 else "")
        samen[binding.host_path.param] = f"/mnt/{kloon}"  # standaard-aankoppelpunt; NIET GEVALIDEERD op een echte NAS
        kloon_datasets.append(kloon)
        klonen.append(b.stap(f"kloon-{opslag}", f"Herstelpunt van '{opslag}' klonen naar de sandbox",
                             actie="zfs.snapshot.clone_to_sandbox", target={"snapshot": f"{dataset}@{snapshot}"},
                             parameters={"doel_dataset": kloon}, expected_version=f"snapshot:{dataset}@{snapshot}",
                             na=[snap_stap]))
    if any(x["code"] == "schaduwtest_niet_mogelijk" for x in b.blokkades):
        return None, kloon_datasets, klonen
    res = _vertaal(b, report, manifest, samen, doel, meting, schaduw_naam, "schaduw.")
    if res.payload is None or not res.ready_for_install:
        return None, kloon_datasets, klonen
    _conflicten(b, manifest, res.payload, inventaris, now, "schaduw.")
    stap = b.stap("schaduw-installeren", f"Testkopie '{schaduw_naam}' met de nieuwe versie op de kloon starten",
                  actie="app.install", target={"app_id": schaduw_naam},
                  parameters=_install_parameters(report, manifest, res), expected_version="absent",
                  na=[*na, *klonen], verwacht=_verwacht(manifest),
                  uitleg="Migratietest op een kloon van de data: start de nieuwe versie niet gezond, dan stopt het "
                         "plan hier en blijft de bestaande app ongewijzigd.")
    return stap, kloon_datasets, klonen


def plan_update(*, plan_id: uuid.UUID, manifest_yaml: str, parameters: Mapping[str, Any], app_name: str | None,
                doel: Doel, schaduw_parameters: Mapping[str, Any] | None, inventaris: Inventaris,
                beschikbaar: Beschikbaar, now: datetime) -> dict[str, Any]:
    b = _Bouw("update", plan_id)
    inventaris = verse_inventaris(inventaris, now)
    gelezen = _lees_manifest(b, manifest_yaml)
    _bron_blokkade(b, inventaris)
    if gelezen is None:
        return _document(b, app_id=None, app_name=app_name, manifest=None, inventaris=inventaris, meting=None,
                         doel=doel, diff={})
    report, manifest = gelezen
    naam = _app_naam(b, manifest, app_name)
    custom = manifest.install.method == "custom_app"
    migraties = bool(manifest.upgrade.migrations)
    # §2.2: voor app.update van een geïnstalleerde Custom App speelt de ix-app-versie niet; wel voor de testkopie
    # (een nieuwe app) en voor de catalogusversie van een catalogusapp.
    meting = _meet(b, manifest, doel, beschikbaar, now, verplicht=(not custom) or migraties)
    record = inventaris.records.get(naam) if inventaris.inventory is not None else None
    extra: dict[str, Any] = {"vastgepind": None}
    if inventaris.inventory is not None and record is None:
        b.blokkeer("app_niet_geinstalleerd", f"App '{naam}' staat niet in de gemeten inventaris.",
                   "Bijwerken kan alleen voor een geïnstalleerde app; gebruik een installatieplan.", app_name=naam)
    if record is None:
        return _document(b, app_id=manifest.app_id, app_name=naam, manifest=_manifest_view(report, manifest),
                         inventaris=inventaris, meting=meting, doel=doel, diff=_diff(manifest, None, inventaris),
                         extra=extra)
    wijze = record.get("installatiewijze")
    if wijze != manifest.install.method:
        b.blokkeer("installatiewijze_verschilt",
                   f"De app is geïnstalleerd als '{wijze}', het manifest beschrijft '{manifest.install.method}'.",
                   "Een compose-app of een andere installatiewijze wordt nooit door dit plan bijgewerkt.")
    huidig_image: Mapping[str, Any] = _map(record.get("image"))
    if custom and manifest.image is not None:
        if huidig_image.get("repository") != manifest.image.repository:
            b.blokkeer("image_repository_verschilt",
                       "De geïnstalleerde app gebruikt een andere image-repository dan het manifest.",
                       "app.update wijzigt alleen tag en digest; een andere repository vraagt een nieuwe installatie.",
                       geinstalleerd=huidig_image.get("repository"), manifest=manifest.image.repository)
        if huidig_image.get("digest") == manifest.image.digest:
            b.waarschuw("al_op_deze_versie", "De geïnstalleerde digest is al gelijk aan die van het manifest.", "image")
        elif not DIGEST_RE.fullmatch(str(huidig_image.get("digest") or "")):
            b.waarschuw("huidige_digest_onbekend", "De digest van de geïnstalleerde versie is niet gemeten. Een later "
                        "terugzetplan kan dan niet controleren of precies die versie terug is.", "image")
        b.waarschuw("alleen_image_wijzigt", "app.update wijzigt alleen de image (tag + digest); poorten, opslag en "
                    "overige formulierwaarden blijven zoals ze geïnstalleerd zijn.", "custom_app")

    # 1. herstelpunt: export/stoppen volgens het manifest, daarna een snapshot per dataset
    methode = manifest.backup.db_export.method
    datasets = _datasets(b, manifest, record)
    snapshot = _snapshot_naam(b, manifest.version)
    voor: list[str] = []
    gestopt: str | None = None
    if methode == "stopped_snapshot":
        gestopt = b.stap("stoppen", f"'{naam}' stoppen voor een consistente snapshot", actie="app.stop",
                         target={"app_id": naam}, verwacht={"staat": "STOPPED"})
        voor = [gestopt]
    elif methode != "none":
        voor = [b.stap("export", f"Database-export laten maken ({methode})", uitleg=(
            f"{manifest.backup.db_export.description_nl} Het platform heeft geen shell in de container: de eigenaar "
            "of de app maakt de export en meldt het resultaat."))]
    snaps: list[tuple[str, str, str, str]] = []
    for opslag, dataset in datasets:
        stap = b.stap(f"snapshot-{opslag}", f"Snapshot van '{opslag}' vóór de update", actie="zfs.snapshot.create",
                      target={"dataset": dataset}, parameters={"naam": snapshot},
                      expected_version=f"dataset:{dataset}", na=voor)
        snaps.append((opslag, dataset, snapshot, stap))
    snap_stappen = [s[3] for s in snaps]
    na = list(snap_stappen)
    if gestopt is not None:
        na.append(b.stap("starten", f"'{naam}' weer starten na de snapshot", actie="app.start",
                         target={"app_id": naam}, na=snap_stappen or [gestopt], opruimen=True, herstelt=gestopt,
                         verwacht={"staat": "RUNNING"},
                         uitleg="Wordt ook vrijgegeven als de snapshot mislukt: de app blijft nooit gestopt achter."))
    if not snaps and not any(x["code"] == "dataset_onbekend" for x in b.blokkades):
        b.blokkeer("herstelpunt_ontbreekt", "Het manifest noemt geen dataset voor een snapshot vóór de update.",
                   "app.update vraagt een herstelpunt (backup.snapshot_datasets); zonder herstelpunt geen update.")

    # 2. schaduwtest bij migraties
    schaduw: str | None = None
    klonen: list[str] = []
    kloon_stappen: list[str] = []
    if migraties and custom and snaps:
        schaduw, klonen, kloon_stappen = _schaduw(b, report, manifest, parameters, schaduw_parameters, doel, meting,
                                                  inventaris, now, naam, snaps, na)
    elif migraties and not custom:
        b.waarschuw("schaduwtest_niet_beschikbaar", "Voor een catalogusapp is geen schaduwtest beschikbaar; het "
                    "herstelpunt en app.rollback zijn de enige terugweg.", "upgrade.migrations")

    # 3. de update zelf, met het herstelpunt als backup-checkpoint
    if custom and manifest.image is not None:
        params: dict[str, Any] = {"image": {"repository": manifest.image.repository,
                                            "tag": form_tag(manifest.image.tag, manifest.image.digest)},
                                  "manifest_sha256": report.manifest_sha256}
    else:
        params = {"catalog_version": manifest.catalog.version if manifest.catalog else ""}
    bijwerken = b.stap("bijwerken", f"'{naam}' bijwerken naar {manifest.version}", actie="app.update",
                       target={"app_id": naam}, parameters=params, na=[*na, *([schaduw] if schaduw else [])],
                       verwacht=_verwacht(manifest), herstelpunt_van=snap_stappen,
                       herstelpunt=snapshot if snaps else None,
                       uitleg="Het herstelpunt (snapshot) gaat als backup_checkpoint_id mee in de goedkeuring. Mislukt "
                              "de readback, maak dan een terugzetplan met dit plan als bron.")
    if schaduw is not None:
        weg = b.stap("schaduw-verwijderen", f"Testkopie '{naam}{SCHADUW_ACHTERVOEGSEL}' verwijderen",
                     actie="app.uninstall", target={"app_id": naam + SCHADUW_ACHTERVOEGSEL}, na=[schaduw, bijwerken],
                     opruimen=True, herstelt=schaduw, verwacht={"staat": "ABSENT"})
        b.stap("kloon-opruimen", "Sandboxkloon laten verwijderen door de eigenaar", na=[weg], opruimen=True,
               herstelt=kloon_stappen[0] if kloon_stappen else schaduw,
               uitleg="Het platform verwijdert nooit zelf datasets. Verwijder na controle: " + ", ".join(klonen) + ".")
    extra["vastgepind"] = {"snapshot_naam": snapshot, "sandbox_klonen": klonen,
                           "herstelpunten": [f"{d}@{s}" for _o, d, s, _i in snaps]}
    extra["eigenaar_herstel"] = {
        "toelichting_nl": "Data terugzetten uit een snapshot (R3) doet alleen de eigenaar; het platform doet dat nooit "
                          "automatisch en verwijdert nooit datasets of media.",
        "tag_only_rollback_safe": manifest.upgrade.tag_only_rollback_safe, "notities_nl": manifest.upgrade.notes_nl}
    return _document(b, app_id=manifest.app_id, app_name=naam, manifest=_manifest_view(report, manifest),
                     inventaris=inventaris, meting=meting, doel=doel,
                     diff=_diff(manifest, None, inventaris, record), extra=extra)


# ------------------------------------------------------------------ terugzetten (B08-004)
def plan_rollback(*, plan_id: uuid.UUID, app_name: str | None, herstel_van: str | None,
                  catalog: Mapping[str, str] | None, update_plan: Mapping[str, Any] | None,
                  update_stappen: Iterable[Mapping[str, Any]] | None, inventaris: Inventaris,
                  beschikbaar: Beschikbaar, now: datetime) -> dict[str, Any]:
    """Terugzetten. Custom App: ``app.rollback {herstel_van}`` = ``app.update`` met de vorige waarden die de executor
    vóór de update bewaarde. Catalogusapp: ``{catalog_version}``, alleen als die versie gemeten nog in de catalogus
    staat (anders ``version_unavailable``). Data herstellen blijft een stap van de eigenaar (R3)."""
    b = _Bouw("rollback", plan_id)
    inventaris = verse_inventaris(inventaris, now)
    _bron_blokkade(b, inventaris)
    naam = app_name
    verwacht: dict[str, Any] = {"staat": "RUNNING"}
    bron: dict[str, Any] = {}
    meting: Beschikbaarheid | None = None
    eigenaar = None
    herstelpunt: str | None = None
    if update_plan is not None:
        naam = str(update_plan.get("app_name") or "")
        bron = {"update_plan_id": update_plan.get("plan_id")}
        stap = next((s for s in update_stappen or [] if s.get("id") == "bijwerken"), None)
        herstel_van = str(stap["execution_id"]) if stap and stap.get("execution_id") else None
        if update_plan.get("soort") != "update":
            b.blokkeer("bronplan_ongeldig", "Het opgegeven plan is geen updateplan.")
        elif herstel_van is None:
            b.blokkeer("niets_om_terug_te_zetten", "De update uit dit plan is niet uitgevoerd; de bestaande app is "
                       "niet gewijzigd.", "Er is niets terug te zetten.")
        image = _map(_map(update_plan.get("diff")).get("image"))
        van, naar = _map(image.get("van")), _map(image.get("naar"))
        if van.get("repository") and DIGEST_RE.fullmatch(str(van.get("digest") or "")):
            verwacht["digest"] = {"repository": van["repository"], "expected": van["digest"]}
        elif naar.get("repository") and DIGEST_RE.fullmatch(str(naar.get("digest") or "")):
            # De vorige digest is niet gemeten: dan minstens niet meer de digest waar de update naartoe ging.
            verwacht["digest_niet"] = {"repository": naar["repository"], "digest": naar["digest"]}
        vast = update_plan.get("vastgepind") or {}
        herstelpunt = str(vast["snapshot_naam"]) if vast.get("herstelpunten") and vast.get("snapshot_naam") else None
        eigenaar = {**(update_plan.get("eigenaar_herstel") or {}), "herstelpunten": vast.get("herstelpunten") or []}
    if not naam or not APP_NAME_RE.fullmatch(naam):
        b.blokkeer("app_naam_ongeldig", "Een geldige TrueNAS-appnaam is verplicht.")
        naam = naam or None
    record = inventaris.records.get(naam) if naam and inventaris.inventory is not None else None
    if naam and inventaris.inventory is not None and record is None:
        b.blokkeer("app_niet_geinstalleerd", f"App '{naam}' staat niet in de gemeten inventaris.", app_name=naam)
    params: dict[str, Any] | None = None
    if catalog is not None and update_plan is None:
        train, app, version = str(catalog.get("train")), str(catalog.get("app")), str(catalog.get("version"))
        meting = verse_meting(beschikbaar(train, app, version), now)
        if record is not None and (record.get("catalogus") or {}).get("app") != app:
            b.blokkeer("catalogusapp_verschilt", "De geïnstalleerde app is niet deze catalogusapp.", catalog_app=app)
        if meting.beschikbaar == "nee":
            blocker = catalog_version_blocker(catalog_app=app, train=train, pinned_version=version,
                                              available_versions=[])
            if blocker is not None:
                b.blokkade(blocker)
        elif meting.beschikbaar != "ja":
            b.blokkeer("capability_unavailable" if meting.status == "capability_unavailable"
                       else "version_availability_unknown",
                       f"Onbekend of versie {version} van '{app}' nog in de catalogus staat.",
                       "Zonder verse meting wordt niet teruggezet naar een catalogusversie.", reden=meting.reden,
                       gemeten_op=meting.gemeten_op)
        params, verwacht["versie"] = {"catalog_version": version}, version
    elif herstel_van is not None:
        if not UUID_RE.fullmatch(herstel_van):
            b.blokkeer("herstel_van_ongeldig", "herstel_van moet het UUID van een eerdere uitvoering zijn.")
        if record is not None and record.get("installatiewijze") != "custom_app":
            b.blokkeer("installatiewijze_verschilt", "Terugzetten met vorige waarden kan alleen voor een Custom App.")
        nu_image = _map(record.get("image")) if record is not None else {}
        if update_plan is None and nu_image.get("repository") \
                and DIGEST_RE.fullmatch(str(nu_image.get("digest") or "")):
            # Zonder updateplan kent het plan de vorige digest niet: dan minstens niet meer de digest van nu.
            verwacht["digest_niet"] = {"repository": nu_image["repository"], "digest": nu_image["digest"]}
        params = {"herstel_van": herstel_van}
    elif update_plan is None:
        b.blokkeer("bron_ontbreekt", "Geef een updateplan, herstel_van of een catalogusversie op.")
    if params is not None and naam:
        if herstelpunt is None:
            b.waarschuw("herstelpunt_onbekend", "Dit plan kent geen herstelpunt. De Hub eist voor app.rollback een "
                        "backup_checkpoint_id in de goedkeuring; zonder herstelpunt weigert de executor.",
                        "herstelpunt")
        if "digest" not in verwacht and "versie" not in verwacht:
            b.waarschuw("vorige_digest_onbekend", (
                "De digest van de vorige versie is niet gemeten. Na het terugzetten controleert het plan dat de app "
                "draait en dat de digest van vóór het terugzetten er niet meer staat; welke versie er dan draait, is "
                "daarmee niet bewezen." if "digest_niet" in verwacht else
                "Het plan kent geen digest van de vorige of de huidige versie. Na het terugzetten kan alleen worden "
                "gecontroleerd dat de app draait; dat de vorige versie terug is, is daarmee niet bewezen."),
                "terugzetten")
        b.stap("terugzetten", f"'{naam}' terugzetten naar de vorige versie", actie="app.rollback",
               target={"app_id": naam}, parameters=params, verwacht=verwacht, herstelpunt=herstelpunt,
               uitleg="Custom App: de executor zet de vóór de update bewaarde formulierwaarden (tag + digest) terug. "
                      "Het herstelpunt van vóór de update blijft staan.")
    huidig = {"image": {"van": dict((record or {}).get("image") or {})}} if record else {}
    return _document(b, app_id=(record or {}).get("app_id") if record else None, app_name=naam, manifest=None,
                     inventaris=inventaris, meting=meting, doel=None, diff=huidig,
                     extra={"bron": bron, "eigenaar_herstel": eigenaar or {
                         "toelichting_nl": "Data terugzetten uit een snapshot (R3) doet alleen de eigenaar."}})


# ------------------------------------------------------------------ stappen: vrijgeven en resultaten
class ResultaatFout(Exception):
    def __init__(self, status: int, code: str, melding: str) -> None:
        super().__init__(melding)
        self.status, self.code, self.melding = status, code, melding


def begin(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    geblokkeerd = bool(document.get("blokkades"))
    staat = [{"id": s["id"], "status": "geblokkeerd" if geblokkeerd else "wacht", "execution_id": None,
              "readback": None, "reden": "plan_geblokkeerd" if geblokkeerd else None, "gemeld_op": None}
             for s in document.get("stappen") or []]
    return staat if geblokkeerd else doorrekenen(document, staat)


def doorrekenen(document: Mapping[str, Any], staat: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Geeft stappen vrij waarvan alle voorgangers ``voltooid`` zijn; blokkeert wat op een mislukte stap wacht.
    Opruim- en herstelstappen komen vrij zodra hun voorgangers klaar zijn (hoe dan ook), maar alleen als de stap die ze
    ongedaan maken echt is uitgevoerd."""
    by = {str(s["id"]): dict(s) for s in staat}
    stappen = list(document.get("stappen") or [])
    gewijzigd = True
    while gewijzigd:
        gewijzigd = False
        for stap in stappen:
            cur = by[stap["id"]]
            if cur["status"] != "wacht":
                continue
            deps = [by[d]["status"] for d in stap["hangt_af_van"]]
            if stap.get("opruimen"):
                if all(d in TERMINAAL for d in deps):
                    bron = by[stap["herstelt"]]["status"] if stap.get("herstelt") else "voltooid"
                    cur["status"] = "vrijgegeven" if bron in ("voltooid", "mislukt") else "overgeslagen"
                    gewijzigd = True
            elif any(d in ("mislukt", "geblokkeerd", "overgeslagen") for d in deps):
                cur["status"], cur["reden"], gewijzigd = "geblokkeerd", "voorgaande_stap_niet_voltooid", True
            elif all(d == "voltooid" for d in deps):
                cur["status"], gewijzigd = "vrijgegeven", True
    return [by[s["id"]] for s in stappen]


def plan_status(document: Mapping[str, Any], staat: Iterable[Mapping[str, Any]]) -> str:
    if document.get("blokkades"):
        return "geblokkeerd"
    rijen = list(staat)
    statussen = [s["status"] for s in rijen]
    if all(s in TERMINAAL for s in statussen):
        return "mislukt" if any(s in ("mislukt", "geblokkeerd") for s in statussen) else "voltooid"
    return "in_uitvoering" if any(s.get("gemeld_op") for s in rijen) else "klaar_voor_voorstel"


def schoon_readback(readback: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Alleen de velden die het plan vergelijkt; nooit vrije configuratie (die kan geheimen bevatten)."""
    if readback is None:
        return None
    return {"staat": str(readback.get("staat") or "")[:32],
            "versie": str(readback["versie"])[:128] if readback.get("versie") else None,
            "digests": [str(d)[:400] for d in (readback.get("digests") or [])[:20]],
            "images": [str(i)[:400] for i in (readback.get("images") or [])[:20]],
            "etag": str(readback["etag"])[:80] if readback.get("etag") else None}


def _afwijking(verwacht: Mapping[str, Any], readback: Mapping[str, Any]) -> str | None:
    if verwacht.get("staat") and readback.get("staat") != verwacht["staat"]:
        return f"toestand is {readback.get('staat') or 'onbekend'}, verwacht {verwacht['staat']}"
    if verwacht.get("versie") and readback.get("versie") != verwacht["versie"]:
        return f"versie is {readback.get('versie') or 'onbekend'}, verwacht {verwacht['versie']}"
    digest = verwacht.get("digest")
    if isinstance(digest, Mapping):
        try:
            terug = check_digest_readback(repository=str(digest["repository"]), expected_digest=str(digest["expected"]),
                                          image_rows=[{"repo_digests": readback.get("digests") or []}])
        except RecipeError as exc:
            return str(exc)
        if not terug.ok:
            return f"digest wijkt af of ontbreekt (verwacht {digest['repository']}@{digest['expected']})"
    niet = verwacht.get("digest_niet")
    if isinstance(niet, Mapping):
        gezien = {str(d) for d in readback.get("digests") or [] if str(d).startswith(f"{niet['repository']}@")}
        if not gezien:
            return f"digest ontbreekt in de readback (van {niet['repository']})"
        if f"{niet['repository']}@{niet['digest']}" in gezien:
            return f"digest is nog {niet['digest']}: de vorige versie is niet teruggezet"
    return None


def meld(document: Mapping[str, Any], staat: Iterable[Mapping[str, Any]], step_id: str, *, status: str,
         execution_id: str | None, readback: Mapping[str, Any] | None, reden: str | None, gemeld_op: str
         ) -> list[dict[str, Any]]:
    """Verwerkt het resultaat van één vrijgegeven stap. ``voltooid`` geldt alleen met een readback die klopt met het
    plan; een afwijking maakt de stap ``mislukt`` (``readback_afwijking``) en houdt alles wat erop wacht tegen."""
    stap = next((s for s in document.get("stappen") or [] if s["id"] == step_id), None)
    rijen = [dict(s) for s in staat]
    cur = next((s for s in rijen if s["id"] == step_id), None)
    if stap is None or cur is None:
        raise ResultaatFout(404, "not_found", "Deze stap bestaat niet in het plan.")
    if cur["status"] != "vrijgegeven":
        raise ResultaatFout(409, "stap_niet_vrijgegeven",
                            f"Stap '{step_id}' is niet vrijgegeven (status: {cur['status']}).")
    schoon = schoon_readback(readback)
    if status == "voltooid":
        if stap["soort"] == "actie" and not execution_id:
            raise ResultaatFout(422, "execution_id_ontbreekt", "Een uitgevoerde actie meldt het execution_id.")
        verwacht = stap.get("verwacht")
        if verwacht:
            if schoon is None:
                raise ResultaatFout(422, "readback_ontbreekt", "Deze stap is pas voltooid met een readback.")
            afwijking = _afwijking(verwacht, schoon)
            if afwijking:
                status, reden = "mislukt", f"readback_afwijking: {afwijking}"
    cur.update(status=status, execution_id=execution_id, readback=schoon, reden=reden, gemeld_op=gemeld_op)
    return doorrekenen(document, rijen)


__all__ = ["SCHEMA", "Doel", "ResultaatFout", "begin", "doorrekenen", "meld", "plan_installatie", "plan_rollback",
           "plan_status", "plan_update", "schoon_readback", "split_form_tag"]
