"""Volg één zoekopdracht en toets de herkomst van een begrensde herhaling via Newznab."""

import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType

import httpx

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


def meet_zoekvraag(sim: httpx.Client, aanvraag: Callable[[], httpx.Response],
                   verwacht: int) -> tuple[httpx.Response, dict[str, int]]:
    voor = aantallen(sim)
    antwoord = aanvraag()
    na = aantallen(sim)
    delta = {naam: na[naam] - voor[naam] for naam in ("zoekopdrachten", "terugoproepen")}
    assert delta == {"zoekopdrachten": verwacht, "terugoproepen": verwacht}, "Onverwachte telling uit één zoekvraag"
    assert na["mislukte_terugoproepen"] == voor["mislukte_terugoproepen"] and na["max_diepte"] <= 1
    return antwoord, delta


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
        resultaat, normaal = meet_zoekvraag(sim, lambda: api.get("search", params={"q": "Expanse", "category": "tv"}), 1)
        bronnen = resultaat.raise_for_status().json()["sources"]
        assert any(s["source_id"] == "prowlarr" and s["status"] == "ok" for s in bronnen)

        params = {"t": "search", "q": "Expanse", "cat": "5000", "apikey": sleutel_feed}
        herkomst = {"X-MSO-Trace": "proef;hop=2;visited=mso,prowlarr"}
        antwoord, met_kop = meet_zoekvraag(sim, lambda: feed.get("/newznab/api", params=params, headers=herkomst), 0)
        assert antwoord.status_code == 508 and antwoord.headers["X-MSO-Refusal"] == "cycle_detected"
        assert b"cycle_detected" in antwoord.content

        rss, zonder_kop = meet_zoekvraag(sim, lambda: feed.get("/newznab/api", params=params), 1)
        rss.raise_for_status()
        assert b"<rss" in rss.content and b"<channel>" in rss.content
    # De gepinde Prowlarr-mock geeft zijn eigen User-Agent mee zonder X-MSO-Trace.
    # MSO neemt Prowlarr op in de bezochte bronnen en slaat deze bij de terugoproep over.
    schrijf(kern.net.dossier / "mso.json", {"normale_zoekvraag": normaal, "weigering_met_kop": antwoord.status_code,
            "met_herkomstkop": met_kop, "zonder_herkomstkop": zonder_kop, "max_diepte": aantallen(sim)["max_diepte"],
            "tijdslimiet_seconden": 25})
