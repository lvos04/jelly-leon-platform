"""Toestandstabel van de aanvraag tot de gekozen testinstantie en het bestand."""

import pytest
from arr_sim import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    with TestClient(create_app()) as sim:
        yield sim


def key(client, instance="seerr"):
    return {"X-Api-Key": client.get("/_sim/sleutels").json()[instance]}


@pytest.mark.parametrize(("media", "server", "target", "series_type"), [
    (101, None, "sonarr-series", "standard"),
    (102, 1, "sonarr-anime", "anime"),
    (102, None, "sonarr-series", "anime"),  # Seerr kiest anime alleen bij expliciete serverkeuze.
])
def test_seerr_aanvraag_komt_bij_geselecteerde_sonarr(client, media, server, target, series_type):
    body = {"mediaType": "tv", "mediaId": media, "seasons": [1]}
    if server is not None:
        body["serverId"] = server
    headers = key(client)
    settings = client.get("/api/v1/settings/sonarr", headers=headers).json()
    profile = settings[server or 0]["activeProfileId"]
    result = client.post("/api/v1/request", headers=headers, json=body)
    assert result.status_code == 201
    request = result.json()
    assert request["status"] == 2
    path = f"/{target}/api/v3"
    item = client.get(f"{path}/series/{request['media']['externalServiceId']}", headers=key(client, target)).json()
    assert item["seriesType"] == series_type and item["qualityProfileId"] == profile
    assert item["seasons"][0]["monitored"] is True
    assert request["media"]["serviceId"] == (server or 0)
    assert client.get("/api/v1/request/1", headers=headers).json()["id"] == request["id"]
    page = client.get("/api/v1/request?take=1&skip=1&filter=all&sort=added", headers=headers).json()
    assert page["results"] == [] and page["pageInfo"]["results"] == 1
    writes = [v["pad"] for v in client.get("/_sim/verzoeken").json() if v["methode"] == "POST"]
    assert writes == ["/api/v1/request", f"{path}/series"]
    if target == "sonarr-anime":
        profiles = client.get(f"{path}/qualityprofile", headers=key(client, target)).json()
        assert next(p for p in profiles if p["id"] == profile)["formatItems"][0]["score"] > 0
        assert client.get(f"{path}/customformat", headers=key(client, target)).json()[0]["name"] == "Dual Audio"


def test_seerr_aanvraag_gebruikt_radarr_voor_een_film(client):
    result = client.post("/api/v1/request", headers=key(client), json={"mediaType": "movie", "mediaId": 103})
    assert result.status_code == 201
    movies = client.get("/radarr-films/api/v3/movie", headers=key(client, "radarr-films")).json()
    assert [m["tmdbId"] for m in movies] == [103]
    assert client.get("/radarr-animefilms/api/v3/movie", headers=key(client, "radarr-animefilms")).json() == []


def test_dubbele_aanvraag_geeft_geen_tweede_serie(client):
    data = {"mediaType": "tv", "mediaId": 101, "seasons": [1]}
    assert client.post("/api/v1/request", headers=key(client), json=data).status_code == 201
    assert client.post("/api/v1/request", headers=key(client), json=data).status_code == 202
    assert len(client.get("/sonarr-series/api/v3/series", headers=key(client, "sonarr-series")).json()) == 1


def test_geweigerde_route_schrijft_geen_aanvraag(client):
    data = {"mediaType": "tv", "mediaId": 102, "seasons": [1], "serverId": 99}
    assert client.post("/api/v1/request", headers=key(client), json=data).is_error
    assert client.get("/api/v1/request", headers=key(client)).json()["pageInfo"]["results"] == 0


def test_toestand_en_sleutels_na_reset(client):
    old = key(client)
    assert client.post("/api/v1/request", headers=old, json={"mediaType": "movie", "mediaId": 103}).is_success
    assert client.post("/_sim/reset").is_success
    assert client.get("/_sim/verzoeken").json() == []
    assert client.get("/api/v1/request", headers=old).status_code == 403
    assert client.get("/api/v1/request", headers=key(client)).json()["results"] == []


