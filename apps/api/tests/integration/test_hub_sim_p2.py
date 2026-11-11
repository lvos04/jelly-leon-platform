"""De P2-stand-in van de Hub (beleid, goedkeuring, ondertekening; contract 1.3 §8) doet wat de tests ervan verwachten.

Een stand-in die te veel toelaat maakt elke ketentest waardeloos; daarom hier de regels zelf: risicoklasse uit de
contracttabel (nooit uit ``risk_claim``), R2/R3 alleen met een goedkeuring die aan de diff gebonden is, en een geldige
handtekening van de policy-sleutel over canonieke JSON. Sim-getest.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

import contract8 as c8
import pytest
from fastapi.testclient import TestClient
from hub_p2 import P2Error

SLEUTEL = "11111111-1111-4111-8111-111111111111"


def voorstel(action_id: str = "app.restart", app: str = "gotify", **extra: Any) -> dict[str, Any]:
    return {"idempotency_key": str(uuid.uuid4()), "action_id": action_id, "target": {"app_id": app},
            "parameters": {}, "actor": {"type": "service", "id": "jelly-leon-platform"},
            "expected_version": "sha256:" + "0" * 64, **extra}


def publiek(hub: Any) -> list[Any]:
    return [key.public_key() for _kid, key in hub.state.sim.policy_keys]


def test_r1_is_toegestaan_zonder_goedkeuring_en_geldig_ondertekend(hub: Any) -> None:
    sim = hub.state.sim
    verzoek = sim.p2.propose(voorstel())
    assert (verzoek.risk_class, verzoek.status, verzoek.rule_ids) == ("R1", "toegestaan", ["ACT:app.restart"])
    request = sim.execution_request(verzoek.request_id)
    assert c8.vorm_fouten(request) == []
    assert c8.handtekening_geldig(request, publiek(hub))
    assert request["approval"] is None
    assert request["policy_decision"] == {"risk_class": "R1", "rule_ids": ["ACT:app.restart"],
                                          "policy_hash": c8.POLICY_HASH}
    geknoeid = copy.deepcopy(request)
    geknoeid["proposal"]["target"]["app_id"] = "authentik"
    assert not c8.handtekening_geldig(geknoeid, publiek(hub))
    assert not c8.handtekening_geldig(request, [])


def test_kritieke_app_vraagt_goedkeuring_en_risk_claim_telt_nooit_mee(hub: Any) -> None:
    p2 = hub.state.sim.p2
    kritiek = p2.propose(voorstel(app="authentik", risk_claim="R0"))
    assert (kritiek.risk_class, kritiek.status) == ("R2", "wacht_op_goedkeuring")
    assert kritiek.rule_ids == ["ACT:app.restart", "APP:kritiek"]
    gewoon = p2.propose(voorstel(risk_claim="R3"))
    assert gewoon.risk_class == "R1"  # een hogere claim verandert de uitkomst ook niet: alleen de tabel telt


def test_r2_wordt_nooit_uitgegeven_zonder_goedkeuring(hub: Any) -> None:
    sim = hub.state.sim
    verzoek = sim.p2.propose(voorstel("app.uninstall"))
    assert verzoek.status == "wacht_op_goedkeuring"
    with pytest.raises(P2Error) as fout:
        sim.execution_request(verzoek.request_id)
    assert (fout.value.status, fout.value.code) == (409, "goedkeuring_ontbreekt")
    sim.p2.reject(verzoek.request_id)
    with pytest.raises(P2Error) as fout:
        sim.execution_request(verzoek.request_id)
    assert fout.value.code == "goedkeuring_ontbreekt"


def test_goedkeuring_is_gebonden_aan_de_diff(hub: Any) -> None:
    sim = hub.state.sim
    p = voorstel("app.update", parameters={
        "image": {"repository": "ghcr.io/gotify/server", "tag": "3.1.1@sha256:" + "ab" * 32}})
    verzoek = sim.p2.propose(p)
    approval = sim.p2.approve(verzoek.request_id, "eigenaar-7", backup_checkpoint_id="pool-x/apps/gotify@voor-3.1.1")
    assert set(approval) == c8.APPROVAL_KEYS
    assert approval["resource_etag"] == p["expected_version"]
    assert approval["preview_hash"] == c8.preview_hash(p) == verzoek.preview_hash
    assert approval["action_scope"] == {"action_id": "app.update", "target": {"app_id": "gotify"},
                                        "parameters_sha256": c8.sha(p["parameters"])}
    assert (approval["policy_hash"], approval["risk_class"], approval["approved_by"]) == (
        c8.POLICY_HASH, "R2", "eigenaar-7")
    geldig = c8.parse_ts(approval["expires_at"]) - c8.parse_ts(approval["approved_at"])  # type: ignore[operator]
    assert 0 < geldig.total_seconds() <= 900
    request = sim.execution_request(verzoek.request_id)
    assert request["approval"] == approval
    assert c8.vorm_fouten(request) == [] and c8.handtekening_geldig(request, publiek(hub))
    lang = sim.p2.approve(sim.p2.propose(voorstel("app.uninstall")).request_id, ttl_s=86400)
    assert (c8.parse_ts(lang["expires_at"]) - c8.parse_ts(lang["approved_at"])).total_seconds() == 900  # type: ignore[operator]


def test_goedkeuren_van_een_r1_voorstel_kan_niet(hub: Any) -> None:
    p2 = hub.state.sim.p2
    with pytest.raises(P2Error) as fout:
        p2.approve(p2.propose(voorstel()).request_id)
    assert (fout.value.status, fout.value.code) == (409, "goedkeuring_niet_nodig")
    with pytest.raises(P2Error) as fout:
        p2.approve(str(uuid.uuid4()))
    assert fout.value.status == 404


@pytest.mark.parametrize(("wijziging", "status", "code"), [
    ({"action_id": "shell.exec"}, 403, "actie_niet_toegestaan"),
    ({"action_id": "pool.destroy", "target": {"dataset": "pool-x"}}, 403, "actie_niet_toegestaan"),
    ({"parameters": {"grace_seconds": 30}}, 422, "parameters_ongeldig"),
    ({"target": {"app_id": "gotify", "host_id": "nas-twee"}}, 422, "parameters_ongeldig"),
    ({"target": {"app_id": "Gotify; rm -rf /"}}, 422, "parameters_ongeldig"),
    ({"idempotency_key": "geen-uuid"}, 422, "validation_error"),
    ({"expected_version": "laatste"}, 422, "validation_error"),
    ({"actor": {"type": "root", "id": "x"}}, 422, "validation_error"),
    ({"sudo": True}, 422, "validation_error"),
    ({"parameters": {"wacht": 1.5}}, 422, "validation_error"),
])
def test_wat_buiten_de_grenzen_valt_wordt_geweigerd(hub: Any, wijziging: dict[str, Any], status: int,
                                                    code: str) -> None:
    p2 = hub.state.sim.p2
    with pytest.raises(P2Error) as fout:
        p2.propose({**voorstel(), **wijziging})
    assert (fout.value.status, fout.value.code) == (status, code)
    assert p2.verzoeken == {}


@pytest.mark.parametrize("params", [
    {"app_create": {"app_name": "gotify", "custom_app": True, "custom_compose_config_string": "services: {}"},
     "manifest": {"app_id": "gotify"}},
    {"app_create": {"app_name": "gotify", "catalog_app": "ix-app", "train": "stable", "version": "1.4.8", "values": {
        "image": {"repository": "ghcr.io/gotify/server", "tag": "latest"}, "resources": {}}},
     "manifest": {"app_id": "gotify"}},
    {"app_create": {"app_name": "gotify", "catalog_app": "ix-app", "train": "stable", "version": "1.4.8", "values": {
        "image": {"repository": "ghcr.io/gotify/server", "tag": "3.1.1@sha256:" + "ab" * 32}, "resources": {},
        "privileged": True}}, "manifest": {"app_id": "gotify"}},
    {"app_create": {"app_name": "gotify", "catalog_app": "ix-app", "train": "stable", "version": "1.4.8", "values": {
        "image": {"repository": "ghcr.io/gotify/server", "tag": "3.1.1@sha256:" + "ab" * 32}, "resources": {},
        "storage": [{"type": "host_path", "host_path_config": {"path": "/var/run/docker.sock"}}]}},
     "manifest": {"app_id": "gotify"}},
])
def test_installatie_via_yaml_latest_privileged_of_docker_socket_komt_er_niet_door(hub: Any,
                                                                                    params: dict[str, Any]) -> None:
    with pytest.raises(P2Error) as fout:
        hub.state.sim.p2.propose(voorstel("app.install", parameters=params, expected_version="absent"))
    assert fout.value.code == "parameters_ongeldig"


def test_idempotency_zelfde_sleutel_zelfde_voorstel(hub: Any) -> None:
    p2 = hub.state.sim.p2
    p = voorstel(idempotency_key=SLEUTEL)
    eerste = p2.propose(p)
    assert p2.propose(dict(p)) is eerste
    assert len(p2.verzoeken) == 1
    with pytest.raises(P2Error) as fout:
        p2.propose({**p, "action_id": "app.stop"})
    assert (fout.value.status, fout.value.code) == (409, "idempotency_conflict")


def test_expected_version_komt_uit_de_readback_van_de_hub(hub: Any) -> None:
    p2 = hub.state.sim.p2
    p2.etag_lezer = lambda target: "sha256:" + "7" * 64 if target.get("app_id") == "gotify" else "absent"
    assert p2.vul_expected_version({"action_id": "app.restart", "target": {"app_id": "gotify"}})[
        "expected_version"] == "sha256:" + "7" * 64
    assert p2.vul_expected_version({"action_id": "app.install", "target": {"app_id": "nieuw"}})[
        "expected_version"] == "absent"
    assert p2.vul_expected_version({"action_id": "zfs.snapshot.create", "target": {"dataset": "pool-x/apps/gotify"}})[
        "expected_version"] == "dataset:pool-x/apps/gotify"
    assert p2.vul_expected_version({"action_id": "zfs.snapshot.clone_to_sandbox",
                                    "target": {"snapshot": "pool-x/apps/gotify@a"}})[
        "expected_version"] == "snapshot:pool-x/apps/gotify@a"
    vast = {"action_id": "app.restart", "target": {"app_id": "gotify"}, "expected_version": "sha256:" + "1" * 64}
    assert p2.vul_expected_version(vast)["expected_version"] == "sha256:" + "1" * 64


def test_route_voor_een_flow_dwingt_de_actor_af_en_vraagt_de_scope(hub: Any) -> None:
    sim = hub.state.sim
    http = TestClient(hub, base_url="http://hub.test")
    antwoord = http.post("/_sim/p2/services", json={"app_id": "node-red", "scopes": []}).json()
    kop = {"Authorization": f"Bearer {antwoord['service_token']}"}
    sim.p2.etag_lezer = lambda _target: "sha256:" + "5" * 64
    body = {"idempotency_key": SLEUTEL, "action_id": "app.restart", "target": {"app_id": "authentik"},
            "parameters": {}, "actor": {"type": "user", "id": "testflow-herstart"}, "risk_claim": "R0"}
    geen_scope = http.post("/api/v1/actions/proposals", json=body, headers=kop)
    assert (geen_scope.status_code, geen_scope.json()["code"]) == (403, "scope_ontbreekt")
    assert http.post("/api/v1/actions/proposals", json=body).status_code == 401
    http.post("/_sim/p2/scopes", json={"app_id": "node-red", "scopes": ["acties.voorstellen"]})
    gemaakt = http.post("/api/v1/actions/proposals", json=body, headers=kop)
    assert gemaakt.status_code == 201, gemaakt.text
    view = gemaakt.json()
    assert view["actor"] == {"type": "flow", "id": "node-red:testflow-herstart"}  # nooit zelf te kiezen
    assert (view["risk_class"], view["status"], view["goedkeuring_nodig"]) == ("R2", "wacht_op_goedkeuring", True)
    assert sim.p2.verzoeken[view["request_id"]].proposal["expected_version"] == "sha256:" + "5" * 64
    zelfde = http.post("/api/v1/actions/proposals", json=body, headers=kop)
    assert zelfde.json()["request_id"] == view["request_id"]
    assert http.get(f"/api/v1/actions/proposals/{view['request_id']}", headers=kop).json()["status"] == \
        "wacht_op_goedkeuring"
    goedgekeurd = http.post(f"/_sim/p2/verzoeken/{view['request_id']}/goedkeuren", json={"approved_by": "eigenaar-1"})
    assert goedgekeurd.status_code == 200
    request = sim.execution_request(view["request_id"])
    assert request["approval"]["approved_by"] == "eigenaar-1" and c8.handtekening_geldig(request, publiek(hub))
    assert http.get(f"/api/v1/actions/proposals/{view['request_id']}", headers=kop).json()["status"] == "uitgegeven"
    geweigerd = http.post("/api/v1/actions/proposals", headers=kop, json={
        **body, "idempotency_key": str(uuid.uuid4()), "action_id": "exec.run"})
    assert (geweigerd.status_code, geweigerd.json()["code"]) == (403, "actie_niet_toegestaan")
