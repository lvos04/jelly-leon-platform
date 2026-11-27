"""Regressieproeven voor de kijkdienst en taakvoortgang bij opslagherstel."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from e2e.integral.scenarios.opslag import plaats_proefmedia
from truenas_sim.server import create_app


def test_alleen_een_draaiende_app_bewijst_geen_bereikbare_kijkdienst():
    def offline(*_):
        raise httpx.ConnectError("lokale mediaserver onbereikbaar")

    with TestClient(create_app()) as sim:
        kern = SimpleNamespace(sim=sim, client=offline)
        with pytest.raises(httpx.ConnectError):
            plaats_proefmedia(kern)


@pytest.fixture
def diensten(tmp_path):
    from e2e.integral.mediafixtures import kijkserver
    from e2e.integral.scenarios.hubveiligheid import appfixture
    from e2e.integral.scenarios.opslag import INSTANTIE, MEDIA, TUSSENSTAND, Proefmedia, schrijf_inhoud

    storing = {}
    taak = {"id": 17, "job_type": "library.sync", "instance_id": INSTANTIE, "status": "running",
            "progress": TUSSENSTAND, "logs_truncated": False, "logs": [
                {"message": "Bibliotheeksynchronisatie wacht op herstelproef", "ts": "2026-01-01", "level": "info"}]}

    def mg_api(req):
        assert req.url.path == "/api/v1/jobs/17"
        return httpx.Response(storing.get("mg_http", 200), json=taak)

    with TestClient(create_app()) as sim, TestClient(kijkserver.create_app(emby=True)) as kijk:
        def kijk_api(req):
            if storing.get("offline"):
                raise httpx.ConnectError("kijkdienst onderbroken")
            antwoord = kijk.request(req.method, req.url.raw_path.decode(), headers=req.headers, content=req.content)
            rijen = antwoord.json()
            if req.url.path == "/Users":
                if storing.get("rechten"):
                    rijen[1]["Policy"]["EnableMediaPlayback"] = False
                if storing.get("gebruiker"):
                    rijen.pop()
            if req.url.path == "/Items" and req.url.params["UserId"] == "proefkijker-b":
                if veld := storing.get("veld"):
                    del rijen["Items"][0]["UserData"][veld]
                if storing.get("item"):
                    rijen["Items"].pop()
            return httpx.Response(antwoord.status_code, json=rijen)

        with httpx.Client(base_url="http://kijkproef", transport=httpx.MockTransport(kijk_api)) as emby, \
             httpx.Client(base_url="http://taakproef", transport=httpx.MockTransport(mg_api)) as mg:
            kern = SimpleNamespace(client=lambda naam, _: {"mg-inhoud": mg, "emby-opslag": emby}[naam],
                                   sim=sim, net=SimpleNamespace(dossier=tmp_path))
            proef = Proefmedia(kern)
            apps = [appfixture(n) for n in ("emby", "mediaguard")]
            sim.post("/_sim/fixtures", json={"apps": apps, "datasets": [MEDIA]}).raise_for_status()
            schrijf_inhoud(kern, MEDIA, **proef.datasetstand)
            sleutel = emby.post("/_sim/bibliotheek", json=proef.kijkstand).raise_for_status().json()["api_key"]
            emby.headers["Authorization"] = f'MediaBrowser Token="{sleutel}"'
            proef.job = taak["id"]
            proef.meet("voor")
            yield proef, storing, taak


@pytest.mark.parametrize("veld", ["Played", "PlayCount", "LastPlayedDate", "PlaybackPositionTicks", "IsFavorite",
                                  "Rating", "AudioStreamIndex", "SubtitleStreamIndex"])
def test_een_ontbrekend_gebruikersveld_stopt_herstel(diensten, veld):
    proef, storing, _ = diensten
    storing["veld"] = veld
    with pytest.raises(AssertionError, match="Kijkgegevens"):
        proef.meet("tijdens")


@pytest.mark.parametrize("afwijking", ["item", "gebruiker", "rechten", "offline", "mg_http", "taak", "taaklog",
                                       "terugval", "stilstand", "app_ontbreekt"])
def test_controle_verwerpt_verlies_of_stilstand(diensten, afwijking):
    proef, storing, taak = diensten
    if afwijking == "taak":
        taak["id"] += 1
    elif afwijking == "taaklog":
        taak["logs"].clear()
    elif afwijking == "terugval":
        taak["progress"] = 0
    elif afwijking == "app_ontbreekt":
        proef.kern.sim.post("/_sim/reset").raise_for_status()
        from e2e.integral.scenarios.opslag import MEDIA, schrijf_inhoud

        proef.kern.sim.post("/_sim/fixtures", json={"datasets": [MEDIA]}).raise_for_status()
        schrijf_inhoud(proef.kern, MEDIA, **proef.datasetstand)
    elif afwijking == "mg_http":
        storing["mg_http"] = 404
    else:
        storing[afwijking] = True
    with pytest.raises((AssertionError, httpx.HTTPError)):
        proef.meet("na", afgerond=afwijking == "stilstand")


def test_actueel_hervatpunt_en_dezelfde_taak_na_herstel(diensten):
    proef, _, taak = diensten
    oud = proef.kijkstand["/media/proefreeks.mkv"]["proefkijker-b"].copy()
    proef.speel_verder()
    proef.meet("tijdens")
    taak.update(status="succeeded", progress=1, result={"status": "succeeded", "series": 1,
                                                       "episodes": 2, "failed_items": []})
    proef.meet("na", afgerond=True)
    proef.emby.post("/Users/proefkijker-b/Items/1/UserData", json=oud).raise_for_status()
    with pytest.raises(AssertionError, match="Kijkgegevens"):
        proef.meet("verouderd_hervatpunt", afgerond=True)
