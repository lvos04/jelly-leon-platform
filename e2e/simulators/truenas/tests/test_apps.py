"""Apps: standaard Custom App (ix-app), catalogusapps, update/rollback/delete en het compose-verbod."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import Rpc, RpcFailed, ix_values
from starlette.testclient import TestClient


def create_custom(rw: Rpc, name: str = "jelly-leon-platform", **kw: Any) -> Any:
    return rw.job("app.create", {"app_name": name, "catalog_app": "ix-app", "train": "stable", "version": "1.4.8",
                                 "values": ix_values(**kw)})


def test_standard_custom_app_create_query_ports(rw: Rpc) -> None:
    app = create_custom(rw)
    assert app["state"] == "RUNNING" and app["custom_app"] is False and app["metadata"]["name"] == "ix-app"
    assert app["active_workloads"]["images"] == ["ghcr.io/lvos04/jelly-leon-platform:0.1.0"]
    assert rw.call("app.used_ports") == [30440]
    full = rw.call("app.query", [["id", "=", "jelly-leon-platform"]], {"get": True, "extra": {"retrieve_config": True}})
    assert full["config"]["image"]["tag"] == "0.1.0"
    assert rw.call("app.config", "jelly-leon-platform")["ports"][0]["port_number"] == 30440


def test_port_conflict_fails_job_with_validation(rw: Rpc) -> None:
    create_custom(rw)
    with pytest.raises(RpcFailed) as exc:
        create_custom(rw, name="ander-app")
    assert "port is being used" in str(exc.value.error)
    assert rw.call("app.query", [["id", "=", "ander-app"]]) == []


def test_compose_and_custom_app_true_are_rejected(client: TestClient, rw: Rpc) -> None:
    for payload in ({"app_name": "x", "custom_app": True, "custom_compose_config_string": "services: {}"},
                    {"app_name": "x", "custom_app": False, "catalog_app": "ix-app",
                     "custom_compose_config": {"services": {}}}):
        with pytest.raises(RpcFailed) as exc:
            rw.call("app.create", payload)
        assert "SIM-BELEID" in exc.value.reason
    create_custom(rw)
    with pytest.raises(RpcFailed):
        rw.call("app.update", "jelly-leon-platform", {"custom_compose_config_string": "x"})
    assert {v["code"] for v in client.get("/_sim/violations").json()} == {"custom_compose_config"}


def test_latest_and_privileged_are_recorded_as_violations(client: TestClient, rw: Rpc) -> None:
    values = ix_values(tag="latest")
    values["privileged"] = True
    rw.job("app.create", {"app_name": "slecht", "catalog_app": "ix-app", "train": "stable", "version": "latest",
                          "values": values})
    codes = {v["code"] for v in client.get("/_sim/violations").json()}
    assert {"image_tag_latest", "privileged", "version_latest"} <= codes


def test_invalid_name_and_missing_host_path(rw: Rpc) -> None:
    with pytest.raises(RpcFailed) as exc:
        rw.call("app.create", {"app_name": "Ongeldig_Naam", "catalog_app": "ix-app", "values": ix_values()})
    assert exc.value.code == -32602
    with pytest.raises(RpcFailed) as exc:
        create_custom(rw, host_path="/mnt/simpool/bestaat-niet")
    assert "not a dataset" in str(exc.value.error)


def test_update_is_shallow_merge_and_tag_change(rw: Rpc) -> None:
    create_custom(rw)
    # alleen {"image": {"tag": …}} sturen wist repository (ondiepe merge, zoals crud.py) → validatiefout
    with pytest.raises(RpcFailed) as exc:
        rw.job("app.update", "jelly-leon-platform", {"values": {"image": {"tag": "0.2.0"}}})
    assert "repository" in str(exc.value.error)
    image = {"repository": "ghcr.io/lvos04/jelly-leon-platform", "tag": "0.2.0", "pull_policy": "missing"}
    app = rw.job("app.update", "jelly-leon-platform", {"values": {"image": image}})
    assert app["active_workloads"]["images"] == ["ghcr.io/lvos04/jelly-leon-platform:0.2.0"]
    # app.rollback werkt NIET voor image-tags van een ix-app (alleen tussen catalogusversies)
    assert rw.call("app.rollback_versions", "jelly-leon-platform") == []
    with pytest.raises(RpcFailed) as exc:
        rw.job("app.rollback", "jelly-leon-platform", {"app_version": "1.4.7", "rollback_snapshot": True})
    assert "not available for rollback" in str(exc.value.error)
    # ix-app 'upgrade' wisselt geen versie, haalt alleen images opnieuw op
    assert rw.job("app.upgrade", "jelly-leon-platform", {"app_version": "1.4.8"})["version"] == "1.4.8"


def test_catalog_app_trains_pinned_upgrade_rollback(client: TestClient, rw: Rpc) -> None:
    """Contract 1.2 §2.2: een trein houdt per app alleen de nieuwste versie. Een vastgezette oudere versie verdwijnt dus
    uit de catalogus (``app.create`` faalt → blokkade ``version_unavailable`` in het plan); al geïnstalleerde versies
    blijven lokaal staan en ``app.rollback`` kan er nog naartoe."""
    assert all(a["train"] == "stable" for a in rw.call("app.available"))
    assert not rw.call("app.available", [["name", "=", "immich"]])
    rw.call("catalog.update", {"preferred_trains": ["stable", "community"]})
    available = {a["name"] for a in rw.call("app.available")}
    assert {"authentik", "immich", "uptime-kuma", "gotify", "node-red", "adguard-home"} <= available
    details = rw.call("catalog.get_app_details", "immich", {"train": "community"})
    assert details["latest_version"] == "1.14.62" and list(details["versions"]) == ["1.14.62"]
    with pytest.raises(RpcFailed):
        rw.call("catalog.get_app_details", "bestaat-niet", {"train": "community"})
    app = rw.job("app.create", {"app_name": "immich", "catalog_app": "immich", "train": "community",
                                "version": "1.14.62", "values": {}})
    assert app["version"] == "1.14.62" and app["upgrade_available"] is False
    # nieuwe versie in de trein: de vorige verdwijnt uit de catalogus
    client.post("/_sim/catalog/publish", json={"train": "community", "app": "immich", "version": "1.14.63",
                                               "app_version": "v3.3.7"})
    assert list(rw.call("catalog.get_app_details", "immich", {"train": "community"})["versions"]) == ["1.14.63"]
    with pytest.raises(RpcFailed) as exc:  # vastgezette, verdwenen versie: installeren faalt
        rw.job("app.create", {"app_name": "immich2", "catalog_app": "immich", "train": "community",
                              "version": "1.14.62", "values": {}})
    assert "Version 1.14.62 not found" in str(exc.value.error)
    summary = rw.call("app.upgrade_summary", "immich", {"app_version": "1.14.63"})
    assert summary["upgrade_version"] == "1.14.63"
    up = rw.job("app.upgrade", "immich", {"app_version": "1.14.63", "snapshot_hostpaths": True})
    assert up["version"] == "1.14.63"
    assert rw.call("app.rollback_versions", "immich") == ["1.14.62"]  # lokaal geïnstalleerd, niet in de catalogus
    back = rw.job("app.rollback", "immich", {"app_version": "1.14.62", "rollback_snapshot": True})
    assert back["version"] == "1.14.62" and back["metadata"]["app_version"] == "v3.3.6"


def test_ix_app_version_itself_can_disappear(client: TestClient, rw: Rpc) -> None:
    """Ook de ``ix-app``-versie zelf kan uit de catalogus verdwijnen: een eigen Custom App installeren met de
    geteste ix-app-versie faalt dan; een al geïnstalleerde app blijft werken en bij te werken (``app.update``)."""
    create_custom(rw)
    client.post("/_sim/catalog/publish", json={"train": "stable", "app": "ix-app", "version": "1.4.9"})
    with pytest.raises(RpcFailed) as exc:
        create_custom(rw, name="tweede", host_port=30441)
    assert "Version 1.4.8 not found" in str(exc.value.error)
    image = {"repository": "ghcr.io/lvos04/jelly-leon-platform", "tag": "0.2.0", "pull_policy": "missing"}
    app = rw.job("app.update", "jelly-leon-platform", {"values": {"image": image}})
    assert app["version"] == "1.4.8" and app["upgrade_available"] is False  # ix-app: geen catalogusupgrade


def test_delete_keeps_data_unless_remove_ix_volumes(client: TestClient, rw: Rpc) -> None:
    values = ix_values(host_path=None)
    values["storage"] = [{"type": "ix_volume", "mount_path": "/appdata",
                          "ix_volume_config": {"acl_enable": False, "dataset_name": "appdata"}}]
    rw.job("app.create", {"app_name": "a1", "catalog_app": "ix-app", "train": "stable", "version": "1.4.8",
                          "values": values})
    ds = "simpool/ix-apps/app_mounts/a1/appdata"
    assert ds in client.get("/_sim/state").json()["datasets"]
    assert rw.job("app.delete", "a1", {"remove_ix_volumes": False, "remove_images": False}) is True
    assert ds in client.get("/_sim/state").json()["datasets"]  # data blijft
    rw.job("app.create", {"app_name": "a1", "catalog_app": "ix-app", "train": "stable", "version": "1.4.8",
                          "values": values})
    rw.job("app.delete", "a1", {"remove_ix_volumes": True})
    assert ds not in client.get("/_sim/state").json()["datasets"]
    assert any(v["code"] == "remove_ix_volumes" for v in client.get("/_sim/violations").json())


def test_start_stop_redeploy(rw: Rpc) -> None:
    create_custom(rw)
    assert rw.job("app.stop", "jelly-leon-platform") is None
    assert rw.call("app.query", [["id", "=", "jelly-leon-platform"]], {"get": True})["state"] == "STOPPED"
    assert rw.job("app.start", "jelly-leon-platform")["state"] == "RUNNING"
    assert rw.job("app.redeploy", "jelly-leon-platform")["state"] == "RUNNING"
    with pytest.raises(RpcFailed):
        rw.job("app.start", "bestaat-niet")


def test_preinstalled_existing_apps_fixture(client: TestClient, ro: Rpc) -> None:
    client.post("/_sim/fixtures", json={"apps": [
        {"app_name": "jellyfin", "catalog_app": "ix-app", "version": "1.4.8",
         "values": ix_values(repo="jellyfin/jellyfin", tag="10.11.11", host_port=8096, host_path=None)},
    ]})
    apps = ro.call("app.query")
    assert [a["name"] for a in apps] == ["jellyfin"] and apps[0]["config"] is None


def test_job_events_via_subscription(rw: Rpc) -> None:
    sub = rw.call("core.subscribe", "core.get_jobs")
    assert isinstance(sub, str)
    create_custom(rw)
    msgs = [n["params"] for n in rw.notifications if n["method"] == "collection_update"]
    assert any(m["msg"] == "added" for m in msgs)
    assert any(m.get("fields", {}).get("state") == "SUCCESS" for m in msgs)
    job = rw.call("core.get_jobs", [["method", "=", "app.create"]], {"get": True})
    assert job["state"] == "SUCCESS" and job["arguments"][0]["values"] == "********"
