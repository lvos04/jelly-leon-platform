"""Leg de ontbrekende verhuisroute vast vóór er iets naar de tweede simulator kan worden geschreven."""

import pytest

from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.hubveiligheid import meet, schrijfacties
from e2e.integral.scenarios.opslag import beheerheaders, lees_proefmedia, plaats_proefmedia

SCENARIO = {
    "groepen": ("opslag",), "diensten": ("truenas-twee",), "eigenaar": "jelly-leon-platform", "soort": "Sim+",
    "verwachting": "De geauthenticeerde verhuispreview meldt de open bouwstap. Beide simulatoren behouden hun data.",
    "blokkade": "NIET GEVALIDEERD (echte NAS)",
    "toelichting": "NIET GEVALIDEERD: verhuisroute B08-006 nog niet gebouwd (501). Daarna tweede host (N-P6-14).",
    "overslagredenen": ("NIET GEVALIDEERD: verhuisroute B08-006 nog niet gebouwd (501)",),
}


def test_verhuisvoorwaarde(kern: Kern) -> None:
    kern.net.ca["truenas-twee"] = kern.net.project.compose("exec", "-T", "truenas-twee", "cat", "/certificaat/sim.crt")
    tweede = kern.client("truenas-twee", 8080, tls=True)
    wacht("tweede simulator", lambda: tweede.get("/api/versions").is_success)
    boot_ids = [c.get("/api/boot_id").raise_for_status().json() for c in (kern.sim, tweede)]
    assert boot_ids[0] != boot_ids[1]
    plaats_proefmedia(kern)
    data = [c.get("/_sim/inhoud").raise_for_status().json() for c in (kern.sim, tweede)]
    for naam in ("move", "uninstall"):
        pad = f"/lifecycle/v1/plans/{naam}/preview"
        assert kern.lifecycle.post(pad, json={}).status_code == 401
        antwoord = kern.lifecycle.post(pad, json={}, headers=beheerheaders(kern, "jelly-leon-platform"))
        assert antwoord.status_code == 501 and antwoord.json()["code"] == "capability_unavailable"
        assert antwoord.json()["details"]["planned_phase"] == "P2"
    assert data == [c.get("/_sim/inhoud").raise_for_status().json() for c in (kern.sim, tweede)]
    lees_proefmedia(kern, 41, 7)
    assert schrijfacties(kern) == []
    assert not any(r["write"] for r in tweede.get("/_sim/calls").raise_for_status().json())
    meet(kern, simulatoren=2, anoniem_http=401, verhuispreview_http=501, verwijderpreview_http=501, schrijfacties=0)
    pytest.skip(SCENARIO["overslagredenen"][0])
