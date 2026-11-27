"""B08-006. Maak een overdraagbare herstelkaart uit de broninventaris en door de Hub bevestigd exportbewijs."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from appstore.manifest import validate_text
from appstore.manifest.model import Manifest
from appstore.manifest.rules import is_secret_name
from appstore.manifest.translate import is_secret
from lifecycle.custom_app import DIGEST_RE
from lifecycle.hubread import Inventaris, geldig_tot, verse_inventaris
from lifecycle.verwijderen import Bron, GemetenOpslag, Herstelpunt, Som, beschermregel, toets_herstelpunt

Grootte = Annotated[int, Field(strict=True, ge=0, le=2**63 - 1)]
SOORTEN: dict[str, Literal["configuratie", "data", "cache", "media"]] = {
    "config": "configuratie", "data": "data", "scratch": "cache", "media": "media"
}
STAPPEN = (
    "Open de export in de Hub. Controleer het herstelpunt en de controlesommen van manifest en formulierwaarden.",
    "Controleer in de Hub of de app-ID op de doelhost vrij is. Kies in de TrueNAS-UI per datasetrol een leeg "
    "doelpad met voldoende vrije ruimte. "
    "Herstel de export in een geïsoleerde testinstallatie op die datasets. Houd de bron-app beschikbaar.",
    "Open het bijbehorende appformulier op de doelhost. Gebruik dezelfde app-ID, manifestversie en images, "
    "koppel de herstelde rollen en vul de aangegeven instellingen en geheimen opnieuw in.",
    "Controleer de image-digests en vergelijk de gegevens op beide hosts. Zijn er sinds de export wijzigingen, "
    "herstel dan eerst een actuele export. Bevestig het herstel zelf in de Hub zodra de gegevens overeenkomen.",
    "Pas na die bevestiging kun je in de Hub apart een verwijderpreview voor de bron-app aanvragen.",
)


class Datasetmeting(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bron: Bron
    dataset: Bron
    grootte_bytes: Grootte


class Verhuisexport(BaseModel):
    """Gegevens uit het geverifieerde archief. De Hub levert alleen openbare parameterwaarden en gemeten groottes."""

    model_config = ConfigDict(extra="forbid", strict=True)
    manifest_yaml: str = Field(min_length=1, max_length=200_000)
    parameters: dict[str, bool | int | str] = Field(default_factory=dict, max_length=256)
    datasetmetingen: list[Datasetmeting] = Field(max_length=1024)


class Datasetrol(BaseModel):
    rol: str
    soort: Literal["configuratie", "data", "cache", "media"]
    grootte_bytes: Grootte


class Manifestpin(BaseModel):
    app_id: str
    versie: str
    manifest_sha256: Som
    image_digests: list[Som]


class Geheimveld(BaseModel):
    naam: str
    invoeren_in: str = "Kluis van de Hub op de doelhost"


class Herstelkaart(BaseModel):
    bron_app: str
    checkpoint_id: UUID
    export_id: UUID
    waarden_sha256: Som
    geldig_tot: datetime
    bron: str = "De oorspronkelijke app en datasets blijven in gebruik."
    doel: str = "Afzonderlijke datasets op de doelhost, bij herstel gekozen per rol."
    terugweg: str = "Blijf bij een ontbrekende bevestiging of afwijking de bron-app gebruiken."


class Verhuisplan(BaseModel):
    soort: Literal["move"] = "move"
    status: Literal["beschikbaar", "geblokkeerd", "laten_staan"]
    app_id: str | None = None
    reden: str | None = None
    melding_nl: str | None = None
    manifest: Manifestpin | None = None
    waarden: dict[str, bool | int | str] = Field(default_factory=dict)
    opnieuw_kiezen: list[str] = Field(default_factory=list)
    geheimen: list[Geheimveld] = Field(default_factory=list)
    datasets: list[Datasetrol] = Field(default_factory=list)
    herstelkaart: Herstelkaart | None = None
    stappen: list[str] = Field(default_factory=list, description="Handelingen van de gebruiker, in deze volgorde.")


def _datasetrollen(manifest: Manifest, opslag: GemetenOpslag, punt: Herstelpunt,
                  metingen: list[Datasetmeting]) -> list[Datasetrol] | None:
    """Verbind intern op mount én bron. Alleen een eenduidige, volledig gedekte set rollen verlaat de planner."""
    declaraties = {d.mount: d for d in manifest.storage}
    per_bron = {m.bron: m for m in metingen}
    if (len(per_bron) != len(metingen) or len(declaraties) != len(manifest.storage)
            or len(opslag.opslag) != len(declaraties) or {k.doel for k in opslag.opslag} != set(declaraties)
            or set(per_bron) != {k.bron for k in opslag.opslag}
            or len({m.dataset for m in metingen}) != len(opslag.opslag)
            or {m.dataset for m in metingen} != set(punt.datasets)):
        return None
    rollen: dict[str, Datasetrol] = {}
    for k in opslag.opslag:
        decl, meting = declaraties[k.doel], per_bron[k.bron]
        rol = decl.required_dataset_role or decl.name
        if k.type != "bind" or (k.dataset is not None and k.dataset != meting.dataset) or rol in rollen:
            return None
        rollen[rol] = Datasetrol(rol=rol, soort=SOORTEN[decl.kind], grootte_bytes=meting.grootte_bytes)
    return [rollen[r] for r in sorted(rollen)]


def _overdraagbaar(soort: str, waarde: bool | int | str) -> bool:
    if soort == "boolean":
        return isinstance(waarde, bool)
    if type(waarde) is int:
        return soort == "integer" or (soort == "port" and 1 <= waarde <= 65535)
    if not isinstance(waarde, str) or len(waarde) > 128:
        return False
    if soort == "timezone" and re.fullmatch(r"UTC|[A-Za-z_]+(/[A-Za-z0-9_+-]+){1,2}", waarde):
        try:
            ZoneInfo(waarde)
            return True
        except ZoneInfoNotFoundError:
            return False
    if soort == "duration":
        return bool(re.fullmatch(r"P(?=\d|T\d)(\d+D)?(T(?=\d)(\d+H)?(\d+M)?(\d+S)?)?", waarde))
    return soort == "gpu" and waarde in ("none", "intel_render")


def _waarden(plan: Verhuisplan, manifest: Manifest, parameters: dict[str, bool | int | str]) -> None:
    """Alleen begrensde openbare typen. Vrije tekst, adressen en paden worden op het doel opnieuw gekozen."""
    for p in manifest.parameters:
        waarde = parameters.get(p.name)
        if is_secret(p) or is_secret_name(p.name):
            plan.geheimen.append(Geheimveld(naam=p.name))
        elif waarde is not None and _overdraagbaar(p.type, waarde):
            plan.waarden[p.name] = waarde
        else:
            plan.opnieuw_kiezen.append(p.name)


def _pinnen(manifest: Manifest, record: dict[str, Any]) -> list[str] | None:
    images = record.get("images")
    if not isinstance(images, list) or not images or not all(
            isinstance(im, dict) and isinstance(im.get("repository"), str) and im["repository"]
            and DIGEST_RE.fullmatch(str(im.get("digest") or ""))
            for im in images):
        return None
    if manifest.install.method != record.get("installatiewijze"):
        return None
    if manifest.image and not any(im["repository"] == manifest.image.repository
                                  and im["digest"] == manifest.image.digest for im in images):
        return None
    if manifest.catalog:
        cat = record.get("catalogus")
        if not isinstance(cat, dict) or (cat.get("app"), cat.get("trein"), cat.get("versie")) != (
                manifest.catalog.app, manifest.catalog.train, manifest.catalog.version):
            return None
    return sorted({im["digest"] for im in images})


def plan_verhuizen(app_name: str, inventaris: Inventaris, herstelpunt: Herstelpunt | None,
                   export: Verhuisexport | None, now: datetime) -> Verhuisplan:
    inventaris = verse_inventaris(inventaris, now)
    record = inventaris.records.get(app_name, {})
    regel = beschermregel(app_name, record)
    if regel:
        return Verhuisplan(status="laten_staan", app_id=regel.app, reden=regel.code,
                           melding_nl="Laat deze beschermde app op de bronhost staan.")

    def blokkeer(code: str, uitleg: str) -> Verhuisplan:
        return Verhuisplan(status="geblokkeerd", reden=code, melding_nl=uitleg)

    if inventaris.status != "ok" or inventaris.inventory is None:
        return blokkeer("inventaris_onbekend", "De Hub heeft geen verse inventaris van de bron-app.")
    if record.get("aanwezig") is not True or record.get("runtime_id") != app_name:
        return blokkeer("app_niet_geinstalleerd", "De bron-app is niet als aanwezig gemeten.")
    if herstelpunt is None:
        return blokkeer("herstelpunt_ontbreekt", "De Hub heeft nog geen herstelpunt voor deze app bevestigd.")
    try:
        opslag = GemetenOpslag.model_validate(record)
    except ValidationError:
        return blokkeer("opslag_onbekend", "De opslagmeting is onvolledig.")
    fout = toets_herstelpunt(herstelpunt, app_name, record, opslag, now)
    if fout:
        return blokkeer(*fout)
    archief = herstelpunt.archief
    if export is None or archief is None or herstelpunt.rpo_uren is None:
        return blokkeer("export_ontbreekt", "De geverifieerde exportgegevens ontbreken.")
    rapport, manifest, _ = validate_text(export.manifest_yaml)
    if (not rapport.valid or not manifest or rapport.manifest_sha256 != archief.manifest_sha256
            or record.get("app_id") not in (None, manifest.app_id)
            or re.search(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)", manifest.version)):
        return blokkeer("manifest_onbruikbaar", "Het exportmanifest heeft geen bruikbare pin voor deze app.")
    regel = beschermregel(manifest.app_id, {}) if manifest.app_id not in (app_name, record.get("app_id")) else None
    if regel:
        return Verhuisplan(status="laten_staan", app_id=regel.app, reden=regel.code,
                           melding_nl="Laat deze beschermde app op de bronhost staan.")
    digests = _pinnen(manifest, record)
    if not digests:
        return blokkeer("release_onbekend", "De gemeten installatie en de gepinde release zijn niet bevestigd.")
    rollen = _datasetrollen(manifest, opslag, herstelpunt, export.datasetmetingen)
    if rollen is None:
        return blokkeer("datasetrollen_onbekend", "Niet elke dataset heeft een eenduidige rol en gemeten grootte.")
    grens = herstelpunt.gemaakt_op + timedelta(hours=herstelpunt.rpo_uren)
    grens = min(grens, geldig_tot(inventaris.gemeten_op) or now)
    plan = Verhuisplan(status="beschikbaar", app_id=manifest.app_id, datasets=rollen, stappen=list(STAPPEN),
                       manifest=Manifestpin(app_id=manifest.app_id, versie=manifest.version,
                                            manifest_sha256=archief.manifest_sha256, image_digests=digests),
                       herstelkaart=Herstelkaart(bron_app=app_name, checkpoint_id=herstelpunt.checkpoint_id,
                                                export_id=archief.export_id,
                                                waarden_sha256=archief.waarden_sha256,
                                                geldig_tot=grens.astimezone(UTC)))
    _waarden(plan, manifest, export.parameters)
    return plan
