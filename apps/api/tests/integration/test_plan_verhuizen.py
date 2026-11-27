"""B08-006 en EX50 via de lifecycle-API, de Hub-stand-in en twee lokale TrueNAS-simulators."""

from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import ketenhulp as k
import planhulp as h
import yaml
from sqlalchemy import select

from appstore.manifest import validate_text
from appstore.manifest.canonical import sha256_digest
from appstore.manifest.translate import Target, translate
from lifecycle.models import AuditEntry, IdempotencyRecord, LifecyclePlan

PAD = "/lifecycle/v1/plans/move/preview"


def test_zonder_herstelpunt_geen_verhuisstappen(client: Any, auth: Any, hub: Any) -> None:
    hub.state.sim.p2.zet_apps(h.gotify_record())
    body = {"app_name": "gotify"}
    assert client.post(PAD, json=body).status_code == 401
    assert client.post(PAD, json=body, headers=auth("viewer", idem=True)).status_code == 403
    antwoord = client.post(PAD, json=body, headers=auth("operator", idem=True))
    assert antwoord.status_code == 200
    plan = antwoord.json()
    assert plan["status"] == "geblokkeerd" and plan["reden"] == "herstelpunt_ontbreekt"
    assert plan["stappen"] == [] and plan["herstelkaart"] is None


def test_beschermde_app_alleen_laten_staan(client: Any, auth: Any) -> None:
    antwoord = client.post(PAD, json={"app_name": "emby"}, headers=auth("admin", idem=True))
    assert antwoord.status_code == 200
    plan = antwoord.json()
    assert plan["status"] == "laten_staan" and plan["reden"] == "emby_nooit_door_platform"
    assert plan["stappen"] == [] and plan["herstelkaart"] is None


