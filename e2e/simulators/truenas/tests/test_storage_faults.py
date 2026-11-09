"""Opslag, snapshots, schijven/SMART, GPU, taken en storingsinjectie."""

from __future__ import annotations

import time

import pytest
from conftest import Rpc, RpcFailed, ix_values
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def test_snapshot_create_and_clone_to_sandbox(client: TestClient, rw: Rpc) -> None:
    snap = rw.call("pool.snapshot.create", {"dataset": "simpool/apps", "name": "voor-update-1"})
    assert snap["id"] == "simpool/apps@voor-update-1"
    assert rw.call("pool.snapshot.query", [["dataset", "=", "simpool/apps"]])[0]["snapshot_name"] == "voor-update-1"
    assert rw.call("pool.snapshot.clone", {"snapshot": snap["id"], "dataset_dst": "simbackup/snapshots/sandbox-1"})
    ds = rw.call("pool.dataset.query", [["id", "=", "simbackup/snapshots/sandbox-1"]], {"get": True})
    assert ds["origin"]["value"] == "simpool/apps@voor-update-1"
    with pytest.raises(RpcFailed):
        rw.call("pool.snapshot.create", {"dataset": "simpool/apps", "name": "voor-update-1"})
    with pytest.raises(RpcFailed):
        rw.call("pool.snapshot.clone", {"snapshot": "simpool/apps@onbekend", "dataset_dst": "simbackup/x"})


def test_pool_degraded_and_alerts(client: TestClient, ro: Rpc) -> None:
    assert ro.call("pool.query", [["name", "=", "simpool"]], {"get": True})["healthy"] is True
    client.post("/_sim/faults/pool_degraded", json={"pool": "simpool", "status": "DEGRADED"})
    pool = ro.call("pool.query", [["name", "=", "simpool"]], {"get": True})
    assert pool["status"] == "DEGRADED" and pool["healthy"] is False
    assert any(a["klass"] == "VolumeStatus" for a in ro.call("alert.list"))
    client.delete("/_sim/faults/pool_degraded")
    assert ro.call("pool.query", [["name", "=", "simpool"]], {"get": True})["status"] == "ONLINE"


def test_disks_smart_gpu_tasks(client: TestClient, ro: Rpc) -> None:
    disks = ro.call("disk.query")
    assert len(disks) == 3 and all(d["serial"].startswith("SIM-") for d in disks)
    assert ro.call("smart.test.results", [["disk", "=", "sda"]], {"get": True})["tests"][0]["status"] == "SUCCESS"
    client.post("/_sim/faults/smart_fail", json={"disk": "sdb"})
    assert ro.call("smart.test.results", [["disk", "=", "sdb"]], {"get": True})["tests"][0]["status"] == "FAILED"
    assert any(a["klass"] == "SMART" for a in ro.call("alert.list"))
    gpus = ro.call("app.gpu_choices")
    assert all(g["vendor"] == "INTEL" and "SIM" in g["description"] for g in gpus.values())
    assert ro.call("pool.snapshottask.query")[0]["dataset"] == "simpool/apps"
    assert ro.call("cloudsync.query") == [] and ro.call("replication.query") == []
    client.post("/_sim/fixtures", json={"cloudsync_tasks": [{"id": 1, "description": "sim", "enabled": True}]})
    assert len(ro.call("cloudsync.query")) == 1


def test_api_unreachable(client: TestClient, keys: dict[str, str]) -> None:
    client.post("/_sim/faults/api_unreachable", json={})
    assert client.get("/api/versions").status_code == 503
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/current") as ws:
        ws.receive_text()
    client.delete("/_sim/faults/api_unreachable")
    with client.websocket_connect("/api/current") as ws:
        assert Rpc(ws).login(keys["readonly"])


def test_key_revoked_mid_session(client: TestClient, keys: dict[str, str]) -> None:
    with client.websocket_connect("/api/current") as ws:
        rpc = Rpc(ws)
        rpc.login(keys["readonly"])
        assert rpc.call("app.query") == []
        client.post("/_sim/faults/key_revoked", json={"key_id": 1})
        with pytest.raises(RpcFailed) as exc:
            rpc.call("app.query")
        assert exc.value.errno == 207
        assert rpc.login(keys["readonly"]) is False


def test_insecure_transport_revokes_key(client: TestClient, keys: dict[str, str]) -> None:
    client.post("/_sim/faults/insecure_transport", json={})
    with client.websocket_connect("/api/current") as ws:
        assert Rpc(ws).login(keys["write"]) is False
    client.delete("/_sim/faults/insecure_transport")
    with client.websocket_connect("/api/current") as ws:
        assert Rpc(ws).login(keys["write"]) is False  # blijvend ingetrokken
    assert client.get("/_sim/state").json()["keys"]["2"]["revoked"] is True


def test_slow_responses(client: TestClient, ro: Rpc) -> None:
    client.post("/_sim/faults/slow", json={"delay_ms": 300})
    start = time.monotonic()
    ro.call("pool.query")
    assert time.monotonic() - start >= 0.3


def test_job_fail_leaves_no_app(client: TestClient, rw: Rpc) -> None:
    client.post("/_sim/faults/job_fail", json={"method": "app.create", "error": "pull mislukt (sim)"})
    with pytest.raises(RpcFailed) as exc:
        rw.job("app.create", {"app_name": "x1", "catalog_app": "ix-app", "train": "stable", "version": "1.4.8",
                              "values": ix_values()})
    assert "pull mislukt" in str(exc.value.error)
    job = rw.call("core.get_jobs", [["method", "=", "app.create"]], {"get": True})
    assert job["state"] == "FAILED" and "pull mislukt" in job["error"]


def test_reboot_during_apply(client: TestClient, keys: dict[str, str]) -> None:
    boot = client.get("/api/boot_id").json()
    client.post("/_sim/faults/reboot_during_apply", json={"method": "app.create", "downtime_ms": 200})
    with client.websocket_connect("/api/current") as ws:
        rpc = Rpc(ws)
        rpc.login(keys["write"])
        job_id = rpc.call("app.create", {"app_name": "x2", "catalog_app": "ix-app", "train": "stable",
                                         "version": "1.4.8", "values": ix_values()})
        assert isinstance(job_id, int)
        with pytest.raises(WebSocketDisconnect):
            for _ in range(20):
                ws.receive_text()
    time.sleep(0.3)
    assert client.get("/api/boot_id").json() != boot
    with client.websocket_connect("/api/current") as ws:
        rpc = Rpc(ws)
        rpc.login(keys["readonly"])
        assert rpc.call("core.get_jobs") == []  # job is weg: readback is verplicht
        app = rpc.call("app.query", [["id", "=", "x2"]], {"get": True})
        assert app["state"] == "CRASHED"  # half toegepast


def test_reset_switches_version(client: TestClient) -> None:
    state = client.post("/_sim/reset", json={"version": "25.04.2.6"}).json()
    assert state["version"] == "25.04.2.6" and state["api_versions"][-1] == "v25.04.2"
    assert client.post("/_sim/reset", json={"version": "1.0"}).status_code == 400
    assert client.post("/_sim/faults/onbekend", json={}).status_code == 404