@pytest.mark.parametrize(("path", "status"), [
    ("/api/v1/request", 403), ("/sonarr-series/api/v3/series", 401), ("/radarr-films/api/v3/movie", 401),
])
@pytest.mark.parametrize("wrong", [False, True], ids=["ontbreekt", "verkeerd"])
def test_sleutel_geweigerd(client, path, status, wrong):
    result = client.get(path, headers={"X-Api-Key": "ongeldig"} if wrong else {})
    assert result.status_code == status
    if status == 401:
        assert result.content == b""
    else:
        assert result.json() == {"status": 403, "error": "You do not have permission to access this endpoint"}


def test_sleutel_van_andere_instantie_geweigerd(client):
    headers = key(client, "sonarr-series")
    assert client.get("/sonarr-anime/api/v3/series", headers=headers).status_code == 401
    assert client.get("/sonarr-series/api/v3/series?apikey=ongeldig", headers=headers).status_code == 401
    assert client.get("/sonarr-series/api/v3/series",
                      params={"apikey": headers["X-Api-Key"]}).status_code == 200
    assert headers["X-Api-Key"] not in client.get("/_sim/verzoeken").text


def test_absolute_en_scene_nummering_houden_bestandskoppeling_na_verversen(client):
    request = client.post("/api/v1/request", headers=key(client), json={
        "mediaType": "tv", "mediaId": 102, "seasons": [1], "serverId": 1,
    }).json()
    sid = request["media"]["externalServiceId"]
    headers = key(client, "sonarr-anime")
    root = "/sonarr-anime/api/v3"
    episodes = client.get(f"{root}/episode", headers=headers, params={"seriesId": sid}).json()
    files = client.get(f"{root}/episodefile", headers=headers, params={"seriesId": sid}).json()
    assert len(episodes) == len(files) == 2
    assert {e["absoluteEpisodeNumber"] for e in episodes} == {13, 14}
    assert all(e["absoluteEpisodeNumber"] != e["sceneAbsoluteEpisodeNumber"] for e in episodes)
    assert {e["episodeFileId"] for e in episodes} == {f["id"] for f in files}
    assert len({f["path"] for f in files}) == 2
    cmd = client.post(f"{root}/command", headers=headers, json={"name": "RefreshSeries", "seriesId": sid})
    assert cmd.status_code == 201
    assert client.get(f"{root}/command/{cmd.json()['id']}", headers=headers).json()["status"] == "completed"
    after = client.get(f"{root}/episode", headers=headers, params={"seriesId": sid}).json()
    assert [e["id"] for e in after] != [e["id"] for e in episodes]
    assert {e["id"]: e["episodeFileId"] for e in after} == {e["id"]: e["episodeFileId"] for e in episodes}


def test_opvragen_van_onbekende_aanvraag(client):
    assert client.get("/api/v1/request/987", headers=key(client)).status_code == 404


def test_storing_bij_doelinstantie_levert_geen_gelinkte_aanvraag(client):
    client.post("/_sim/fouten", json={"naam": "http_503", "pad": "/sonarr-series/api/v3/series$", "aantal": 1})
    result = client.post("/api/v1/request", headers=key(client), json={"mediaType": "tv", "mediaId": 101,
                                                                      "seasons": [1]})
    assert result.is_error
    assert client.get("/api/v1/request", headers=key(client)).json()["results"] == []


def test_zoeken_en_ophalen_gaat_naar_sab(mso_bron):
    import httpx
    from sabnzbd_sim import create_app as sab_app

    sab = sab_app()
    with TestClient(sab) as sab_client:
        sab_key = sab_client.get("/_sim/sleutels").json()["apikey"]
        sim = create_app(sab_url="http://sabnzbd", sab_key=sab_key, sab_transport=httpx.ASGITransport(app=sab))
        with TestClient(sim) as client:
            req = client.post("/api/v1/request", headers=key(client), json={
                "mediaType": "tv", "mediaId": 101, "seasons": [1],
            }).json()
            sid = req["media"]["externalServiceId"]
            headers = key(client, "sonarr-series")
            path = "/sonarr-series/api/v3"
            params = {"seriesId": sid, "seasonNumber": 1}
            release = client.get(f"{path}/release", params=params, headers=headers).json()[0]
            body = {"guid": release["guid"], "indexerId": release["indexerId"]}
            assert client.post(f"{path}/release", headers=headers, json=body).is_success
            queue = client.get(f"{path}/queue", headers=headers).json()
            assert queue["totalRecords"] == 1
            sab_queue = sab_client.get("/api", params={"mode": "queue", "apikey": sab_key}).json()
            assert queue["records"][0]["downloadId"] in {s["nzo_id"] for s in sab_queue["queue"]["slots"]}
            assert client.post(f"{path}/release", headers=headers, json={**body, "guid": "onbekend"}).is_error


