"""Protocol, versies, authenticatie en rechten."""

from __future__ import annotations

import json
from typing import Any

import pytest
from conftest import Rpc, RpcFailed
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from truenas_sim.server import SimServer, create_app


def test_versions_and_current(client: TestClient) -> None:
    versions = client.get("/api/versions").json()
    assert versions[0] == "v25.04.0" and versions[-1] == "v25.10.5"
    with client.websocket_connect("/api/current") as ws:
        assert Rpc(ws).call("core.ping") == "pong"
    with client.websocket_connect("/api/v25.10.5") as ws:
        assert Rpc(ws).call("core.ping") == "pong"
    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/api/v99.0.0") as ws:
        ws.receive_text()


def test_jsonrpc_errors(client: TestClient) -> None:
    with client.websocket_connect("/api/current") as ws:
        rpc = Rpc(ws)
        assert rpc.raw("{niet json")["error"]["code"] == -32700
        assert rpc.raw({"id": 1, "method": "core.ping"})["error"]["code"] == -32600
        assert rpc.raw({"jsonrpc": "2.0", "id": 2, "method": "core.ping", "params": {}})["error"]["code"] == -32600
        r = rpc.raw({"jsonrpc": "2.0", "id": 3, "method": "shell.exec", "params": []})
        assert r["error"] == {"code": -32601, "message": "Method does not exist"}
        with pytest.raises(RpcFailed) as exc:
            rpc.call("system.info")
        assert exc.value.code == -32001 and exc.value.errno == 207  # ENOTAUTHENTICATED
        assert exc.value.error["data"]["errname"] == "ENOTAUTHENTICATED"


def test_login_and_methods_differ_per_version(client: TestClient, keys: dict[str, str]) -> None:
    with client.websocket_connect("/api/current") as ws:
        rpc = Rpc(ws)
        assert rpc.login("1-verkeerd") is False
        assert rpc.login(keys["readonly"]) is True
        methods = rpc.call("core.get_methods")
        assert "pool.snapshot.create" in methods and "zfs.snapshot.create" not in methods
        assert methods["app.create"]["job"] is True and "APPS_WRITE" in methods["app.create"]["roles"]
        assert rpc.call("system.version") == "TrueNAS-25.10.8" and rpc.call("system.info")["version"] == "25.10.8"
        with pytest.raises(RpcFailed) as exc:
            rpc.call("zfs.snapshot.create", {"dataset": "simpool/apps", "name": "x"})
        assert exc.value.code == -32601
    # oudere API-versie op dezelfde NAS → oudere methodenamen
    with client.websocket_connect("/api/v25.04.2") as ws:
        rpc = Rpc(ws)
        rpc.login(keys["readonly"])
        methods = rpc.call("core.get_methods")
        assert "zfs.snapshot.create" in methods and "pool.snapshot.create" not in methods


def test_25_04_profile() -> None:
    with TestClient(create_app(SimServer("25.04.2.6"))) as c:
        assert c.get("/api/versions").json()[-1] == "v25.04.2"
        keys = c.get("/_sim/keys").json()
        with c.websocket_connect("/api/current") as ws:
            rpc = Rpc(ws)
            rpc.login(keys["write"])
            assert rpc.call("system.version_short") == "25.04.2.6"
            snap = rpc.call("zfs.snapshot.create", {"dataset": "simpool/apps", "name": "voor-update"})
            assert snap["id"] == "simpool/apps@voor-update"


def test_readonly_key_cannot_write(ro: Rpc) -> None:
    from conftest import ix_values

    for method, params in [
        ("app.create", [{"app_name": "x", "catalog_app": "ix-app", "train": "stable", "version": "1.4.8",
                         "values": ix_values()}]),
        ("pool.snapshot.create", [{"dataset": "simpool/apps", "name": "x"}]),
        ("catalog.update", [{"preferred_trains": ["stable", "community"]}]),
        ("app.delete", ["x"]),
    ]:
        with pytest.raises(RpcFailed) as exc:
            ro.call(method, *params)
        assert exc.value.errno == 13 and exc.value.reason == "Not authorized", method
    assert ro.call("app.query") == []
    assert isinstance(ro.call("pool.query"), list)


def test_login_ex(client: TestClient, keys: dict[str, str]) -> None:
    with client.websocket_connect("/api/current") as ws:
        rpc = Rpc(ws)
        r = rpc.call("auth.login_ex", {"mechanism": "API_KEY_PLAIN", "username": "executor", "api_key": keys["write"]})
        assert r["response_type"] == "SUCCESS"
        assert rpc.call("auth.me")["pw_name"] == "executor"


def test_calls_log_shows_no_writes_by_readonly(client: TestClient, ro: Rpc) -> None:
    for m in ("pool.query", "app.query", "pool.dataset.query", "alert.list", "disk.query"):
        ro.call(m)
    calls: list[dict[str, Any]] = client.get("/_sim/calls").json()
    assert calls and not any(c["write"] for c in calls if c["user"] == "hub-readonly")
    _ = json


def test_readonly_key_is_refused_every_write_method(client: TestClient, ro: Rpc) -> None:
    """Alle methoden met een schrijfrol (niet uitsluitend *_READ) weigeren de read-only key vóór uitvoering."""
    methods = ro.call("core.get_methods")
    writers = sorted(n for n, m in methods.items() if m["roles"] and not all(r.endswith("_READ") for r in m["roles"]))
    assert {"app.create", "app.update", "app.delete", "app.rollback", "pool.snapshot.rollback",
            "pool.dataset.create", "catalog.update"} <= set(writers)
    for name in writers:
        with pytest.raises(RpcFailed) as exc:
            ro.call(name)
        assert exc.value.errno == 13 and exc.value.reason == "Not authorized", name
    state = client.get("/_sim/state").json()
    assert state["apps"] == {} and state["snapshots"] == []
    assert client.get("/_sim/violations").json() == []


def test_readonly_key_cannot_see_or_abort_executor_jobs(client: TestClient, keys: dict[str, str]) -> None:
    """Zoals middlewared (job.py credential_access_error): zonder FULL_ADMIN alleen eigen jobs zien/afbreken."""
    from conftest import ix_values

    client.post("/_sim/faults/slow", json={"delay_ms": 300})
    with client.websocket_connect("/api/current") as ws_w, client.websocket_connect("/api/current") as ws_r:
        rw, ro = Rpc(ws_w), Rpc(ws_r)
        assert rw.login(keys["write"]) and ro.login(keys["readonly"])
        ro.call("core.subscribe", "core.get_jobs")
        job_id = rw.call("app.create", {"app_name": "lang-bezig", "catalog_app": "ix-app", "train": "stable",
                                        "version": "1.4.8", "values": ix_values()})
        assert ro.call("core.get_jobs") == []
        for method in ("core.job_abort", "core.job_wait"):
            with pytest.raises(RpcFailed) as exc:
                ro.call(method, job_id)
            assert exc.value.errno == 1 and "not owned" in exc.value.reason, method
        assert rw.call("core.job_wait", job_id)["state"] == "RUNNING"
        assert not [n for n in ro.notifications if n["params"]["collection"] == "core.get_jobs"]
    client.delete("/_sim/faults/slow")
