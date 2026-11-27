"""B08-006. Alleen een gemeten app met volledig en vers herstelbewijs krijgt een voorstel."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import planhulp as h
import pytest

from lifecycle import planner
from lifecycle.api_plans import vervaltijd
from lifecycle.errors import ApiError
from lifecycle.verwijderen import Herstelpunt

DATASET = "pool-x/fotos"
PAD = "/mnt/" + DATASET
BUITEN = "/srv/proefalbum"
VOLUME = "/mnt/.ix-apps/app_mounts/albums/bibliotheek"


@pytest.fixture
def record() -> dict[str, Any]:
    return h.app_record("albums", repository="voorbeeld/immich-server", opslag=[
        (PAD, "/fotos", DATASET), (BUITEN, "/extra", None), (VOLUME, "/bibliotheek", None)])


@pytest.fixture
def bewijs(record: dict[str, Any]) -> dict[str, Any]:
    return {"app": "albums", "inventaris_id": record["id"], "versie_etag": record["versie_etag"],
            "resource_etag": h.OUDE_DIGEST, "checkpoint_id": str(uuid.uuid4()), "status": "geverifieerd",
            "gemaakt_op": h.NU - timedelta(minutes=59), "rpo_uren": 1, "datasets": [DATASET],
            "opslagbronnen": [PAD, BUITEN, VOLUME],
            "archief": {"export_id": str(uuid.uuid4()), "manifest_sha256": h.OUDE_DIGEST,
                        "waarden_sha256": h.GOTIFY_DIGEST}}


def plan(record: dict[str, Any], bewijs: dict[str, Any] | None = None) -> dict[str, Any]:
    return planner.plan_verwijderen(plan_id=uuid.uuid4(), app_name=record["naam"], inventaris=h.inventaris(record),
                                   herstelpunt=Herstelpunt.model_validate(bewijs) if bewijs else None, now=h.NU)


def test_verwijderplan_en_terugweg(record: dict[str, Any], bewijs: dict[str, Any]) -> None:
    doc = plan(record, bewijs)
    assert doc["blokkades"] == [] and h.status(planner.begin(doc)) == {"verwijderen": "vrijgegeven"}
    assert doc["diff"]["wordt_verwijderd"] == [{"soort": "app", "naam": "albums"}]
    assert doc["diff"]["blijft_staan"] == {"datasets": [DATASET], "koppelingen": [
        {"soort": "hostpad", "bron": PAD, "doel": "/fotos", "dataset": DATASET},
        {"soort": "overig", "bron": BUITEN, "doel": "/extra", "dataset": None},
        {"soort": "ixvolume", "bron": VOLUME, "doel": "/bibliotheek", "dataset": None}]}
    stap = doc["stappen"][0]
    assert stap["voorstel"]["parameters"] == {} and stap["voorstel"]["action_id"] == "app.uninstall"
    assert stap["voorstel"]["expected_version"] == bewijs["resource_etag"]
    assert stap["herstelpunt"] == bewijs["checkpoint_id"] and doc["terugweg"]["archief"] == bewijs["archief"]
    assert doc["goedkeuring"]["vereist"] and vervaltijd(doc, h.NU) == h.NU + timedelta(minutes=1)


@pytest.mark.parametrize(("veld", "waarde", "code"), [
    ("app", "andere-app", "herstelpunt_ander_doel"),
    ("inventaris_id", str(uuid.uuid4()), "herstelpunt_ander_doel"),
    ("versie_etag", 2, "herstelpunt_ander_doel"),
    ("status", "niet_geverifieerd", "herstelpunt_ongeverifieerd"),
    ("rpo_uren", None, "rpo_onbekend"),
    ("gemaakt_op", h.NU - timedelta(hours=2), "herstelpunt_buiten_rpo"),
    ("gemaakt_op", h.NU + timedelta(seconds=1), "herstelpunt_buiten_rpo"),
    ("datasets", [], "herstelpunt_onvolledig"),
    ("opslagbronnen", [PAD, VOLUME], "herstelpunt_onvolledig"),
    ("opslagbronnen", [PAD, BUITEN, VOLUME, "/extra-config"], "herstelpunt_onvolledig"),
    ("archief", None, "herstelarchief_ontbreekt"),
])
def test_onbruikbaar_herstelbewijs(record: dict[str, Any], bewijs: dict[str, Any],
                                  veld: str, waarde: Any, code: str) -> None:
    doc = plan(record, {**bewijs, veld: waarde})
    assert code in h.codes(doc) and doc["stappen"] == []


@pytest.mark.parametrize("naam", ["emby", "embyserver-kopie", "jellyfin", "jelly-leon", "mediaguard-core",
                                  "prowlarr", "sabnzbd", "qbittorrent", "ai-server-manager",
                                  "ai-server-manager-executor"])
def test_beschermde_app_zonder_plan(naam: str) -> None:
    with pytest.raises(ApiError) as fout:
        plan(h.app_record(naam))
    assert fout.value.status == 403 and fout.value.details["regel"].startswith("APP-")


def test_nevenimage_en_catalogusnaam_worden_ook_herkend(record: dict[str, Any]) -> None:
    for wijziging in ({"images": [*record["images"], {"repository": "voorbeeld/embyserver"}]},
                      {"catalogus": {**record["catalogus"], "app": "emby"}}):
        with pytest.raises(ApiError) as fout:
            plan({**record, **wijziging})
        assert fout.value.details["reason"] == "emby_nooit_door_platform"


@pytest.mark.parametrize(("wijziging", "code"), [
    ({"opslag": []}, "herstelpunt_onvolledig"),
    ({"opslag": [{"soort": "hostpad", "doel": "/extra"}]}, "opslag_onbekend"),
    ({"opslag": [{"soort": "overig", "bron": "app-database", "doel": "/db", "type": "volume", "dataset": None}]},
     "opslagbehoud_onzeker"),
    ({"datasets": None}, "opslag_onbekend"),
    ({"images": []}, "images_onbekend"),
    ({"aanwezig": False}, "app_niet_geinstalleerd"),
])
def test_onvolledige_meting_blokkeert(record: dict[str, Any], bewijs: dict[str, Any],
                                     wijziging: dict[str, Any], code: str) -> None:
    doc = plan({**record, **wijziging}, bewijs)
    assert code in h.codes(doc) and doc["stappen"] == []
