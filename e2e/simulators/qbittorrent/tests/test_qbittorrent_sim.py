"""Toestandstabel van private torrent, aanmelding en foutinjectie."""

import json

import pytest
from fastapi.testclient import TestClient
from qbittorrent_sim import create_app


@pytest.fixture
def client(mso_bron):
    with TestClient(create_app()) as sim:
        yield sim


def test_private_tracker_voorkeuren_en_seedbeleid_zijn_meetbaar_en_schrijfbaar(client):
    credentials = client.get("/_sim/sleutels").json()
    assert client.post("/api/v2/auth/login", data=credentials).text == "Ok."
    torrents = client.get("/api/v2/torrents/info").json()
    private = [t for t in torrents if client.get("/api/v2/torrents/properties",
                                                  params={"hash": t["hash"]}).json()["is_private"]]
    assert len(private) == 1
    infohash = private[0]["hash"]
    trackers = client.get("/api/v2/torrents/trackers", params={"hash": infohash}).json()
    assert {t["url"] for t in trackers if t["status"] == 0} >= {"** [DHT] **", "** [PeX] **"}
    prefs = client.get("/api/v2/app/preferences").json()
    assert prefs["dht"] is True and prefs["pex"] is True
    assert client.post("/api/v2/app/setPreferences", data={"json": json.dumps({"dht": False, "pex": False})}).is_success
    prefs = client.get("/api/v2/app/preferences").json()
    assert prefs["dht"] is False and prefs["pex"] is False
    assert client.post("/api/v2/torrents/setShareLimits", data={
        "hashes": infohash, "ratioLimit": 2.5, "seedingTimeLimit": 60, "inactiveSeedingTimeLimit": 17,
    }).is_success
    item = client.get("/api/v2/torrents/info", params={"hashes": infohash}).json()[0]
    assert (item["ratio_limit"], item["seeding_time_limit"], item["inactive_seeding_time_limit"]) == (2.5, 60, 17)
    log = client.get("/_sim/verzoeken").text
    assert credentials["password"] not in log and client.cookies["SID"] not in log
    assert infohash not in log and trackers[-1]["url"] not in log
    assert client.post("/_sim/reset").is_success
    assert client.get("/api/v2/app/preferences").status_code == 403


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer ongeldig"}, {"Cookie": "SID=ongeldig"}])
def test_qb_weigert_zonder_geldige_sessie(client, headers):
    assert client.get("/api/v2/app/preferences", headers=headers).status_code == 403


@pytest.mark.parametrize("fault", ["http_503", "traag", "ongeldige_json"])
def test_qb_foutinjectie(client, foutproef, fault):
    assert client.post("/api/v2/auth/login", data=client.get("/_sim/sleutels").json()).text == "Ok."
    foutproef(client, "/api/v2/app/preferences", {}, fault)
