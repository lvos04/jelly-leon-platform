"""B08-006. Alleen verifieerbare, overdraagbare inventaris levert handmatige verhuisstappen op."""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from ipaddress import IPv4Address
from socket import INADDR_LOOPBACK
from typing import Any

import planhulp as h
import pytest
import yaml

from appstore.manifest import validate_text
from lifecycle import beschermd
from lifecycle.errors import ApiError
from lifecycle.verhuizen import Verhuisexport, plan_verhuizen
from lifecycle.verwijderen import Herstelpunt

BRON = h.GOTIFY["data_path"]
DATASET = BRON.removeprefix("/mnt/")
ADRES = str(IPv4Address(INADDR_LOOPBACK))


@pytest.fixture
def record() -> dict[str, Any]:
    return h.gotify_record(digest=h.GOTIFY_DIGEST)


@pytest.fixture
def export() -> dict[str, Any]:
    manifest = yaml.safe_load(h.manifest("gotify"))
    for name, typ, source in (("dienstsleutel", "secret_ref", "secret"), ("omschrijving", "string", "wizard"),
                              ("doel_url", "url", "wizard")):
        manifest["parameters"].append({"name": name, "type": typ, "source": source, "required": False,
                                       "description_nl": "Proefwaarde voor de verhuizing."})
    return {"manifest_yaml": yaml.safe_dump(manifest),
            "parameters": {**h.GOTIFY, "dienstsleutel": str(uuid.uuid4()), "omschrijving": ADRES,
                           "doel_url": f"https://{ADRES}/", "extra": DATASET},
            "datasetmetingen": [{"bron": BRON, "dataset": DATASET, "grootte_bytes": 8123}]}


@pytest.fixture
def punt(record: dict[str, Any], export: dict[str, Any]) -> dict[str, Any]:
    rapport, _, _ = validate_text(export["manifest_yaml"])
    assert rapport.valid
    return {"app": "gotify", "checkpoint_id": uuid.uuid4(), "inventaris_id": record["id"], "versie_etag": 1,
            "resource_etag": h.GOTIFY_DIGEST, "status": "geverifieerd", "gemaakt_op": h.NU, "rpo_uren": 2,
            "datasets": [DATASET], "opslagbronnen": [BRON],
            "archief": {"export_id": uuid.uuid4(), "manifest_sha256": rapport.manifest_sha256,
                        "waarden_sha256": h.OUDE_DIGEST}}


def preview(record: dict[str, Any], punt: dict[str, Any], export: dict[str, Any]) -> Any:
    return plan_verhuizen(record["naam"], h.inventaris(record), Herstelpunt.model_validate(punt),
                          Verhuisexport.model_validate(export), h.NU)


def test_alleen_openbare_waarden_en_logische_rollen(record: Any, punt: Any, export: Any) -> None:
    plan = preview(record, punt, export)
    assert plan.status == "beschikbaar" and plan.app_id == plan.manifest.app_id == "gotify"
    assert plan.manifest.manifest_sha256 == punt["archief"]["manifest_sha256"]
    assert plan.manifest.image_digests == [h.GOTIFY_DIGEST] and plan.manifest.versie == "3.1.1"
    assert plan.waarden == {k: h.GOTIFY[k] for k in ("host_port", "timezone", "rpo", "rto")}
    assert [d.model_dump() for d in plan.datasets] == [{"rol": "gotify_data", "soort": "data", "grootte_bytes": 8123}]
    assert set(plan.opnieuw_kiezen) == {"data_path", "omschrijving", "doel_url"}
    assert [(g.naam, g.invoeren_in) for g in plan.geheimen] == [("dienstsleutel", "Kluis van de Hub op de doelhost")]
    assert plan.herstelkaart.checkpoint_id == punt["checkpoint_id"]
    assert plan.herstelkaart.export_id == punt["archief"]["export_id"]
    tekst = plan.model_dump_json()
    assert not re.search(r"(?:\d{1,3}\.){3}\d{1,3}|/mnt/[^/]+", tekst)
    assert all(v not in tekst for v in (DATASET, export["parameters"]["dienstsleutel"], "https://", "extra"))
    assert "Bevestig" in plan.stappen[-2] and "Pas na die bevestiging" in plan.stappen[-1]


@pytest.mark.parametrize("naam", ["emby", "embyserver-kopie", "jellyfin", "jelly-leon", "mediaguard-core",
                                  "prowlarr", "sabnzbd", "qbittorrent", "ai-server-manager-executor"])
