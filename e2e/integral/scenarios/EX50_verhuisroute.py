"""EX50. Beide hosts behouden hun gegevens tijdens de verhuispreview van een beschermde bron-app."""

from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.hubveiligheid import meet, schrijfacties
from e2e.integral.scenarios.opslag import beheerheaders, plaats_proefmedia

SCENARIO = {
    "groepen": ("opslag",), "diensten": ("truenas-twee", "emby-opslag", "mg-inhoud", "arr-inhoud"),
    "eigenaar": "jelly-leon-platform", "soort": "Sim+",
    "verwachting": "Emby blijft staan bij verhuizen en verwijderen. Beide simulatoren behouden hun gegevens.",
    "blokkade": "NIET GEVALIDEERD (echte NAS)",
    "toelichting": "NIET GEVALIDEERD (echte NAS): herstel op een tweede host (N-P6-14).",
}


def test_verhuisvoorwaarde(kern: Kern) -> None:
    kern.net.ca["truenas-twee"] = kern.net.project.compose("exec", "-T", "truenas-twee", "cat", "/certificaat/sim.crt")
    tweede = kern.client("truenas-twee", 8080, tls=True)
    wacht("tweede simulator", lambda: tweede.get("/api/versions").is_success)
    boot_ids = [c.get("/api/boot_id").raise_for_status().json() for c in (kern.sim, tweede)]
    assert boot_ids[0] != boot_ids[1]
    media = plaats_proefmedia(kern)
    data = [c.get("/_sim/inhoud").raise_for_status().json() for c in (kern.sim, tweede)]
    for naam in ("move", "uninstall"):
        pad = f"/lifecycle/v1/plans/{naam}/preview"
        assert kern.lifecycle.post(pad, json={}).status_code == 401
        body = {"app_name": "emby"}
        antwoord = kern.lifecycle.post(pad, json=body, headers=beheerheaders(kern, "jelly-leon-platform"))
        if naam == "uninstall":
            assert antwoord.status_code == 403 and antwoord.json()["details"]["reason"] == "emby_nooit_door_platform"
        else:
            assert antwoord.status_code == 200
            plan = antwoord.json()
            assert (plan["status"], plan["reden"], plan["stappen"]) == ("laten_staan", "emby_nooit_door_platform", [])
            assert plan["herstelkaart"] is None
        media.meet(naam + "_bron_behouden")
    assert data == [c.get("/_sim/inhoud").raise_for_status().json() for c in (kern.sim, tweede)]
    media.vervolg()
    assert schrijfacties(kern) == []
    assert not any(r["write"] for r in tweede.get("/_sim/calls").raise_for_status().json())
    meet(kern, simulatoren=2, anoniem_http=401, verhuispreview_http=200, verwijderpreview_http=403, schrijfacties=0)
