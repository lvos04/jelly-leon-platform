from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient

from truenas_sim.server import SimServer, create_app


class RpcFailed(Exception):
    def __init__(self, error: dict[str, Any]) -> None:
        super().__init__(error.get("message"))
        self.error = error

    @property
    def code(self) -> int:
        return int(self.error["code"])

    @property
    def errno(self) -> int | None:
        return (self.error.get("data") or {}).get("error")

    @property
    def reason(self) -> str:
        return str((self.error.get("data") or {}).get("reason", ""))


class Rpc:
    """Minimale JSON-RPC-client over de TestClient-websocket (bewaart notificaties)."""

    def __init__(self, ws: Any) -> None:
        self.ws = ws
        self.ids = itertools.count(1)
        self.notifications: list[dict[str, Any]] = []

    def raw(self, payload: Any) -> dict[str, Any]:
        self.ws.send_text(payload if isinstance(payload, str) else json.dumps(payload))
        return self._next_reply()

    def _next_reply(self) -> dict[str, Any]:
        while True:
            msg = json.loads(self.ws.receive_text())
            if "method" in msg and "id" not in msg:
                self.notifications.append(msg)
                continue
            return dict(msg)

    def call(self, method: str, *params: Any) -> Any:
        ident = next(self.ids)
        reply = self.raw({"jsonrpc": "2.0", "id": ident, "method": method, "params": list(params)})
        assert reply["id"] == ident
        if "error" in reply:
            raise RpcFailed(reply["error"])
        return reply["result"]

    def job(self, method: str, *params: Any) -> Any:
        job_id = self.call(method, *params)
        assert isinstance(job_id, int)
        return self.call("core.job_wait", job_id)

    def login(self, key: str) -> bool:
        return bool(self.call("auth.login_with_api_key", key))


@pytest.fixture
def server() -> SimServer:
    return SimServer("25.10.8")


@pytest.fixture
def client(server: SimServer) -> Iterator[TestClient]:
    with TestClient(create_app(server)) as c:
        yield c


@pytest.fixture
def keys(client: TestClient) -> dict[str, str]:
    return dict(client.get("/_sim/keys").json())


@pytest.fixture
def ro(client: TestClient, keys: dict[str, str]) -> Iterator[Rpc]:
    with client.websocket_connect("/api/current") as ws:
        rpc = Rpc(ws)
        assert rpc.login(keys["readonly"])
        yield rpc


@pytest.fixture
def rw(client: TestClient, keys: dict[str, str]) -> Iterator[Rpc]:
    with client.websocket_connect("/api/current") as ws:
        rpc = Rpc(ws)
        assert rpc.login(keys["write"])
        yield rpc


def ix_values(tag: str = "0.1.0", host_port: int = 30440, repo: str = "ghcr.io/lvos04/jelly-leon-platform",
              host_path: str | None = "/mnt/simpool/apps") -> dict[str, Any]:
    storage: list[dict[str, Any]] = []
    if host_path:
        storage.append({"type": "host_path", "mount_path": "/appdata", "read_only": False,
                        "host_path_config": {"acl_enable": False, "path": host_path}})
    return {
        "image": {"repository": repo, "tag": tag, "pull_policy": "missing"},
        "TZ": "Etc/UTC", "envs": [], "restart_policy": "unless-stopped", "disable_builtin_healthcheck": False,
        "privileged": False, "capabilities": {"add": []}, "run_as_custom_user": False, "host_network": False,
        "ports": [{"bind_mode": "published", "port_number": host_port, "container_port": 30440, "protocol": "tcp",
                   "host_ips": []}],
        "storage": storage,
        "resources": {"enable_resource_limits": True, "limits": {"cpus": 1, "memory": 512}, "gpus": {}},
    }
