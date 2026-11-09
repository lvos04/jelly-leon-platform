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
