"""Een categoriecorrectie wordt zichtbaar in de diagnose en wacht op een expliciet akkoord."""

from typing import Any

import httpx

from e2e.integral.bewijs import schrijf
from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.mso import eerste_start, koppel, sab_api, sab_client, toepassen

SCENARIO = {
    "groepen": ("media", "mso"), "diensten": ("mso", "sabnzbd-sim", "mg-categorie-sim"),
    "eigenaar": "media-source-optimizer",
    "verwachting": "MSO verklaart de terugval op * en schrijft pas na akkoord precies één categorie in SABnzbd.",
}

CATEGORIE = "tv-anime"
LEESMODI = {"version", "status", "fullstatus", "warnings", "get_config", "get_cats", "server_stats", "get_files"}


def schrijfacties(log: list[dict[str, Any]]) -> list[dict[str, str]]:
    acties = []
    for regel in log:
        details = regel["details"]
        if regel["pad"] == "/api" and (details.get("mode") in LEESMODI
                                       or (details in ({"mode": "queue"}, {"mode": "history"}))):
            continue
        acties.append(details)
    return acties


def voeg_taak_toe(sab: httpx.Client) -> str:
    taak = sab_api(sab, "addurl", name="http://sabnzbd-sim/proef.nzb", cat=CATEGORIE)
    assert taak["status"] is True
    return str(taak["nzo_ids"][0])


def wachtrij(sab: httpx.Client) -> dict[str, str]:
    return {t["nzo_id"]: t["cat"] for t in sab_api(sab, "queue")["queue"]["slots"]}


def test_wijziging_na_akkoord(kern: Kern) -> None:
    api = eerste_start(kern, "manage")
    sab = sab_client(kern, api)
    mg = kern.client("mg-categorie-sim", 8080)
    # Gebruik de bestaande alleen-lezen-fixture uit dezelfde gepinde mockimage.
    sleutel = kern.net.project.compose("exec", "-T", "mg-categorie-sim", "python", "-c",
                                       "from msomocks.mediaguard import API_KEYS; "
                                       "print(next(k for k,v in API_KEYS.items() if v[1]=='viewer'))").strip()
    koppel(api, "mediaguard", mg, {"api_key": sleutel}, auth_method="apikey")

    assert sab_api(sab, "del_config", section="categories", keyword=CATEGORIE)["status"] is True
    categorieen = sab_api(sab, "get_config", section="categories")["config"]["categories"]
    assert CATEGORIE not in {c["name"] for c in categorieen}
    eerste = voeg_taak_toe(sab)
    assert wachtrij(sab)[eerste] == "*"
    begin = len(kern.verzoeklog(sab))

    diagnose = api.get("usenet/categories/diagnosis", params={"refresh": "true"}).raise_for_status().json()
    assert diagnose["verdict"] == "a" and diagnose["sab"]["queue_star"] >= 1
    assert [(m["category"], m["instances"], m["certainty"]) for m in diagnose["missing"]] == [
        (CATEGORIE, ["Anime"], "zeker")]
    voorstel = api.post("usenet/categories/propose", json={"names": [CATEGORIE]}).raise_for_status().json()
    assert voorstel["target"] == "sabnzbd_categories"
    assert [op["path"] for op in voorstel["ops"]] == ["categories/" + CATEGORIE]
    weigering = api.post(f"changes/{voorstel['id']}/apply")
    assert weigering.status_code == 409 and "goedkeuring" in weigering.json()["detail"]
    assert schrijfacties(kern.verzoeklog(sab)[begin:]) == []
    toepassen(api, voorstel)

    daarna = sab_api(sab, "get_config", section="categories")["config"]["categories"]
    assert [c for c in daarna if c["name"] != CATEGORIE] == categorieen
    assert [c["dir"] for c in daarna if c["name"] == CATEGORIE] == [CATEGORIE]
    acties = schrijfacties(kern.verzoeklog(sab)[begin:])
    assert acties == [{"mode": "set_config", "section": "categories"}]
    tweede = voeg_taak_toe(sab)
    rij = wachtrij(sab)
    assert eerste != tweede and (rij[eerste], rij[tweede]) == ("*", CATEGORIE)
    schrijf(kern.net.dossier / "mso.json", {"oorzaak": diagnose["missing"][0]["category"], "instantie": "Anime",
            "zonder_akkoord_http": weigering.status_code, "sab_wijzigingen": acties,
            "eerste_taak": rij[eerste], "volgende_taak": rij[tweede]})
