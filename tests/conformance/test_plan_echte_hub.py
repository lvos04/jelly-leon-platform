"""S-P2-420: de planner (B08-003, C1.3-JLP-14) tegen de **echte Hub-code** (I10-10-04).

Draait alleen via ``make test-conformance-hub`` (``e2e/conformance/echte_hub.py``): daar zijn Hub-API en -worker echt
en is de TrueNAS de simulator van deze repo, verbonden via de gewone wizardroutes van de Hub. Tegen de Hub-simulator
slaat deze module over (die heeft eigen tests onder ``apps/api/tests``). Sim-getest; NIET GEVALIDEERD (echte NAS).

1. De Hub geeft deze dienst precies de scopes die de planner nodig heeft (anders faalt de proef).
2. Een installatieplan op de gemeten inventaris en de gemeten catalogusbeschikbaarheid van de Hub.
3. Een poortconflict met een gemeten app blokkeert het plan; een stapresultaat wordt dan geweigerd.
4. Het voorstel uit het plan neemt de Hub aan, maar zonder goedkeuring voert hij niets uit; het stapresultaat zonder
   uitvoering (geen readback) weigert de planner, en de stap die erop wacht komt niet vrij.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.testclient import TestClient

from lifecycle.app import create_app, default_http_client_factory
from lifecycle.config import Settings
from lifecycle.hubread import BENODIGDE_SCOPES, ontbrekende_scopes
from lifecycle.secrets_store import ensure_secrets
from lifecycle.worker import Worker

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = (ROOT / "install" / "manifests" / "gotify.manifest.yaml").read_text(encoding="utf-8")
DOEL = {"truenas_version": "25.10.8", "ix_app_version": "1.4.8"}
BEZETTE_POORT = 38080  # de bestaande simulatorapp van de harnas


def _params(poort: int) -> dict[str, Any]:
    return {"data_path": "/mnt/simpool/apps/gotify", "timezone": "Etc/UTC", "rpo": "P1D", "rto": "PT4H",
            "host_port": poort}


@pytest.fixture(scope="module")
def echt(target: Any) -> Any:
    if target.kind != "echte Hub" or not os.environ.get("JLP_HUB_ENROLLMENT_TOKEN_3"):
        pytest.skip("alleen tegen de echte Hub-code (make test-conformance-hub)")
    return target


@pytest.fixture(scope="module")
def hub(echt: Any) -> Iterator[httpx.Client]:
    with httpx.Client(base_url=echt.base_url, trust_env=False, timeout=30,
                      cookies={"hub_session": echt.session_cookie or ""}) as c:
        yield c


def _idem(echt: Any, key: str | None = None) -> dict[str, str]:
    return {"X-CSRF-Token": echt.csrf or "", "Idempotency-Key": key or str(uuid.uuid4())}


@pytest.fixture(scope="module")
def planner(echt: Any, service_db: str, tmp_path_factory: pytest.TempPathFactory) -> Iterator[TestClient]:
    """De eigen dienst, eenmaal gekoppeld met de echte Hub via de eigen worker (zoals in productie)."""
    tmp_path = tmp_path_factory.mktemp("planner")
    ensure_secrets(tmp_path / "secrets")
    settings = Settings(data_dir=tmp_path, database_url=service_db, hub_url=echt.base_url,
                        enrollment_token=os.environ["JLP_HUB_ENROLLMENT_TOKEN_3"],
                        hub_key_fingerprint=echt.fingerprint, allow_loopback_hub=True)
    worker = Worker(settings, http_client_factory=default_http_client_factory(settings))
    try:
        assert worker.tick()["enrollment"] == "gekoppeld"
        with TestClient(create_app(settings), base_url="http://lifecycle.test") as c:
            yield c
    finally:
        worker.engine.dispose()


def _plan(echt: Any, planner: TestClient, poort: int) -> dict[str, Any]:
    h = {"Authorization": f"Bearer {echt.mint_user_jwt()}", "Idempotency-Key": str(uuid.uuid4())}
    r = planner.post("/lifecycle/v1/plans/install/preview", headers=h,
                     json={"manifest_yaml": MANIFEST, "parameters": _params(poort), "app_name": "gotify", "doel": DOEL})
    assert r.status_code == 201, r.text
    return dict(r.json())


def _plan_na_meting(echt: Any, planner: TestClient, poort: int) -> dict[str, Any]:
    """De worker van de Hub meet de catalogus in zijn eigen ronde; wacht daarop (hooguit 60 s), nooit op een aanname."""
    deadline = time.monotonic() + 60
    while True:
        plan = _plan(echt, planner, poort)
        if plan["plan"]["bronnen"]["catalogus"]["beschikbaar"] != "onbekend" or time.monotonic() > deadline:
            return plan
        time.sleep(2)


def _codes(plan: dict[str, Any]) -> set[str]:
    return {b["code"] for b in plan["plan"]["blokkades"]}


def _melden(echt: Any, planner: TestClient, plan: dict[str, Any], stap: str, body: dict[str, Any]) -> Any:
    h = {"Authorization": f"Bearer {echt.mint_user_jwt()}", "Idempotency-Key": str(uuid.uuid4())}
    return planner.post(f"/lifecycle/v1/plans/{plan['id']}/steps/{stap}/result", headers=h, json=body)


def test_de_hub_geeft_precies_de_scopes_die_de_planner_nodig_heeft(echt: Any, hub: httpx.Client) -> None:
    r = hub.get("/api/v1/services")
    assert r.status_code == 200, r.text
    body = r.json()
    rijen = [d for d in body.get("items", body) if d.get("app_id") == "jelly-leon-platform"]
    assert rijen, "jelly-leon-platform staat niet in het register van de Hub"
    for rij in rijen:
        assert ontbrekende_scopes(rij.get("scopes")) == [], rij.get("scopes")


def test_een_scope_die_de_hub_niet_uitgeeft_laat_de_proef_falen() -> None:
    """Negatief (zonder Hub): vraagt de dienst een scope die de Hub niet uitgeeft, dan is de proef hierboven rood."""
    assert ontbrekende_scopes(["inventaris.lezen"]) == ["catalogus.lezen"]
    assert ontbrekende_scopes([]) == sorted(BENODIGDE_SCOPES)
    for vorm in (None, "inventaris.lezen catalogus.lezen", [1, 2], {"inventaris.lezen": True}):
        assert ontbrekende_scopes(vorm) == sorted(BENODIGDE_SCOPES)


def test_installatieplan_op_gemeten_inventaris_en_catalogus_van_de_echte_hub(echt: Any, planner: TestClient) -> None:
    plan = _plan_na_meting(echt, planner, 30215)
    inv, cat = plan["plan"]["bronnen"]["inventaris"], plan["plan"]["bronnen"]["catalogus"]
    assert inv["status"] == "ok" and inv["bron"] == "hub_snapshot" and inv["aantal_apps"] >= 1, inv
    assert cat == {**cat, "status": "ok", "beschikbaar": "ja", "train": "stable", "app": "ix-app",
                   "version": "1.4.8"}, cat
    assert cat["gemeten_op"] and inv["gemeten_op"]
    assert plan["status"] == "klaar_voor_voorstel" and plan["plan"]["blokkades"] == [], plan["plan"]["blokkades"]
    assert plan["plan"]["uitvoering"]["deze_dienst_voert_uit"] is False and plan["vrijgegeven_stappen"]


def test_poortconflict_met_een_gemeten_app_blokkeert_tegen_de_echte_hub(echt: Any, planner: TestClient) -> None:
    plan = _plan_na_meting(echt, planner, BEZETTE_POORT)
    assert plan["plan"]["bronnen"]["inventaris"]["status"] == "ok"
    assert plan["status"] == "geblokkeerd" and "port_in_use" in _codes(plan), plan["plan"]["blokkades"]
    assert all(s["status"] == "geblokkeerd" for s in plan["voortgang"]) and plan["vrijgegeven_stappen"] == []
    stap = next(s["id"] for s in plan["plan"]["stappen"] if s["soort"] == "actie")
    r = _melden(echt, planner, plan, stap, {"status": "voltooid", "execution_id": str(uuid.uuid4())})
    assert r.status_code == 409 and r.json()["code"] == "plan_geblokkeerd", r.text


def test_voorstel_neemt_de_echte_hub_aan_en_zonder_goedkeuring_gebeurt_er_niets(
        echt: Any, hub: httpx.Client, planner: TestClient) -> None:
    plan = _plan_na_meting(echt, planner, 30215)
    assert plan["status"] == "klaar_voor_voorstel", plan["plan"]["blokkades"]
    stap = next(s for s in plan["plan"]["stappen"]
                if s["soort"] == "actie" and s["voorstel"]["action_id"] == "app.install")
    v = stap["voorstel"]
    voorstel = {k: v[k] for k in ("idempotency_key", "action_id", "target", "parameters", "reason")}
    if v.get("expected_version"):
        voorstel["expected_version"] = v["expected_version"]
    r = hub.post("/api/v1/actions", json=voorstel, headers=_idem(echt, v["idempotency_key"]))
    assert r.status_code == 201, r.text
    aanvraag = r.json()
    rid = aanvraag["request_id"]
    assert aanvraag["risk_class"] in ("R2", "R3") and aanvraag["status"] != "klaar", aanvraag["status"]

    voor = _sim_schrijfaanroepen()
    r = hub.post(f"/api/v1/actions/{rid}/execute", headers=_idem(echt))
    assert r.status_code == 409 and r.json()["code"] in ("goedkeuring_nodig", "actie_geblokkeerd"), r.text
    na = hub.get(f"/api/v1/actions/{rid}").json()
    assert na["status"] not in ("bezig", "geslaagd", "uitgevoerd"), na["status"]
    assert _sim_schrijfaanroepen() == voor, "de NAS-simulator kreeg een schrijfaanroep zonder goedkeuring"
    assert "gotify" not in _sim_state()["apps"]

    # Zonder uitvoering is er geen readback: de planner weigert het stapresultaat en geeft de volgende stap niet vrij.
    r = _melden(echt, planner, plan, stap["id"], {"status": "voltooid", "execution_id": rid})
    assert r.status_code == 422 and r.json()["code"] == "readback_ontbreekt", r.text
    later = [s for s in plan["plan"]["stappen"] if stap["id"] in s["hangt_af_van"]]
    for s in later:
        r = _melden(echt, planner, plan, s["id"], {"status": "voltooid", "execution_id": str(uuid.uuid4())})
        assert r.status_code == 409 and r.json()["code"] == "stap_niet_vrijgegeven", r.text


def _sim() -> httpx.Client:
    ctx = ssl.create_default_context(cafile=os.environ["JLP_TRUENAS_SIM_CA"])
    return httpx.Client(base_url=os.environ["JLP_TRUENAS_SIM_URL"], verify=ctx, trust_env=False, timeout=10)


def _sim_state() -> dict[str, Any]:
    with _sim() as c:
        return dict(c.get("/_sim/state").json())


def _sim_schrijfaanroepen() -> list[str]:
    with _sim() as c:
        calls = c.get("/_sim/calls").json()
    return [json.dumps(x, sort_keys=True) for x in calls
            if str(x.get("method", "")).split(".")[-1] in ("create", "update", "delete", "upgrade", "rollback",
                                                            "start", "stop", "redeploy", "clone")]
