"""Gemeten opslag en het door de Hub bevestigde B12-herstelbewijs voor een verwijderpreview."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from lifecycle import beschermd
from lifecycle.errors import ApiError

Som = Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]
Bron = Annotated[str, Field(min_length=1, max_length=4096)]


class Herstelarchief(BaseModel):
    """Onveranderlijke verwijzing naar het geverifieerde archief met het vorige manifest en de formulierwaarden."""

    model_config = ConfigDict(extra="forbid")
    export_id: UUID
    manifest_sha256: Som
    waarden_sha256: Som


class Herstelpunt(BaseModel):
    """De Hub verbindt het herstelpunt, de D16-grens en het herstelarchief aan dezelfde gemeten app."""

    model_config = ConfigDict(extra="forbid")
    checkpoint_id: UUID
    app: str = Field(min_length=1, max_length=40)
    inventaris_id: UUID
    versie_etag: int = Field(strict=True, gt=0)
    resource_etag: Som
    gemaakt_op: AwareDatetime
    status: Literal["geverifieerd", "niet_geverifieerd", "ongeldig"]
    rpo_uren: float | None = Field(default=None, strict=True, gt=0, le=24 * 365)
    datasets: list[Bron] = Field(max_length=1024)
    opslagbronnen: list[Bron] = Field(max_length=1024, description="Alle gedekte bronnen uit de appconfiguratie.")
    archief: Herstelarchief | None = None


class Koppeling(BaseModel):
    """Publieke velden uit de Hub-inventaris. Een onbekende dataset verbergt het gemeten hostpad niet."""

    soort: Literal["hostpad", "ixvolume", "overig"]
    type: str
    bron: Bron
    doel: Bron
    dataset: Bron | None


class GemetenOpslag(BaseModel):
    opslag: list[Koppeling]
    datasets: list[Bron]

    def behoudbaar(self) -> bool:
        # app.delete ruimt Docker named volumes altijd op. Alleen gemeten bindmounts blijven behouden.
        return all(k.type == "bind" and k.bron.startswith("/") for k in self.opslag)

    def blijft_staan(self, checkpoint_datasets: tuple[str, ...] = ()) -> dict[str, Any]:
        datasets = set(self.datasets) | {k.dataset for k in self.opslag if k.dataset} | set(checkpoint_datasets)
        return {"datasets": sorted(datasets),
                "koppelingen": [k.model_dump(exclude={"type"}) for k in self.opslag]}

    def gedekt(self, punt: Herstelpunt) -> bool:
        datasets = set(punt.datasets)
        bronnen = set(punt.opslagbronnen)
        # Een herstelpunt zonder bronnen bewijst niets, ook niet bij een app zonder gemeten opslag.
        return (bool(bronnen) and set(self.datasets) <= datasets
                and {k.bron for k in self.opslag} == bronnen
                and all(k.dataset in datasets for k in self.opslag if k.dataset))


def beschermregel(naam: str, record: Mapping[str, Any]) -> beschermd.Regel | None:
    """Toets ook de gemeten alias, catalogusnaam en nevenimages aan de gepinde beschermlijst."""
    try:
        beleid = beschermd.beleid()
    except beschermd.BeleidFout:
        raise ApiError(503, "beleid_onbekend", "De beschermde lijst van de Hub is onleesbaar.") from None
    images = record.get("images")
    beelden: list[str] = []
    if isinstance(images, list):
        beelden = [im["repository"] for im in images if isinstance(im, dict) and isinstance(im.get("repository"), str)]
    image = record.get("image")
    if isinstance(image, dict) and isinstance(image.get("repository"), str):
        beelden.append(image["repository"])
    catalogus = record.get("catalogus")
    namen = (naam, record.get("runtime_id"), record.get("app_id"),
             catalogus.get("app") if isinstance(catalogus, dict) else None)
    for identiteit in namen:
        if isinstance(identiteit, str):
            regels = beschermd.regels(beleid, "app.uninstall", identiteit, beelden)
            if regels:
                return regels[0]
    return None


def toets_beleid(naam: str, record: Mapping[str, Any]) -> None:
    regel = beschermregel(naam, record)
    if regel:
        raise ApiError(403, "forbidden", regel.reden_nl, {"reason": regel.code, "regel": regel.id})


def toets_herstelpunt(punt: Herstelpunt, naam: str, record: Mapping[str, Any], opslag: GemetenOpslag | None,
                      now: datetime) -> tuple[str, str] | None:
    if (punt.app != naam or str(punt.inventaris_id) != record.get("id")
            or punt.versie_etag != record.get("versie_etag")):
        return "herstelpunt_ander_doel", "Het herstelbewijs hoort niet bij deze app en inventarisrevisie."
    if punt.status != "geverifieerd":
        return "herstelpunt_ongeverifieerd", "De Hub heeft dit herstelpunt niet geverifieerd."
    if punt.rpo_uren is None:
        return "rpo_onbekend", "De maximale leeftijd van dit herstelpunt ontbreekt in het beleid."
    leeftijd = now - punt.gemaakt_op
    if not timedelta(0) <= leeftijd < timedelta(hours=punt.rpo_uren):
        return "herstelpunt_buiten_rpo", "Het herstelpunt ligt buiten het geldige tijdvenster van het beleid."
    if opslag is None or not opslag.gedekt(punt):
        return "herstelpunt_onvolledig", "Het herstelpunt dekt niet alle gemeten opslag van de app."
    if punt.archief is None:
        return "herstelarchief_ontbreekt", "Het archief met het oorspronkelijke manifest en de waarden ontbreekt."
    return None
