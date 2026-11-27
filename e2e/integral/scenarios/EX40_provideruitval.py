"""SAB bevestigt afzonderlijke providerstoringen en MSO beschermt de laatste ingeschakelde server."""

from typing import Any

import httpx

from e2e.integral.bewijs import schrijf
from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.mso import eerste_start, koppel, sab_api, sab_client

SCENARIO = {
    "groepen": ("media", "mso"), "diensten": ("mso", "sabnzbd-sim"), "eigenaar": "media-source-optimizer",
    "verwachting": "De autopilot schakelt uitgevallen providers één voor één uit en bewaart de laatste server.",
}


def ingeschakeld(sab: httpx.Client) -> set[str]:
    return {s["name"] for s in sab_api(sab, "get_config", section="servers")["config"]["servers"] if s["enable"]}


def uitval(sab: httpx.Client, naam: str) -> None:
    storing = sab.post("/_sim/serveruitval", json={"server": naam, "uitgevallen": True, "reden": "verlopen"})
    assert storing.raise_for_status().json() == {"uitgevallen": True}


def test_laatste_server(kern: Kern) -> None:
    api = eerste_start(kern, "autopilot")
    sab = sab_client(kern)
    koppel(api, "sabnzbd", sab, {"api_key": sab.params["apikey"]})
    servers = sorted(ingeschakeld(sab))
    labels = {s["name"]: s["displayname"] for s in sab_api(sab, "get_config", section="servers")["config"]["servers"]}
    assert len(servers) == 3
    stappen: list[dict[str, Any]] = []
    for i, naam in enumerate(servers):
        uitval(sab, naam)
        ronde = api.post("usenet/autopilot/run").raise_for_status().json()["servers"]
        assert ronde["autopilot"] is True
        actief = ingeschakeld(sab)
        if i < len(servers) - 1:
            assert ronde["switched_off"] == [naam] and actief == set(servers[i + 1:])
            over = sab_api(sab, "fullstatus")["status"]["servers"]
            assert {s["servername"] for s in over if s["serveractive"] and not s["servererror"]} == {
                labels[n] for n in actief}
        else:
            assert ronde["kept_last"] == [naam] and ronde["switched_off"] == [] and actief == {naam}
        stappen.append({"storing": i + 1, "ingeschakeld": len(actief),
                        "uitgeschakeld_in_ronde": len(ronde["switched_off"]),
                        "laatste_beschermd": naam in ronde["kept_last"]})
    schrijf(kern.net.dossier / "mso.json", {"servers_vooraf": len(servers), "stappen": stappen})
