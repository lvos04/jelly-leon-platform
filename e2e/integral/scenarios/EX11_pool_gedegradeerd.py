"""Een poolstoring tussen voorstel en uitvoering wordt ook door de executor gemeten."""

from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.hubveiligheid import bezorgingen, meet, oproepen, schrijfacties, vernieuw
from e2e.integral.scenarios.opslag import aanvraag, bereid_opslag_voor, gate, snapshot, voer_uit

SCENARIO = {
    "groepen": ("opslag", "backupvoorwaarden"), "eigenaar": "jelly-leon-platform", "diensten": ("onedrive",),
    "verwachting": "Hub en executor blokkeren de snapshot bij een poolstoring. De Hub weigert mediapurge.",
}


def test_poolstoring_na_voorstel(kern: Kern) -> None:
    bereid_opslag_voor(kern)
    rij = snapshot(kern)
    assert rij["status"] == "klaar" and rij["risk_class"] == "R1" and rij["blokkades"] == []
    begin = len(oproepen(kern))
    kern.sim.post("/_sim/faults/pool_degraded", json={}).raise_for_status()
    # De Hub heeft nog zijn gezonde inventaris. De executor leest de pool vlak vóór de mutatie opnieuw.
    antwoord = voer_uit(kern, rij)
    assert antwoord.status_code == 409 and antwoord.json()["code"] == "uitvoering_geweigerd"
    assert bezorgingen(kern) == [[409]]
    assert any(r["user"] == "executor" and r["method"] == "pool.query" for r in oproepen(kern)[begin:])
    actie = kern.hub.get(f"/api/v1/actions/{rij['request_id']}").raise_for_status().json()
    assert actie["resultaat"]["code"] == "pool_niet_gezond"
    vernieuw(kern)
    pools = kern.hub.get("/api/v1/inventory/pools").raise_for_status().json()
    assert any(p["status"] == "DEGRADED" and p["gezond"] is False for p in pools["items"])
    assert gate(kern, "data_mutatie")["toegestaan"] is False
    geweigerd = voer_uit(kern, snapshot(kern))
    assert geweigerd.status_code == 409 and geweigerd.json()["code"] == "actie_geblokkeerd"
    assert "pool_niet_gezond" in {b["code"] for b in geweigerd.json()["details"]["blokkades"]}
    purge = aanvraag(kern, "media.purge", {}, {})
    assert purge.status_code == 403 and purge.json()["code"] == "actie_geweigerd"
    assert bezorgingen(kern) == [[409]] and schrijfacties(kern) == []
    meet(kern, executor_http=409, hub_http=geweigerd.status_code, mediapurge_http=purge.status_code, schrijfacties=0)