def test_beschermbeleid_gaat_voor_verhuisbewijs(naam: str) -> None:
    plan = plan_verhuizen(naam, h.inventaris(), None, None, h.NU)
    assert plan.status == "laten_staan" and plan.stappen == [] and plan.herstelkaart is None


@pytest.mark.parametrize("veld", ["app_id", "catalogus", "images"])
def test_beschermde_identiteit_achter_alias(veld: str, record: Any, punt: Any, export: Any) -> None:
    record[veld] = {"app_id": "mediaguard", "catalogus": {"app": "emby"},
                    "images": [*record["images"], {"repository": "voorbeeld/jellyfin"}]}[veld]
    assert preview(record, punt, export).status == "laten_staan"


def test_onleesbaar_beleid_blokkeert(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setattr(beschermd, "BELEID", tmp_path / "ontbreekt")
    with pytest.raises(ApiError) as fout:
        plan_verhuizen("gotify", h.inventaris(), None, None, h.NU)
    assert fout.value.status == 503


@pytest.mark.parametrize(("groep", "wijziging", "reden"), [
    ("punt", {"status": "ongeldig"}, "herstelpunt_ongeverifieerd"),
    ("punt", {"gemaakt_op": h.NU - timedelta(hours=2)}, "herstelpunt_buiten_rpo"),
    ("punt", {"versie_etag": 3}, "herstelpunt_ander_doel"),
    ("punt", {"datasets": []}, "herstelpunt_onvolledig"),
    ("record", {"app_id": "andere-app"}, "manifest_onbruikbaar"),
    ("record", {"images": []}, "release_onbekend"),
    ("record", {"images": [{"repository": h.GOTIFY_REPO, "digest": h.OUDE_DIGEST}]}, "release_onbekend"),
    ("record", {"aanwezig": False}, "app_niet_geinstalleerd"),
    ("export", {"manifest_yaml": "app_id: onbekend"}, "manifest_onbruikbaar"),
    ("export", {"manifest_yaml": h.manifest("gotify")}, "manifest_onbruikbaar"),
    ("export", {"datasetmetingen": []}, "datasetrollen_onbekend"),
    ("export", {"datasetmetingen": [{"bron": BRON, "dataset": "ander/archief", "grootte_bytes": 0}]},
     "datasetrollen_onbekend"),
])
def test_onvolledige_of_verkeerde_bron_geeft_geen_stappen(groep: str, wijziging: Any, reden: str,
                                                          record: Any, punt: Any, export: Any) -> None:
    {"record": record, "punt": punt, "export": export}[groep].update(wijziging)
    plan = preview(record, punt, export)
    assert (plan.status, plan.reden, plan.stappen, plan.herstelkaart) == ("geblokkeerd", reden, [], None)


def test_catalogusapp_met_verschillende_rollen_en_kluisvelden() -> None:
    tekst = h.manifest("authentik")
    rapport, manifest, _ = validate_text(tekst)
    assert rapport.valid and manifest and manifest.catalog
    opslag = [(f"/mnt/proef/{d.name}", d.mount, f"proef/{d.name}") for d in manifest.storage]
    record = h.app_record(manifest.app_id, catalogus_app=manifest.catalog.app, trein=manifest.catalog.train,
                          versie=manifest.catalog.version, digest=h.OUDE_DIGEST, opslag=opslag)
    punt = {"checkpoint_id": uuid.uuid4(), "app": manifest.app_id, "inventaris_id": record["id"], "versie_etag": 1,
            "resource_etag": h.OUDE_DIGEST, "gemaakt_op": h.NU, "rpo_uren": 1, "status": "geverifieerd",
            "datasets": [ds for _, _, ds in opslag], "opslagbronnen": [p for p, _, _ in opslag],
            "archief": {"export_id": uuid.uuid4(), "manifest_sha256": rapport.manifest_sha256,
                        "waarden_sha256": h.GOTIFY_DIGEST}}
    metingen = [{"bron": p, "dataset": ds, "grootte_bytes": i} for i, (p, _, ds) in enumerate(opslag)]
    export = {"manifest_yaml": tekst, "datasetmetingen": metingen, "parameters": {"http_port": 30140}}
    plan = preview(record, punt, export)
    assert plan.status == "beschikbaar"
    assert {d.rol for d in plan.datasets} == {d.required_dataset_role for d in manifest.storage}
    assert {d.soort for d in plan.datasets} >= {"configuratie", "data"}
    assert plan.waarden == {"http_port": 30140}
    assert {g.naam for g in plan.geheimen} == {"db_password", "secret_key"}
    assert plan.herstelkaart.bron_app == manifest.app_id
    record["catalogus"]["versie"] = "onbekend"
    assert preview(record, punt, export).reden == "release_onbekend"