@pytest.mark.parametrize("fault", ["http_503", "traag", "ongeldige_json"])
def test_arr_foutinjectie(client, foutproef, fault):
    foutproef(client, "/api/v1/request", key(client), fault)


def test_seerr_controleert_sleutel_en_bereikbaarheid_van_de_doelinstantie(client):
    headers = key(client)
    server = client.get("/api/v1/settings/sonarr", headers=headers).json()[1]
    body = {k: server[k] for k in ("hostname", "port", "baseUrl", "useSsl", "apiKey")}
    path = "/api/v1/settings/sonarr/test"
    ok = client.post(path, headers=headers, json=body)
    assert ok.is_success and ok.json()["profiles"][0]["id"] == server["activeAnimeProfileId"]
    assert client.post(path, headers=headers, json={**body, "apiKey": "ongeldig"}).status_code == 500
    client.post("/_sim/fouten", json={"naam": "http_503", "pad": "/sonarr-anime/api/v3/qualityprofile", "aantal": 1})
    assert client.post(path, headers=headers, json=body).status_code == 500


def test_zoekopdracht_zonder_downloadclient_wordt_geen_voltooide_download(client):
    instance = "sonarr-series"
    headers = key(client, instance)
    req = client.post("/api/v1/request", headers=key(client), json={"mediaType": "tv", "mediaId": 101, "seasons": [1]})
    sid = req.json()["media"]["externalServiceId"]
    path = f"/{instance}/api/v3"
    cid = client.post(f"{path}/command", headers=headers, json={"name": "SeriesSearch", "seriesId": sid}).json()["id"]
    assert client.get(f"{path}/command/{cid}", headers=headers).json()["status"] == "failed"
    assert client.get(f"{path}/queue", headers=headers).json()["totalRecords"] == 0
    assert client.get(f"{path}/downloadclient", headers=headers).json() == []


@pytest.mark.parametrize("naam", ["arr_sim", "sabnzbd_sim", "qbittorrent_sim"])
def test_start_weigert_een_extern_luisteradres(naam, monkeypatch):
    import importlib
    import sys

    main = importlib.import_module(naam + ".__main__")
    monkeypatch.setattr(sys, "argv", [naam, "--host", "buiten.invalid"])
    monkeypatch.setattr(main, "serve", lambda *a: pytest.fail("Extern luisteradres doorgegeven"))
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 2


def test_staging_herkent_alleen_het_eigen_episodebestand(tmp_path):
    staging = tmp_path / "sonarr-series/De vuurtoren/staging"
    staging.mkdir(parents=True)
    bestand = staging / "S01E01.mkv"
    ander = staging / "bewaard.mkv"
    bestand.write_bytes(b"aflevering een")
    ander.write_bytes(b"bewaren")
    with TestClient(create_app(testbibliotheek=tmp_path)) as sim:
        r = sim.post("/api/v1/request", headers=key(sim), json={"mediaType": "tv", "mediaId": 101, "seasons": [1]})
        assert r.status_code == 201
        basis = "/sonarr-series/api/v3"
        headers = key(sim, "sonarr-series")
        sid = r.json()["media"]["externalServiceId"]
        item, = sim.get(f"{basis}/episodefile", params={"seriesId": sid}, headers=headers).json()
        pad = f"{basis}/episodefile/{item['id']}"
        assert item["size"] == bestand.stat().st_size and item["path"] == str(bestand)
        assert sim.delete(pad).status_code == 401
        assert sim.delete(f"{basis}/episodefile/999", headers=headers).status_code == 404
        assert bestand.read_bytes() == b"aflevering een"
        assert sim.delete(pad, headers=headers).status_code == 200
        assert not bestand.exists() and sim.get(pad, headers=headers).status_code == 404
        assert ander.read_bytes() == b"bewaren"
        wis = [v for v in sim.get("/_sim/verzoeken").json() if v["methode"] == "DELETE" and v["aanmelding"]]
        assert set(wis[-1]["details"]) == {"bron_sha256"}  # de waarnemer verzint geen voorafgaande kopie
