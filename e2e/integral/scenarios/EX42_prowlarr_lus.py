"""Volg één zoekopdracht en toets de herkomst van een begrensde herhaling via Newznab."""

import signal
from collections.abc import Iterator
from contextlib import contextmanager
from types import FrameType

import httpx
import pytest

from e2e.integral.bewijs import schrijf
from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.mso import eerste_start, koppel

SCENARIO = {
    "groepen": ("media", "mso"), "diensten": ("mso", "prowlarr-sim"), "eigenaar": "media-source-optimizer",
    "verwachting": "De herkomstcontrole begrenst de cyclus tussen MSO en Prowlarr tot één zoekopdracht.",
}


@contextmanager
def tijdslimiet(seconden: int) -> Iterator[None]:
    def verlopen(_signaal: int, _frame: FrameType | None) -> None:
        raise TimeoutError("De tijdslimiet voor de Prowlarr-proef is verstreken")

    vorige = signal.signal(signal.SIGALRM, verlopen)
    signal.alarm(seconden)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, vorige)


def aantallen(sim: httpx.Client) -> dict[str, int]:
    stand: dict[str, int] = sim.get("/_sim/zoekstatistiek").raise_for_status().json()
    log = sim.get("/_sim/verzoeken").raise_for_status().json()
    assert stand["zoekopdrachten"] == sum(r["pad"] == "/api/v1/search" for r in log)
    return stand


def geen_nieuwe_zoekopdracht(voor: int, na: int) -> None:
    assert na == voor, "De herhaalde zoekvraag is opnieuw aan Prowlarr aangeboden"


def test_cyclus_en_herkomstkop(kern: Kern) -> None:
    api = eerste_start(kern, "manage")
    feed = kern.client("mso", 38081)
    sim = kern.client("prowlarr-sim", 30219)
    sleutel = sim.get("/_sim/sleutels").raise_for_status().json()["apikey"]
    koppel(api, "prowlarr", sim, {"api_key": sleutel})
    sim.headers["X-Api-Key"] = sleutel
    instellingen = {"newznab.public_url": str(feed.base_url).rstrip("/"), "newznab.backends": ["prowlarr"],
                    "search.timeout_s": 4, "search.prowlarr_auto_retry": False}
    api.put("settings", json={"values": instellingen}).raise_for_status()
    afnemer = api.post("newznab/consumers", json={"label": "Integrale proef", "node": "external"}).raise_for_status()
    sleutel_feed = afnemer.json()["apikey"]
    schema = sim.get("/api/v1/indexer/schema").raise_for_status().json()
    indexer = next(i for i in schema if i["name"] == "Generic Newznab")
    waarden = {"baseUrl": str(feed.base_url.join("/newznab")), "apiKey": sleutel_feed, "apiPath": "/api"}
    for veld in indexer["fields"]:
        if veld["name"] in waarden:
            veld["value"] = waarden[veld["name"]]
    registratie = sim.post("/api/v1/indexer", json={**indexer, "name": "Proef-MSO", "enable": True})
    assert registratie.status_code == 201 and registratie.json()["name"] == "Proef-MSO"
    with tijdslimiet(25):
        assert aantallen(sim)["zoekopdrachten"] == 0
        resultaat = api.get("search", params={"q": "Expanse", "category": "tv"}).raise_for_status().json()
        assert any(s["source_id"] == "prowlarr" and s["status"] == "ok" for s in resultaat["sources"])
        stand = aantallen(sim)
        assert stand == {"zoekopdrachten": 1, "terugoproepen": 1, "max_diepte": 1, "mislukte_terugoproepen": 0}

        # Een vaste, nageleverde kopie van een al bezochte vraag. Alleen de herkomstkop varieert.
        params = {"t": "search", "q": "Expanse", "cat": "5000", "apikey": sleutel_feed}
        herkomst = {"X-MSO-Trace": "proef;hop=2;visited=mso,prowlarr"}
        antwoord = feed.get("/newznab/api", params=params, headers=herkomst)
        assert antwoord.status_code == 508 and antwoord.headers["X-MSO-Refusal"] == "cycle_detected"
        assert b"cycle_detected" in antwoord.content
        met_kop = aantallen(sim)["zoekopdrachten"]
        geen_nieuwe_zoekopdracht(stand["zoekopdrachten"], met_kop)

        tellingen = [met_kop]
        for _ in range(2):
            rss = feed.get("/newznab/api", params=params).raise_for_status()
            assert b"<rss" in rss.content and b"<channel>" in rss.content
            nieuw = aantallen(sim)
            assert nieuw["max_diepte"] == 1 and nieuw["mislukte_terugoproepen"] == 0
            assert nieuw["zoekopdrachten"] == nieuw["terugoproepen"] == tellingen[-1] + 1
            with pytest.raises(AssertionError, match="opnieuw aan Prowlarr"):
                geen_nieuwe_zoekopdracht(tellingen[-1], nieuw["zoekopdrachten"])
            tellingen.append(nieuw["zoekopdrachten"])
    schrijf(kern.net.dossier / "mso.json", {"normale_zoekvraag": stand, "weigering_met_kop": antwoord.status_code,
            "zoekopdrachten_met_kop": met_kop, "zoekopdrachten_bij_twee_herhalingen_zonder_kop": tellingen,
            "max_diepte": nieuw["max_diepte"], "tijdslimiet_seconden": 25})
