"""JP7 / contract 1.2 §2.2 (B08-003/004): regels van de lifecycle-planner tegen de TrueNAS-simulator (JSON-RPC over
WebSocket). Terugzetten van een Custom App = ``app.update`` met de bewaarde vorige waarden; ``app.rollback`` alleen
voor catalogusapps; digest in het tagveld + readback; ``version_unavailable`` bij een verdwenen catalogusversie.
Sim-getest; op een echte NAS NIET GEVALIDEERD. De plan-routes van de API geven in P0 nog ``capability_unavailable``."""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterator
from typing import Any

import pytest
from starlette.testclient import TestClient

from lifecycle.custom_app import (
    Blocker,
    RecipeError,
    catalog_version_blocker,
    check_digest_readback,
    form_tag,
    rollback_recipe,
    split_form_tag,
)

truenas_sim = pytest.importorskip("truenas_sim.server")
REPO = "ghcr.io/lvos04/jelly-leon-platform"
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64


class Rpc:
    def __init__(self, ws: Any) -> None:
        self.ws, self.ids = ws, itertools.count(1)

    def call(self, method: str, *params: Any) -> Any:
        ident = next(self.ids)
        self.ws.send_text(json.dumps({"jsonrpc": "2.0", "id": ident, "method": method, "params": list(params)}))
        while True:
            msg = json.loads(self.ws.receive_text())
            if msg.get("id") == ident:
                break
        if "error" in msg:
            raise RuntimeError(json.dumps(msg["error"]))
        return msg["result"]

    def job(self, method: str, *params: Any) -> Any:
        return self.call("core.job_wait", self.call(method, *params))


@pytest.fixture
def nas() -> Iterator[tuple[Rpc, TestClient]]:
    sim = truenas_sim.SimServer("25.10.8")
    with TestClient(truenas_sim.create_app(sim)) as client:
        keys = client.get("/_sim/keys").json()
        with client.websocket_connect("/api/current") as ws:
            rpc = Rpc(ws)
            assert rpc.call("auth.login_with_api_key", keys["write"])
            yield rpc, client


def values(tag: str) -> dict[str, Any]:
    return {
        "image": {"repository": REPO, "tag": tag, "pull_policy": "missing"}, "TZ": "Etc/UTC", "envs": [],
        "restart_policy": "unless-stopped", "disable_builtin_healthcheck": False, "privileged": False,
        "capabilities": {"add": []}, "run_as_custom_user": False, "host_network": False,
        "ports": [{"bind_mode": "published", "port_number": 30440, "container_port": 30440, "protocol": "tcp",
                   "host_ips": []}],
        "storage": [{"type": "host_path", "mount_path": "/appdata", "read_only": False,
                     "host_path_config": {"acl_enable": False, "path": "/mnt/simpool/apps"}}],
    }


def test_form_tag_rules() -> None:
    assert form_tag("0.1.0", D1) == f"0.1.0@{D1}" and split_form_tag(f"0.1.0@{D1}") == ("0.1.0", D1)
    assert form_tag("0.1.0", None) == "0.1.0"
    for bad in (("latest", D1), ("0.1.0", "sha256:kort"), ("", None), ("LATEST", None)):
        with pytest.raises(RecipeError):
            form_tag(*bad)
    with pytest.raises(RecipeError):
        split_form_tag("latest@" + D1)


def test_update_then_rollback_custom_app_via_previous_values(nas: tuple[Rpc, TestClient]) -> None:
    rpc, _ = nas
    v1 = values(form_tag("0.1.0", D1))
    rpc.job("app.create", {"app_name": "jelly-leon-platform", "catalog_app": "ix-app", "train": "stable",
                           "version": "1.4.8", "values": v1})
    assert check_digest_readback(repository=REPO, expected_digest=D1, image_rows=rpc.call("app.image.query")).ok
    previous = rpc.call("app.config", "jelly-leon-platform")  # vóór de update bewaren: volledige vorige waarden
    rpc.job("app.update", "jelly-leon-platform", {"values": {"image": {**v1["image"], "tag": form_tag("0.2.0", D2)}}})
    assert check_digest_readback(repository=REPO, expected_digest=D2, image_rows=rpc.call("app.image.query")).ok
    # app.rollback kent geen image-tags van een ix-app: er is niets om naar terug te gaan
    assert rpc.call("app.rollback_versions", "jelly-leon-platform") == []
    recipe = rollback_recipe(app_name="jelly-leon-platform", catalog_app="ix-app", previous_values=previous)
    assert isinstance(recipe, dict) and recipe["method"] == "app.update"
    rpc.job(recipe["method"], *recipe["params"])
    assert check_digest_readback(repository=REPO, expected_digest=D1, image_rows=rpc.call("app.image.query")).ok
    assert rpc.call("app.config", "jelly-leon-platform")["image"]["tag"] == f"0.1.0@{D1}"


