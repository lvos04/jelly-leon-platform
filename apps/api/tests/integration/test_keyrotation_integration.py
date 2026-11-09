"""Integratie §4.5: de worker haalt ondertekende sleutelrotaties op; daarna accepteert de API JWT's met de nieuwe
sleutel. Een ongeldig statement voegt nooit vertrouwen toe. Een Hub zonder het endpoint (404) betekent: geen rotatie."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from lifecycle.config import Settings
from lifecycle.worker import Worker

APP = "jelly-leon-platform"


def worker_for(clean_db: str, data_dir: Path, hub: Any) -> Worker:
    settings = Settings(data_dir=data_dir, database_url=clean_db, hub_url="http://hub.test",
                        key_rotation_poll_seconds=30)
    return Worker(settings, http_client_factory=lambda: TestClient(hub, base_url="http://hub.test"))


def test_worker_applies_rotation_and_api_accepts_new_key(clean_db: str, data_dir: Path, hub: Any,
                                                         make_app: Any) -> None:
    sim = hub.state.sim
    with TestClient(make_app()) as c:
        worker = worker_for(clean_db, data_dir, hub)
        assert worker.tick()["key_rotation"] == "geen"
        new_kid = sim.rotate_key("jwt", retire_old=False)
        worker.next_key_poll_at = 0
        assert worker.tick()["key_rotation"] == "toegepast:1"
        tok = sim.mint("u1", ["viewer"], APP)  # met de nieuwe sleutel ondertekend
        assert c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
        link = c.get("/connector/v1/hub-link", headers={"Authorization": f"Bearer {tok}"}).json()
        assert link["rotation_sequence"] == 1 and new_kid in link["pinned_kids"]["jwt"]
        assert link["keyset_fingerprint"] == sim.keyset_fingerprint()


def test_unknown_kid_poll_in_api_applies_rotation(make_app: Any, hub: Any) -> None:
    sim = hub.state.sim
    with TestClient(make_app()) as c:
        c.app.state.key_poller.run_inline = True
        sim.rotate_key("jwt", retire_old=True)
        tok = sim.mint("u1", ["viewer"], APP)
        r = c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401 and r.json()["details"]["reason"] == "unknown_key"  # wacht niet op de poll
        assert c.app.state.key_poller.last_result == "toegepast:1"
        assert c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {tok}"}).status_code == 200


def test_forged_rotation_adds_no_trust(clean_db: str, data_dir: Path, hub: Any, make_app: Any) -> None:
    sim = hub.state.sim
    with TestClient(make_app()) as c:
        worker = worker_for(clean_db, data_dir, hub)
        sim.rotate_key("jwt", signer=sim.policy_keys[-1])  # verkeerd doel ondertekent
        assert worker.tick()["key_rotation"] == "geweigerd:0"
        tok = sim.mint("u1", ["viewer"], APP)
        assert c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {tok}"}).status_code == 401
        old = sim.mint("u1", ["viewer"], APP, kid=sim.jwt_keys[0][0], key=sim.jwt_keys[0][1])
        link = c.get("/connector/v1/hub-link", headers={"Authorization": f"Bearer {old}"}).json()
        assert link["rotation_sequence"] == 0 and link["last_error"]["code"] == "sleutelrotatie_ongeldig"


def test_hub_without_rotation_endpoint(clean_db: str, data_dir: Path, hub: Any, make_app: Any) -> None:
    hub.state.sim.faults.add("rotations_404")
    with TestClient(make_app()):
        assert worker_for(clean_db, data_dir, hub).tick()["key_rotation"] == "niet_beschikbaar"
