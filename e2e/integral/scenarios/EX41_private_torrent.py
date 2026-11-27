"""Vergelijk dezelfde DHT- en PeX-wijziging op een publieke en een private testbibliotheek."""

import json
import time
from typing import Any

import httpx

from e2e.integral.bewijs import schrijf
from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.mso import eerste_start, koppel, toepassen

SCENARIO = {
    "groepen": ("media", "mso"), "diensten": ("mso", "qbittorrent-sim", "qbittorrent-publiek"),
    "eigenaar": "media-source-optimizer",
    "verwachting": "Private torrents behouden DHT, PeX en seedbeleid. Dezelfde vlaggen zijn publiek instelbaar.",
}


def bereid_qb_voor(kern: Kern, dienst: str) -> tuple[httpx.Client, dict[str, str]]:
    qb = kern.client(dienst, 30218)
    login = qb.get("/_sim/sleutels").raise_for_status().json()
    assert qb.post("/api/v2/auth/login", data=login).raise_for_status().text == "Ok."
    qb.post("/api/v2/app/setPreferences", data={"json": json.dumps({"dht": False, "pex": False})}).raise_for_status()
    return qb, login


def vlaggen(qb: httpx.Client) -> dict[str, bool]:
    prefs = qb.get("/api/v2/app/preferences").raise_for_status().json()
    return {k: prefs[k] for k in ("dht", "pex")}


def herken(api: httpx.Client, infohash: str, prive: bool) -> None:
    laatste_slot = -1

    def gemeten() -> bool:
        nonlocal laatste_slot
        # De productieroute bundelt handmatige verversingen binnen hetzelfde tijdvak.
        slot = int(time.time()) // 30
        if slot != laatste_slot:
            taak = api.post("torrent/refresh").raise_for_status().json()
            assert taak["job_id"]
            laatste_slot = slot
        items = api.get("torrent/torrents").raise_for_status().json()["items"]
        return any(r["hash"] == infohash and r["private"] is prive for r in items)

    wacht("herkenning van de torrent door de MSO-werker", gemeten, seconden=80)


def prive_toestand(qb: httpx.Client, infohash: str) -> dict[str, Any]:
    tracker = qb.get("/api/v2/torrents/trackers", params={"hash": infohash}).raise_for_status().json()
    torrent, = qb.get("/api/v2/torrents/info", params={"hashes": infohash}).raise_for_status().json()
    limieten = ("ratio_limit", "seeding_time_limit", "inactive_seeding_time_limit")
    return {"vlaggen": vlaggen(qb),
            "trackerstatus": {r["url"]: r["status"] for r in tracker if r["url"] in {"** [DHT] **", "** [PeX] **"}},
            "seedlimieten": {k: torrent[k] for k in limieten}}


def wijzigingen(kern: Kern, qb: httpx.Client, begin: int) -> list[str]:
    return [r["pad"] for r in kern.verzoeklog(qb)[begin:] if r["methode"] != "GET"
            and r["pad"] not in {"/api/v2/auth/login", "/api/v2/auth/logout"}]


def test_private_en_publieke_torrent(kern: Kern) -> None:
    api = eerste_start(kern, "manage")
    publiek, login = bereid_qb_voor(kern, "qbittorrent-publiek")
    koppel(api, "qbittorrent", publiek, login)
    toegevoegd = publiek.post("/api/v2/torrents/add",
                              data={"urls": "http://qbittorrent-publiek/proef", "stopped": "true"})
    assert toegevoegd.raise_for_status().text == "Ok."
    item, = publiek.get("/api/v2/torrents/info").raise_for_status().json()
    assert publiek.get("/api/v2/torrents/properties", params={"hash": item["hash"]}).json()["is_private"] is False
    herken(api, item["hash"], False)
    uit, aan = {"dht": False, "pex": False}, {"dht": True, "pex": True}
    assert vlaggen(publiek) == uit
    begin = len(kern.verzoeklog(publiek))
    route = "torrent/preferences/preview"
    voorstel = api.post(route, json={"changes": aan}).raise_for_status().json()
    assert {op["path"] for op in voorstel["ops"]} == {"pref:dht", "pref:pex"}
    toepassen(api, voorstel)
    assert vlaggen(publiek) == aan
    assert set(wijzigingen(kern, publiek, begin)) == {"/api/v2/app/setPreferences"}

    qb, login = bereid_qb_voor(kern, "qbittorrent-sim")
    prive, = [r for r in qb.get("/api/v2/torrents/info").raise_for_status().json()
              if qb.get("/api/v2/torrents/properties", params={"hash": r["hash"]}).json()["is_private"]]
    h = prive["hash"]
    voor = prive_toestand(qb, h)
    assert voor["vlaggen"] == uit and voor["trackerstatus"] == {"** [DHT] **": 0, "** [PeX] **": 0}
    begin = len(kern.verzoeklog(qb))
    koppel(api, "qbittorrent", qb, login)
    herken(api, h, True)
    regel = {"tracker_host": "tracker.privehd.example", "label": "Proeftracker", "private": True,
             "min_ratio": 1.5, "min_seed_time_h": 72, "mode": "either"}
    assert api.post("torrent/tracker-rules", json=regel).raise_for_status().json()["id"]
    weigeringen = []
    for instelling, waarde, reden in (("dht", True, "private"), ("pex", True, "private"),
                                      ("max_ratio", 0.5, "seedverplichting")):
        antwoord = api.post(route, json={"changes": {instelling: waarde}})
        assert antwoord.status_code == 422 and reden in antwoord.json()["detail"].lower()
        weigeringen.append(antwoord.status_code)
    na = prive_toestand(qb, h)
    assert na == voor and wijzigingen(kern, qb, begin) == [], "De private torrent is gewijzigd na de nulmeting"
    schrijf(kern.net.dossier / "mso.json", {"publiek_voor": uit, "publiek_na": vlaggen(publiek),
            "prive_voor": voor, "prive_na": na, "weigeringen_http": weigeringen,
            "prive_schrijfacties": wijzigingen(kern, qb, begin)})
