"""B08-006 via de lifecycle-API en de lokale Hub-, executor- en TrueNAS-simulators."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import ketenhulp as k
import planhulp as h
import pytest
import yaml
from hub_p2 import P2Error

from appstore.manifest import validate_text
from appstore.manifest.canonical import sha256_digest
from appstore.manifest.translate import Target, translate
from lifecycle import beschermd

PAD = "/lifecycle/v1/plans/uninstall/preview"


@pytest.mark.parametrize("optie", ["remove_ix_volumes", "force_remove_ix_volumes", "remove_images"])
def test_verwijderopties_zijn_geen_invoer(client: Any, auth: Any, optie: str) -> None:
    antwoord = client.post(PAD, json={"app_name": "immich", optie: True}, headers=auth("admin", idem=True))
    assert antwoord.status_code == 422
    assert antwoord.json()["code"] == "validation_error"


def test_geen_herstelpunt_en_geen_schrijfrecht(client: Any, auth: Any, hub: Any) -> None:
    hub.state.sim.p2.zet_apps(h.app_record("immich"))
    body = {"app_name": "immich"}
    assert client.post(PAD, json=body).status_code == 401
    assert client.post(PAD, json=body, headers=auth("viewer", idem=True)).status_code == 403
    antwoord = client.post(PAD, json=body, headers=auth("operator", idem=True))
    assert antwoord.status_code == 201
    plan = antwoord.json()
    assert "herstelpunt_ontbreekt" in h.codes(plan["plan"])
    assert plan["status"] == "geblokkeerd" and plan["vrijgegeven_stappen"] == []
    assert plan["plan"]["stappen"] == []


def test_onleesbaar_beleid_weigert_ook_de_rechtstreekse_route(client: Any, auth: Any, hub: Any,
                                                               tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub.state.sim.p2.zet_apps(h.app_record("immich"))
    monkeypatch.setattr(beschermd, "BELEID", tmp_path / "ontbreekt")
    antwoord = client.post(PAD, json={"app_name": "immich"}, headers=auth("admin", idem=True))
    assert (antwoord.status_code, antwoord.json()["code"]) == (503, "beleid_onbekend")


FOTOS = "simpool/media"
EXTRA = "simbackup/proefalbum"
IX = "simpool/ix-apps/app_mounts/immich/bibliotheek"
EMBY = "simpool/apps/kijkstanden"
MG = "simpool/apps/voortgang"


def installeer_proefapp(keten: k.Keten) -> str:
    """Maak de proefinstallatie uit het oorspronkelijke manifest via dezelfde vertaler als het installatieplan."""
    tekst = (h.ROOT / "e2e/conformance/demo-app.manifest.yaml").read_text().replace("demo-app", "immich")
    doc = yaml.safe_load(tekst)
    doc["storage"].extend([
        {"name": "album", "kind": "data", "mount": "/extra", "required_dataset_role": "extra_album"},
        {"name": "bibliotheek", "kind": "config", "mount": "/bibliotheek", "required_dataset_role": "bibliotheek"}])
    doc["custom_app"]["storage"].extend([
        {"name": "album", "type": "host_path", "host_path": {"param": "album_pad"}},
        {"name": "bibliotheek", "type": "ix_volume", "dataset_name": "bibliotheek"}])
    doc["parameters"].append({**doc["parameters"][-1], "name": "album_pad", "dataset_role": "extra_album"})
    doc["backup"]["snapshot_datasets"] = ["data", "album", "bibliotheek"]
    tekst = yaml.safe_dump(doc)
    rapport, manifest, _ = validate_text(tekst)
    assert rapport.valid and manifest, [f.code for f in rapport.findings]
    doel = keten.doel()
    resultaat = translate(manifest, {"data_path": "/mnt/" + FOTOS, "album_pad": "/mnt/" + EXTRA},
                          Target(**doel, available_versions=[doel["ix_app_version"]]), app_name="immich",
                          manifest_sha256=rapport.manifest_sha256)
    assert resultaat.ready_for_install and resultaat.payload, [f.code for f in resultaat.findings]
    keten.rpc.job("app.create", resultaat.payload.model_dump(mode="json"))
    return tekst


def test_verwijderen_en_herinstalleren_met_behoud_van_gegevens(client: Any, auth: Any, hub: Any,
                                                              tmp_path: Path) -> None:
    with k.nas_simulator() as (rpc, nas):
        keten = k.bouw_keten(client, auth, hub, rpc, nas)
        versie = keten.doel()["ix_app_version"]
        nas.post("/_sim/fixtures", json={"datasets": [EXTRA, EMBY, MG], "apps": [
            {"app_name": naam, "version": versie, "values": {"image": {"repository": "voorbeeld/" + naam,
                                                                       "tag": "1.0.0"}}}
            for naam in ("emby", "mediaguard")]}).raise_for_status()
        manifest = installeer_proefapp(keten)
        proefdata = {FOTOS: {"fotos": ["album-a", "album-b"]}, EXTRA: {"foto": "buiten-de-pool"},
                     IX: {"index": [10, 20]}, MG: {"taak": "proef", "positie": 37},
                     EMBY: {"gebruikers": {naam: {"gezien": True, "hervatpunt": positie, "favoriet": True,
                                                 "beoordeling": 4, "spoorkeuze": 2}
                                          for naam, positie in (("een", 18), ("twee", 24))}}}
        for dataset, inhoud in proefdata.items():
            nas.post("/_sim/inhoud", json={"dataset": dataset, "inhoud": inhoud}).raise_for_status()
        keten.meet()
        voor = keten.inhoud()["datasets"]
        assert all(voor[ds] == inhoud for ds, inhoud in proefdata.items())
        for naam in ("mediaguard", "emby"):
            r = client.post(PAD, json={"app_name": naam}, headers=auth("admin", idem=True))
            assert r.status_code == 403 and r.json()["details"]["regel"].startswith("APP-")

        # De Hub-stand-in leest echte snapshots in de simulator en archiveert de gemeten waarden buiten het plan.
        snapshots = [rpc.call("pool.snapshot.create", {"dataset": ds, "name": "voor-verwijderen"})
                     for ds in (FOTOS, EXTRA, IX)]
        assert all(keten.inhoud()["snapshots"][s["id"]] == voor[s["dataset"]] for s in snapshots)
        export_id = str(uuid.uuid4())
        archief = tmp_path / export_id
        archief.mkdir()
        waarden = rpc.call("app.config", "immich")
        (archief / "waarden.json").write_text(json.dumps(waarden))
        (archief / "manifest.yaml").write_text(manifest)
        record = hub.state.sim.p2.apps["immich"]
        punt = {"app": "immich", "inventaris_id": record["id"], "versie_etag": record["versie_etag"],
                "resource_etag": keten.executor.etag({"app_id": "immich"}), "checkpoint_id": str(uuid.uuid4()),
                "status": "geverifieerd", "rpo_uren": 1,
                "gemaakt_op": datetime.fromtimestamp(min(s["properties"]["creation"]["parsed"]["$date"]
                                                         for s in snapshots) / 1000, UTC).isoformat(),
                "datasets": [s["dataset"] for s in snapshots], "opslagbronnen": [m["bron"] for m in record["opslag"]],
                "archief": {"export_id": export_id, "manifest_sha256": validate_text(manifest)[0].manifest_sha256,
                            "waarden_sha256": sha256_digest(waarden)}}
        body = {"app_name": "immich", "herstelpunt": punt}
        r = client.post(PAD, json=body, headers=auth("admin", idem=True))
        assert (r.status_code, r.json()["details"]["reason"]) == (403, "herstelpunt_niet_vertrouwd")
        oud = {**punt, "gemaakt_op": (datetime.now(UTC) - timedelta(hours=2)).isoformat()}
        r = client.post(PAD, json={**body, "herstelpunt": oud}, headers=h.hub_kop(hub, str(uuid.uuid4()), None))
        assert r.status_code == 201 and "herstelpunt_buiten_rpo" in h.codes(r.json()["plan"])

        sleutel = str(uuid.uuid4())
        r = client.post(PAD, json=body, headers=h.hub_kop(hub, sleutel, None))
        assert r.status_code == 201
        view = r.json()
        assert view["vrijgegeven_stappen"] == ["verwijderen"]
        assert client.post(PAD, json=body, headers=h.hub_kop(hub, sleutel, None)).json() == view
        assert "APP_MODE" not in json.dumps(view)
        diff = view["plan"]["diff"]
        assert diff["wordt_verwijderd"] == [{"soort": "app", "naam": "immich"}]
        assert set(diff["blijft_staan"]["datasets"]) == {FOTOS, EXTRA, IX}
        assert {m["bron"] for m in diff["blijft_staan"]["koppelingen"]} == set(punt["opslagbronnen"])
        assert {m["soort"] for m in diff["blijft_staan"]["koppelingen"]} == {"hostpad", "ixvolume"}

        start = len(nas.get("/_sim/calls").json())
        verzoek = keten.dien_in(view, "verwijderen")
        with pytest.raises(P2Error) as fout:
            hub.state.sim.execution_request(verzoek.request_id)
        assert fout.value.code == "goedkeuring_ontbreekt" and keten.app("immich") is not None
        uit = keten.voer_uit(view, "verwijderen")
        assert uit.plan["status"] == "voltooid" and uit.resultaat["readback"]["staat"] == "ABSENT"
        assert uit.request["approval"]["backup_checkpoint_id"] == punt["checkpoint_id"]
        assert [c["method"] for c in nas.get("/_sim/calls").json()[start:] if c["write"]] == ["app.delete"]
        jobs = rpc.call("core.get_jobs", [["method", "=", "app.delete"]])
        opties = {"remove_images": False, "remove_ix_volumes": False, "force_remove_ix_volumes": False}
        assert diff["verwijderopties"] == opties and [j["arguments"] for j in jobs] == [["immich", opties]]
        assert keten.app("immich") is None and keten.overtredingen() == []
        assert set(proefdata) <= set(nas.get("/_sim/state").json()["datasets"])
        assert keten.inhoud()["datasets"] == voor
        assert all(keten.app(naam)["state"] == "RUNNING" for naam in ("emby", "mediaguard"))

        terug = keten.lees(view["id"])["plan"]["terugweg"]
        map_van_kaart = tmp_path / terug["archief"]["export_id"]
        bewaard = json.loads((map_van_kaart / "waarden.json").read_text())
        rapport, gelezen, _ = validate_text((map_van_kaart / "manifest.yaml").read_text())
        assert rapport.valid and gelezen and gelezen.app_id == terug["app_name"]
        assert rapport.manifest_sha256 == terug["archief"]["manifest_sha256"]
        assert sha256_digest(bewaard) == terug["archief"]["waarden_sha256"]
        rpc.job("app.create", {**{k: terug[k] for k in ("app_name", "train", "catalog_app", "version")},
                               "values": bewaard})
        assert keten.app("immich")["state"] == "RUNNING" and rpc.call("app.config", "immich") == waarden
        assert keten.inhoud()["datasets"] == voor