def test_digest_mismatch_is_detected(nas: tuple[Rpc, TestClient]) -> None:
    rpc, client = nas
    rpc.job("app.create", {"app_name": "jelly-leon-platform", "catalog_app": "ix-app", "train": "stable",
                           "version": "1.4.8", "values": values(form_tag("0.1.0", D1))})
    client.post("/_sim/faults/digest_mismatch")
    result = check_digest_readback(repository=REPO, expected_digest=D1, image_rows=rpc.call("app.image.query"))
    assert not result.ok and "incident" in result.message_nl and result.observed


def test_missing_previous_values_blocks_rollback() -> None:
    blocked = rollback_recipe(app_name="x", catalog_app="ix-app", previous_values=None)
    assert isinstance(blocked, Blocker) and blocked.code == "rollback_recipe_missing"
    with pytest.raises(RecipeError):  # nooit terug naar 'latest'
        rollback_recipe(app_name="x", catalog_app="ix-app",
                        previous_values={"image": {"repository": REPO, "tag": "latest"}})


def test_catalog_app_rollback_and_version_unavailable(nas: tuple[Rpc, TestClient]) -> None:
    rpc, client = nas
    rpc.call("catalog.update", {"preferred_trains": ["stable", "community"]})
    details = rpc.call("catalog.get_app_details", "immich", {"train": "community"})
    pinned = "1.14.63"
    assert catalog_version_blocker(catalog_app="immich", train="community", pinned_version=pinned,
                                   available_versions=details["versions"]) is None
    rpc.job("app.create", {"app_name": "immich", "catalog_app": "immich", "train": "community", "version": pinned,
                           "values": {}})
    client.post("/_sim/catalog/publish", json={"train": "community", "app": "immich", "version": "1.14.64"})
    details = rpc.call("catalog.get_app_details", "immich", {"train": "community"})
    blocker = catalog_version_blocker(catalog_app="immich", train="community", pinned_version=pinned,
                                      available_versions=details["versions"])
    assert blocker is not None and blocker.code == "version_unavailable" and "nooit stil" in blocker.proposal_nl
    assert blocker.details["available"] == ["1.14.64"]
    rpc.job("app.upgrade", "immich", {"app_version": "1.14.64"})
    recipe = rollback_recipe(app_name="immich", catalog_app="immich", previous_version=pinned,
                             rollback_versions=rpc.call("app.rollback_versions", "immich"))
    assert isinstance(recipe, dict) and recipe["method"] == "app.rollback"
    assert rpc.job(recipe["method"], *recipe["params"])["version"] == pinned
    gone = rollback_recipe(app_name="immich", catalog_app="immich", previous_version="1.0.0", rollback_versions=[])
    assert isinstance(gone, Blocker) and gone.code == "rollback_version_unavailable"


def test_ix_app_version_itself_unavailable(nas: tuple[Rpc, TestClient]) -> None:
    rpc, client = nas
    client.post("/_sim/catalog/publish", json={"train": "stable", "app": "ix-app", "version": "1.4.9"})
    details = rpc.call("catalog.get_app_details", "ix-app", {"train": "stable"})
    blocker = catalog_version_blocker(catalog_app="ix-app", train="stable", pinned_version="1.4.8",
                                      available_versions=details["versions"])
    assert blocker is not None and blocker.code == "version_unavailable" and "vertaling" in blocker.proposal_nl
