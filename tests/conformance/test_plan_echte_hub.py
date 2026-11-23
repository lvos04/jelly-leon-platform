"""S-P2-420: de planner (B08-003, C1.3-JLP-14) tegen de **echte Hub-code** (I10-10-04).

Draait alleen via ``make test-conformance-hub`` (``e2e/conformance/echte_hub.py``): daar zijn Hub-API en -worker echt
en is de TrueNAS de simulator van deze repo, verbonden via de gewone wizardroutes van de Hub. Tegen de Hub-simulator
slaat deze module over (die heeft eigen tests onder ``apps/api/tests``). Sim-getest; NIET GEVALIDEERD (echte NAS).

1. De Hub geeft deze dienst precies de scopes die de planner nodig heeft (anders faalt de proef).
2. Een losse installatiepreview op de gemeten inventaris en catalogusbeschikbaarheid van de Hub: zonder
   catalogusbinding geen uitvoerbare stap (§8.2, R-MANIFEST-1).
3. Een poortconflict met een gemeten app blokkeert het plan; een stapresultaat wordt dan geweigerd.
4. De positieve route: de Hub maakt het plan zelf via ``/api/v1/catalog/plans/install`` (eigen bewijs, binding en
   claim), de planner geeft alleen die planstap vrij; het voorstel van de geregistreerde planstap voert zonder
   goedkeuring niets uit (409, nul schrijfaanroepen) en het stapresultaat zonder readback weigert de planner.
5. De catalogusroute van de Hub weigert een extra, dubbel of ontbrekend queryveld, net als de stand-in (R-MANIFEST-3).
"""

from __future__ import annotations

import json
import os
import ssl
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import uvicorn
from hub_p2 import AVAILABILITY_VORMFOUTEN
from starlette.testclient import TestClient

from lifecycle.app import create_app, default_http_client_factory
from lifecycle.config import Settings
from lifecycle.hubread import BENODIGDE_SCOPES, CATALOG_AVAILABILITY_PATH, ontbrekende_scopes
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


@pytest.fixture(scope="module")
def planner_http(planner: TestClient) -> Iterator[None]:
    """De planner als HTTP-server op de hostpoort die de simulator voor ``jelly-leon-platform`` meldt: zo vindt de Hub
    hem via de gemeten inventaris, zoals in productie. De lifespan liep al in de TestClient."""
    server = uvicorn.Server(uvicorn.Config(planner.app, host="127.0.0.1", port=int(os.environ["JLP_PLANNER_POORT"]),
                                           lifespan="off", log_level="warning"))
    draad = threading.Thread(target=server.run, daemon=True)
    draad.start()
    deadline = time.monotonic() + 20
    while not server.started:
        assert time.monotonic() < deadline and draad.is_alive(), "planner-HTTP-server start niet"
        time.sleep(0.1)
    try:
        yield
    finally:
        server.should_exit = True
        draad.join(10)


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
        assert _precies_benodigd(rij.get("scopes")), rij.get("scopes")


def _precies_benodigd(uitgegeven: Any) -> bool:
    """De uitgegeven verzameling als geheel: niets ontbreekt, niets extra en niets dubbel (R-MANIFEST-6)."""
    return (ontbrekende_scopes(uitgegeven) == [] and len(uitgegeven) == len(BENODIGDE_SCOPES)
            and set(uitgegeven) == BENODIGDE_SCOPES)


def test_een_scope_die_de_hub_niet_uitgeeft_laat_de_proef_falen() -> None:
    """Negatief (zonder Hub): vraagt de dienst een scope die de Hub niet uitgeeft, of geeft de Hub er een te veel of
    dubbel uit, dan is de proef hierboven rood."""
    assert _precies_benodigd(["catalogus.lezen", "inventaris.lezen"])
    assert not _precies_benodigd(["inventaris.lezen", "catalogus.lezen", "acties.voorstellen"])
    assert not _precies_benodigd(["inventaris.lezen", "catalogus.lezen", "catalogus.lezen"])
    assert not _precies_benodigd(["inventaris.lezen"])
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
    # Losse manifest_yaml: alleen een onvertrouwde preview, ook op een verse meting zonder andere blokkade (§8.2).
    assert plan["status"] == "geblokkeerd" and _codes(plan) == {"catalogusbinding_ontbreekt"}, _codes(plan)
    assert plan["plan"]["uitvoering"]["deze_dienst_voert_uit"] is False and plan["vrijgegeven_stappen"] == []
    assert plan["catalog_binding"] is None


