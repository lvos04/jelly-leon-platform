"""Gesimuleerde datasetinhoud (snapshot, kloon, terugzetten) en de storing ``migratie_mislukt`` (B08-004).

Zo is in de lifecycle-E2E meetbaar dat een mislukte databasemigratie in een schaduwapp (op een kloon) de productie-app
en haar data ongemoeid laat. De inhoud is alleen via ``/_sim/inhoud`` zichtbaar, nooit via de JSON-RPC-API.
"""

from __future__ import annotations

from typing import Any

from conftest import Rpc, ix_values
from starlette.testclient import TestClient

DB = {"schema": "1", "gegevens": "kijkhistorie-en-voortgang"}


def _app(rw: Rpc, name: str, tag: str, path: str, port: int) -> Any:
    return rw.job("app.create", {"app_name": name, "catalog_app": "ix-app", "train": "stable", "version": "1.4.8",
                                 "values": ix_values(tag=tag, host_port=port, repo="ghcr.io/voorbeeld/app",
                                                     host_path=path)})


def test_inhoud_volgt_snapshot_en_kloon(client: TestClient, rw: Rpc) -> None:
    client.post("/_sim/fixtures", json={"datasets": ["simpool/apps/app", "simpool/apps/sandbox"]})
    assert client.post("/_sim/inhoud", json={"dataset": "simpool/apps/app", "inhoud": DB}).status_code == 200
    rw.call("pool.snapshot.create", {"dataset": "simpool/apps/app", "name": "voor-update"})
    rw.call("pool.snapshot.clone", {"snapshot": "simpool/apps/app@voor-update",
                                    "dataset_dst": "simpool/apps/sandbox/app-kloon"})
    inhoud = client.get("/_sim/inhoud").json()
    assert inhoud["datasets"]["simpool/apps/sandbox/app-kloon"] == DB
    assert inhoud["snapshots"]["simpool/apps/app@voor-update"] == DB
    # de JSON-RPC-API toont de inhoud nooit
    assert all("inhoud" not in d for d in rw.call("pool.dataset.query"))


def test_migratie_mislukt_raakt_alleen_de_app_met_die_image(client: TestClient, rw: Rpc) -> None:
    client.post("/_sim/fixtures", json={"datasets": ["simpool/apps/app", "simpool/apps/sandbox"]})
    client.post("/_sim/inhoud", json={"dataset": "simpool/apps/app", "inhoud": DB})
    _app(rw, "app", "1.0.0", "/mnt/simpool/apps/app", 31001)
    rw.call("pool.snapshot.create", {"dataset": "simpool/apps/app", "name": "s"})
    rw.call("pool.snapshot.clone", {"snapshot": "simpool/apps/app@s", "dataset_dst": "simpool/apps/sandbox/kloon"})
    client.post("/_sim/faults/migratie_mislukt", json={"image": "ghcr.io/voorbeeld/app:2.0.0"})
    schaduw = _app(rw, "app-schaduw", "2.0.0", "/mnt/simpool/apps/sandbox/kloon", 31002)
    assert schaduw["state"] == "CRASHED"  # de job slaagt, de container crasht daarna
    inhoud = client.get("/_sim/inhoud").json()["datasets"]
    assert inhoud["simpool/apps/sandbox/kloon"]["schema"] == "half-gemigreerd:2.0.0"
    assert inhoud["simpool/apps/app"] == DB  # productie-data ongemoeid
    prod = rw.call("app.query", [["id", "=", "app"]], {"get": True})
    assert prod["state"] == "RUNNING" and prod["active_workloads"]["images"] == ["ghcr.io/voorbeeld/app:1.0.0"]


def test_migratie_mislukt_bij_update_en_terugzetten_met_snapshot(client: TestClient, rw: Rpc) -> None:
    client.post("/_sim/fixtures", json={"datasets": ["simpool/apps/app"]})
    client.post("/_sim/inhoud", json={"dataset": "simpool/apps/app", "inhoud": DB})
    _app(rw, "app", "1.0.0", "/mnt/simpool/apps/app", 31001)
    rw.call("pool.snapshot.create", {"dataset": "simpool/apps/app", "name": "terugzetpunt"})
    client.post("/_sim/faults/migratie_mislukt", json={"image": "ghcr.io/voorbeeld/app:2.0.0"})
    nieuw = ix_values(tag="2.0.0", host_port=31001, repo="ghcr.io/voorbeeld/app", host_path="/mnt/simpool/apps/app")
    bijgewerkt = rw.job("app.update", "app", {"values": nieuw})
    assert bijgewerkt["state"] == "CRASHED"
    assert client.get("/_sim/inhoud").json()["datasets"]["simpool/apps/app"]["beschadigd"] is True
    # terugzetten van de data (destructief, alleen via de eigenaar): inhoud weer van het terugzetpunt, als overtreding
    # geregistreerd zodat een test kan zien dat dit nooit automatisch gebeurt
    rw.call("pool.snapshot.rollback", "simpool/apps/app@terugzetpunt")
    assert client.get("/_sim/inhoud").json()["datasets"]["simpool/apps/app"] == DB
    assert any(v["code"] == "snapshot_rollback" for v in client.get("/_sim/violations").json())


def test_inhoud_alleen_voor_bestaande_dataset(client: TestClient) -> None:
    assert client.post("/_sim/inhoud", json={"dataset": "simpool/bestaat-niet", "inhoud": DB}).status_code == 404
    assert client.post("/_sim/inhoud", json={"dataset": "simpool/apps", "inhoud": "geen object"}).status_code == 404
