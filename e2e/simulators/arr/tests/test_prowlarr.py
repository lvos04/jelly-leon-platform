"""De Prowlarr-mock van MSO doet de terugoproep; de bediening toont alleen aantallen."""

import pytest
from arr_sim.prowlarr import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def prowlarr(mso_bron):
    with TestClient(create_app()) as client:
        yield client


@pytest.mark.parametrize("headers", [{}, {"X-Api-Key": "ongeldig"}])
def test_prowlarr_weigert_zonder_geldige_sleutel(prowlarr, headers):
    assert prowlarr.get("/api/v1/search", headers=headers).status_code == 401


def test_zoeken_via_mso_doet_een_waarneembare_terugoproep(prowlarr, monkeypatch):
    import httpx
    from msomocks import prowlarr as bron

    ontvangen = []

    def antwoord(url, **kwargs):
        ontvangen.append((url, kwargs["headers"], kwargs["params"]))
        return httpx.Response(200, text="<rss><channel><item><title>Teruggevonden</title>"
                                       "<guid>sim-item</guid></item></channel></rss>",
                              request=httpx.Request("GET", url))

    monkeypatch.setattr(bron.httpx, "get", antwoord)
    headers = {"X-Api-Key": prowlarr.get("/_sim/sleutels").json()["apikey"]}
    schema = prowlarr.get("/api/v1/indexer/schema", headers=headers).json()
    indexer = next(i for i in schema if i.get("name") == "Generic Newznab")
    for field in indexer["fields"]:
        if field["name"] == "baseUrl":
            field["value"] = "http://mso.invalid"
    registered = prowlarr.post("/api/v1/indexer", headers=headers, json={**indexer, "name": "Test-MSO"})
    assert registered.status_code == 201
    result = prowlarr.get("/api/v1/search", headers={**headers, "X-MSO-Trace": "synthetisch"},
                          params={"indexerIds": registered.json()["id"], "query": "Proef"})
    assert [r["title"] for r in result.json()] == ["Teruggevonden"]
    assert len(ontvangen) == 1 and ontvangen[0][0] == "http://mso.invalid/api"
    assert ontvangen[0][2]["q"] == "Proef" and "x-mso-trace" not in {h.lower() for h in ontvangen[0][1]}
    assert prowlarr.get("/_sim/zoekstatistiek").json() == {
        "zoekopdrachten": 1, "terugoproepen": 1, "max_diepte": 1, "mislukte_terugoproepen": 0}
    assert sum(v["pad"] == "/api/v1/search" for v in prowlarr.get("/_sim/verzoeken").json()) == 1


@pytest.mark.parametrize("fault", ["http_503", "traag", "ongeldige_json"])
def test_prowlarr_foutinjectie(prowlarr, foutproef, fault):
    headers = {"X-Api-Key": prowlarr.get("/_sim/sleutels").json()["apikey"]}
    foutproef(prowlarr, "/api/v1/tag", headers, fault)