def test_poortconflict_met_een_gemeten_app_blokkeert_tegen_de_echte_hub(echt: Any, planner: TestClient) -> None:
    plan = _plan_na_meting(echt, planner, BEZETTE_POORT)
    assert plan["plan"]["bronnen"]["inventaris"]["status"] == "ok"
    assert plan["status"] == "geblokkeerd" and "port_in_use" in _codes(plan), plan["plan"]["blokkades"]
    assert all(s["status"] == "geblokkeerd" for s in plan["voortgang"]) and plan["vrijgegeven_stappen"] == []
    stap = next(s["id"] for s in plan["plan"]["stappen"] if s["soort"] == "actie")
    r = _melden(echt, planner, plan, stap, {"status": "voltooid", "execution_id": str(uuid.uuid4())})
    assert r.status_code == 409 and r.json()["code"] == "plan_geblokkeerd", r.text


def test_catalogusroute_van_de_hub_geeft_de_planstap_vrij_en_zonder_goedkeuring_gebeurt_er_niets(
        echt: Any, hub: httpx.Client, planner: TestClient, planner_http: None) -> None:
    _plan_na_meting(echt, planner, 30215)  # wacht tot de worker van de Hub ix-app 1.4.8 heeft gemeten
    r = hub.post("/api/v1/catalog/plans/install", headers=_idem(echt), json={
        "app_id": os.environ["JLP_CATALOGUS_APP_ID"], "version_id": os.environ["JLP_CATALOGUS_VERSION_ID"],
        "app_name": "demo-app", "parameters": json.loads(os.environ["JLP_CATALOGUS_PARAMETERS"]),
        "doel": json.loads(os.environ["JLP_CATALOGUS_DOEL"])})
    assert r.status_code == 201, r.text
    cplan = r.json()
    assert (cplan["status"], cplan["step_id"]) == ("klaar_voor_voorstel", "installeren"), cplan

    # Bij de planner: precies de binding van de Hub, en alleen deze stap vrijgegeven.
    kop = {"Authorization": f"Bearer {echt.mint_user_jwt()}"}
    plan = planner.get(f"/lifecycle/v1/plans/{cplan['plan_id']}", headers=kop).json()
    binding = plan["catalog_binding"]
    assert set(binding) == {"appstore_service_id", "source_approval_revision", "provenance", "release", "instance"}
    assert binding["instance"]["app_name"] == "demo-app" and binding["instance"]["expected_version"] == "absent"
    assert binding["provenance"]["record"]["version_id"] == os.environ["JLP_CATALOGUS_VERSION_ID"]
    assert plan["etag"] == cplan["plan_etag"] and plan["vrijgegeven_stappen"] == ["installeren"], plan["voortgang"]

    # Het voorstel van de geregistreerde planstap: de Hub neemt het aan, maar zonder goedkeuring gebeurt er niets.
    r = hub.post(f"/api/v1/catalog/plans/{cplan['plan_id']}/steps/{cplan['step_id']}/propose", headers=_idem(echt))
    assert r.status_code == 201, r.text
    aanvraag = r.json()
    rid = aanvraag["request_id"]
    assert aanvraag["risk_class"] in ("R2", "R3") and aanvraag["status"] == "wacht_op_goedkeuring", aanvraag
    voor = _sim_schrijfaanroepen()
    r = hub.post(f"/api/v1/actions/{rid}/execute", headers=_idem(echt))
    assert r.status_code == 409 and r.json()["code"] == "goedkeuring_nodig", r.text
    na = hub.get(f"/api/v1/actions/{rid}").json()
    assert na["status"] not in ("bezig", "geslaagd", "uitgevoerd"), na["status"]
    assert _sim_schrijfaanroepen() == voor, "de NAS-simulator kreeg een schrijfaanroep zonder goedkeuring"
    assert "demo-app" not in _sim_state()["apps"]
    nu = hub.get(f"/api/v1/catalog/plans/{cplan['plan_id']}").json()
    assert nu["status"] == "voorgesteld" and nu["registratie"] is None, nu

    # Zonder uitvoering is er geen readback: de planner weigert het stapresultaat; de stap blijft open.
    r = _melden(echt, planner, plan, "installeren", {"status": "voltooid", "execution_id": rid})
    assert r.status_code == 422 and r.json()["code"] == "readback_ontbreekt", r.text
    later = planner.get(f"/lifecycle/v1/plans/{cplan['plan_id']}", headers=kop).json()
    assert later["etag"] == plan["etag"] and later["voortgang"] == plan["voortgang"]


@pytest.mark.parametrize("vorm", sorted(AVAILABILITY_VORMFOUTEN))
def test_catalogusroute_weigert_een_open_verzoekvorm(echt: Any, planner: TestClient, vorm: str) -> None:
    """Dezelfde lijst als de stand-in (``hub_p2.AVAILABILITY_VORMFOUTEN``): de echte Hub antwoordt 422."""
    token = planner.app.state.hub_link.read().service_token
    with httpx.Client(base_url=echt.base_url, trust_env=False, timeout=10) as c:
        r = c.get(CATALOG_AVAILABILITY_PATH, params=AVAILABILITY_VORMFOUTEN[vorm],
                  headers={"Authorization": f"Bearer {token}"})
    assert (r.status_code, r.json()["code"]) == (422, "validation_error"), r.text


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
