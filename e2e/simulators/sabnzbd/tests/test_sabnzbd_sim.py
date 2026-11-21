"""Toestandstabel voor de categorie, providerstatus en de negatieve SAB-proeven."""

import pytest
from fastapi.testclient import TestClient
from sabnzbd_sim import create_app


@pytest.fixture
def client(mso_bron):
    with TestClient(create_app()) as sim:
        yield sim


def api(client, mode, **params):
    apikey = client.get("/_sim/sleutels").json()["apikey"]
    return client.get("/api", params={"apikey": apikey, "output": "json", "mode": mode, **params}).json()


def test_onbekende_categorie_en_teruglezen_na_wijziging(client):
    assert "proef" not in api(client, "get_cats")["categories"]
    first = api(client, "addurl", name="https://download.invalid/eerste.nzb", cat="proef")["nzo_ids"][0]
    api(client, "set_config", section="categories", keyword="proef")
    second = api(client, "addurl", name="https://download.invalid/tweede.nzb", cat="proef")["nzo_ids"][0]
    cats = {s["nzo_id"]: s["cat"] for s in api(client, "queue")["queue"]["slots"]}
    assert (cats[first], cats[second]) == ("*", "proef")
    log = client.get("/_sim/verzoeken").json()
    assert [v["details"] for v in log if v["details"].get("mode") == "set_config"] == [
        {"mode": "set_config", "section": "categories"}]
    assert client.get("/_sim/sleutels").json()["apikey"] not in str(log)


def test_providers_vallen_een_voor_een_uit_en_herstellen(client):
    servers = api(client, "get_config", section="servers")["config"]["servers"]
    names = [s["name"] for s in servers if s["enable"]]
    assert len(names) == 3
    for count in range(3):
        status = api(client, "fullstatus")["status"]["servers"]
        assert sum(s["serveractive"] and not s["servererror"] for s in status) == 3 - count
        if count < 2:
            res = client.post("/_sim/serveruitval", json={"server": names[count], "uitgevallen": True})
            assert res.status_code == 200
    assert client.post("/_sim/serveruitval", json={"server": names[0], "uitgevallen": False}).is_success
    assert sum(not s["servererror"] for s in api(client, "status")["status"]["servers"]) == 2
    assert client.post("/_sim/reset").is_success
    assert all(not s["servererror"] for s in api(client, "status")["status"]["servers"])


def test_onbekende_server_verandert_niets(client):
    before = api(client, "status")
    assert client.post("/_sim/serveruitval", json={"server": "onbekend", "uitgevallen": True}).status_code == 404
    assert api(client, "status") == before


@pytest.mark.parametrize(("params", "message"), [
    ({}, "API Key Required"), ({"apikey": "ongeldig"}, "API Key Incorrect"),
])
def test_sab_weigert_ontbrekende_of_verkeerde_sleutel(client, params, message):
    result = client.get("/api", params={"mode": "queue", **params})
    assert (result.status_code, result.text) == (403, message)


@pytest.mark.parametrize("fault", ["http_503", "traag", "ongeldige_json"])
def test_sab_foutinjectie(client, foutproef, fault):
    foutproef(client, "/api?mode=version", {}, fault)


def test_formulier_met_schrijfmodus_wordt_herkend(client):
    key = client.get("/_sim/sleutels").json()["apikey"]
    data = {"mode": "set_config", "section": "categories", "keyword": "proef", "apikey": key}
    answer = client.post("/api", data=data)
    assert answer.is_success
    log = client.get("/_sim/verzoeken").json()
    assert log[-1]["details"] == {"mode": "set_config", "section": "categories"} and key not in str(log)
    assert "proef" in api(client, "get_cats")["categories"]