def test_ex50_hetzelfde_plan_op_een_ander_adres(client: Any, auth: Any, hub: Any) -> None:
    """De proefbeheerder herstelt dezelfde export met een nieuwe roltoewijzing. De dienst kent de doelhost niet."""
    tekst = yaml.safe_load(h.manifest("gotify"))
    tekst["parameters"].append({"name": "dienstsleutel", "type": "secret_ref", "source": "secret",
                                "required": False, "description_nl": "Sleutel uit de kluis."})
    manifest_yaml = yaml.safe_dump(tekst)
    rapport, manifest, _ = validate_text(manifest_yaml)
    assert rapport.valid and manifest
    geheim = str(uuid.uuid4())
    kijkers = {naam: {"gezien": True, "hervat": n, "favoriet": True, "beoordeling": 4, "spoorkeuze": 2}
               for naam, n in (("een", 18), ("twee", 24))}
    metingen = {"kijkstanden": kijkers, "voortgang": {"taak": "proef", "positie": 37}}
    with (k.nas_simulator(base_url="http://bron.invalid") as (bron_rpc, bron),
          k.nas_simulator(base_url="http://doel.invalid") as (doel_rpc, doel)):
        assert bron.base_url != doel.base_url and bron.get("/api/boot_id").json() != doel.get("/api/boot_id").json()
        keten = k.bouw_keten(client, auth, hub, bron_rpc, bron)
        k.gotify_draait(keten, tag=f"{manifest.version}@{h.GOTIFY_DIGEST}")
        versie = keten.doel()["ix_app_version"]
        bron.post("/_sim/fixtures", json={"datasets": list(metingen), "apps": [
            {"app_name": naam, "version": versie, "values": {"image": {"repository": "voorbeeld/" + naam,
                                                                       "tag": "1.0.0"}}}
            for naam in ("emby", "mediaguard")]}).raise_for_status()
        for dataset, inhoud in metingen.items():
            bron.post("/_sim/inhoud", json={"dataset": dataset, "inhoud": inhoud}).raise_for_status()
        keten.meet()
        voor = keten.inhoud()["datasets"]
        assert all(voor[ds] == inhoud for ds, inhoud in metingen.items())
        bronwaarden = bron_rpc.call("app.config", "gotify")
        snapshot = bron_rpc.call("pool.snapshot.create", {"dataset": k.DATASET, "name": "voor-verhuizen"})
        exportdata = keten.inhoud()["snapshots"][snapshot["id"]]
        assert exportdata == voor[k.DATASET]
        dataset = next(d for d in bron_rpc.call("pool.dataset.query") if d["id"] == k.DATASET)
        record = hub.state.sim.p2.apps["gotify"]
        punt = {"app": "gotify", "inventaris_id": record["id"], "versie_etag": record["versie_etag"],
                "resource_etag": keten.executor.etag({"app_id": "gotify"}), "status": "geverifieerd",
                "checkpoint_id": str(uuid.uuid4()), "gemaakt_op": datetime.fromtimestamp(
                    snapshot["properties"]["creation"]["parsed"]["$date"] / 1000, UTC).isoformat(), "rpo_uren": 1,
                "datasets": [dataset["id"]], "opslagbronnen": [k.PAD],
                "archief": {"export_id": str(uuid.uuid4()), "manifest_sha256": rapport.manifest_sha256,
                            "waarden_sha256": sha256_digest(bronwaarden)}}
        parameters = {**h.GOTIFY, "data_path": k.PAD, "timezone": bronwaarden["TZ"],
                      "host_port": bronwaarden["ports"][0]["port_number"]}
        export = {"manifest_yaml": manifest_yaml, "parameters": {**parameters, "dienstsleutel": geheim},
                  "datasetmetingen": [{"bron": k.PAD, "dataset": dataset["id"],
                                      "grootte_bytes": dataset["used"]["parsed"]}]}
        archieven = {punt["archief"]["export_id"]: {"manifest": manifest_yaml, "waarden": bronwaarden,
                                                   "datasets": {"gotify_data": exportdata}}}
        body = {"app_name": "gotify", "herstelpunt": punt, "export": export}
        start = len(bron.get("/_sim/calls").json())
        for veld in ("herstelpunt", "export"):
            antwoord = client.post(PAD, json={"app_name": "gotify", veld: body[veld]}, headers=auth("admin", idem=True))
            assert (antwoord.status_code, antwoord.json()["details"]["reason"]) == (403, "herstelpunt_niet_vertrouwd")
        sleutel = str(uuid.uuid4())
        antwoord = client.post(PAD, json=body, headers=h.hub_kop(hub, sleutel, None))
        assert antwoord.status_code == 200 and antwoord.json()["status"] == "beschikbaar"
        plan = antwoord.json()
        herhaling = client.post(PAD, json=body, headers=h.hub_kop(hub, sleutel, None))
        assert herhaling.headers["Idempotent-Replay"] == "true" and herhaling.json() == plan
        for gevoelig in (geheim, k.DATASET, k.PAD, "bron.invalid", "doel.invalid"):
            assert gevoelig not in antwoord.text
        assert not re.search(r"(?:\d{1,3}\.){3}\d{1,3}|/mnt/[^/]+", antwoord.text)
        assert plan["datasets"] == [{"rol": "gotify_data", "soort": "data", "grootte_bytes": dataset["used"]["parsed"]}]
        assert plan["herstelkaart"]["waarden_sha256"] == sha256_digest(bronwaarden)
        assert not any(c["write"] for c in bron.get("/_sim/calls").json()[start:])
        assert not any(c["write"] for c in doel.get("/_sim/calls").json())
        with client.app.state.session_factory() as session:
            assert session.scalar(select(LifecyclePlan)) is None
            audit = session.scalars(select(AuditEntry).where(AuditEntry.action == "lifecycle.move.preview")).all()
            assert len(audit) == 1 and geheim not in json.dumps(audit[0].detail)
            cache = session.scalars(select(IdempotencyRecord).where(IdempotencyRecord.path == PAD)).all()
            assert all(geheim not in json.dumps(r.response_body) for r in cache)

        # De eigenaar kiest hier pas het doelpad en past het ontvangen plan toe via het standaardformulier.
        archief = archieven[plan["herstelkaart"]["export_id"]]
        pin, te_herstellen, _ = validate_text(archief["manifest"])
        assert pin.valid and te_herstellen and pin.manifest_sha256 == plan["manifest"]["manifest_sha256"]
        assert sha256_digest(archief["waarden"]) == plan["herstelkaart"]["waarden_sha256"]
        doelpaden = {"gotify_data": "herstelsim/nieuwe-meldingen"}
        doel_dataset = doelpaden[plan["datasets"][0]["rol"]]
        doel.post("/_sim/fixtures", json={"datasets": [doel_dataset]}).raise_for_status()
        kopie = archief["datasets"][plan["datasets"][0]["rol"]]
        doel.post("/_sim/inhoud", json={"dataset": doel_dataset, "inhoud": kopie}).raise_for_status()
        params = dict(plan["waarden"])
        for p in te_herstellen.parameters:
            if p.source == "dataset_picker":
                params[p.name] = "/mnt/" + doelpaden[p.dataset_role]
        schema = doel_rpc.call("catalog.get_app_details", "ix-app", {"train": "stable"})
        vertaling = translate(te_herstellen, params, Target(truenas_version=doel_rpc.call("system.version_short"),
                              ix_app_version=schema["latest_version"], available_versions=list(schema["versions"])),
                              app_name=plan["app_id"], manifest_sha256=plan["manifest"]["manifest_sha256"])
        assert vertaling.ready_for_install and vertaling.payload
        doel_rpc.job("app.create", vertaling.payload.model_dump(mode="json"))
        bron_app = keten.app(plan["app_id"])
        doel_app, = doel_rpc.call("app.query", [["name", "=", plan["app_id"]]])
        assert bron_app["name"] == doel_app["name"] == plan["app_id"] == manifest.app_id
        assert bron_app["state"] == doel_app["state"] == "RUNNING"
        assert doel_app["active_workloads"]["images"][0].endswith("@" + plan["manifest"]["image_digests"][0])
        bron_mount, = bron_app["active_workloads"]["volumes"]
        doel_mount, = doel_app["active_workloads"]["volumes"]
        assert bron_mount["source"] != doel_mount["source"] == "/mnt/" + doel_dataset
        rollen = {d.mount: d.required_dataset_role for d in manifest.storage}
        assert rollen[bron_mount["destination"]] == rollen[doel_mount["destination"]] == plan["datasets"][0]["rol"]
        assert doel.get("/_sim/inhoud").json()["datasets"][doel_dataset] == voor[k.DATASET]
        assert keten.inhoud()["datasets"] == voor
        assert not any(c["write"] for c in bron.get("/_sim/calls").json()[start:])
        assert [c["method"] for c in doel.get("/_sim/calls").json() if c["write"]] == ["app.create"]
        assert all(keten.app(naam)["state"] == "RUNNING" for naam in ("emby", "mediaguard"))


def test_doelhost_en_bronverwijdering_zijn_geen_invoer(client: Any, auth: Any) -> None:
    for veld in ("doelhost", "verwijder_bron"):
        r = client.post(PAD, json={"app_name": "gotify", veld: True}, headers=auth("admin", idem=True))
        assert r.status_code == 422 and r.json()["code"] == "validation_error"
