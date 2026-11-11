"""Hub-lezer (C1.3-JLP-14): inventaris (``inventaris.lezen``) en catalogusbeschikbaarheid (``catalogus.lezen``) tegen de
Hub-simulator in de vorm van de echte Hub. Elke storing wordt eerlijk ``unknown`` of ``capability_unavailable``."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from hub_p2 import app_record
from planhulp import GOTIFY_REPO, gotify_record
from starlette.testclient import TestClient

from lifecycle import hubread
from lifecycle.hubread import HubLezer

URL = "http://hub.test"


@pytest.fixture
def lezer(hub: Any) -> Any:
    _sid, token = hub.state.sim.register_service("jelly-leon-platform")
    with TestClient(hub, base_url=URL) as client:
        yield HubLezer(client, URL, token)


def test_inventaris_in_de_vorm_van_de_conflictanalyse(hub: Any, lezer: HubLezer) -> None:
    p2 = hub.state.sim.p2
    p2.zet_apps(gotify_record(), app_record("zonder-poort", repository="example.org/x", tag="2.0.0"))
    inv = lezer.inventaris()
    assert inv.status == "ok" and inv.reden is None and inv.snapshot_id == p2.snapshot_id and inv.gemeten_op
    assert inv.inventory is not None and inv.inventory.source == "hub_snapshot"
    assert sorted(inv.records) == ["gotify", "zonder-poort"]
    gotify = next(a for a in inv.inventory.apps if a.name == "gotify")
    assert gotify.platform_app_id == "gotify" and gotify.state == "RUNNING" and gotify.catalog_app == "ix-app"
    assert gotify.images == [f"{GOTIFY_REPO}:3.0.0"]
    assert [(p.container_port, p.protocol, p.host_ports[0].host_port) for p in gotify.used_ports] == \
        [(8080, "tcp", 30215)]
    assert [(v.source, v.destination, v.type) for v in gotify.volumes] == \
        [("/mnt/pool-x/apps/gotify", "/app/data", "bind")]
    assert inv.view() == {"status": "ok", "reden": None, "bron": "hub_snapshot", "gemeten_op": inv.gemeten_op,
                          "snapshot_id": p2.snapshot_id, "aantal_apps": 2, "retry_after_s": None}


def test_inventaris_over_meer_paginas_binnen_een_meting(hub: Any, lezer: HubLezer,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hubread, "PAGE_SIZE", 2)
    p2 = hub.state.sim.p2
    p2.zet_apps(*[app_record(f"app-{i}", repository="example.org/x", tag="1.0.0") for i in range(5)])
    inv = lezer.inventaris()
    assert inv.status == "ok" and sorted(inv.records) == [f"app-{i}" for i in range(5)]
    assert hub.state.sim.calls["apps"] == 3 and hub.state.sim.calls["app_detail"] == 5


def test_wisselende_meting_begint_opnieuw_en_geeft_anders_unknown(hub: Any, lezer: HubLezer,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(hubread, "PAGE_SIZE", 2)
    p2 = hub.state.sim.p2
    p2.zet_apps(*[app_record(f"app-{i}", repository="example.org/x", tag="1.0.0") for i in range(3)])
    p2.wissel_na_pagina = 1  # één keer 409 snapshot_changed → opnieuw vanaf de eerste pagina
    inv = lezer.inventaris()
    assert inv.status == "ok" and len(inv.records) == 3 and inv.snapshot_id == p2.snapshot_id
    p2.wissel_na_pagina = 10  # blijft wisselen → geen half beeld, maar 'unknown'
    wisselt = lezer.inventaris()
    assert (wisselt.status, wisselt.reden, wisselt.inventory) == ("unknown", "meting_wisselt", None)


@pytest.mark.parametrize(("zet", "status", "reden"), [
    (lambda p2: p2.scopes.update({"jelly-leon-platform": set()}), "capability_unavailable", "scope_ontbreekt"),
    (lambda p2: setattr(p2, "inventaris_storing", (404, "not_found", None)), "capability_unavailable",
     "route_ontbreekt"),
    (lambda p2: setattr(p2, "inventaris_storing", (500, "internal_error", None)), "unknown", "hub_antwoord_500"),
    (lambda p2: setattr(p2, "verouderd", True), "unknown", "inventaris_verouderd"),
    (lambda p2: (setattr(p2, "inventaris_status", "unknown"), setattr(p2, "inventaris_reden", "truenas_onbereikbaar")),
     "unknown", "inventaris_truenas_onbereikbaar"),
])
def test_storing_in_de_inventaris_is_nooit_een_lege_lijst(hub: Any, lezer: HubLezer, zet: Any, status: str,
                                                          reden: str) -> None:
    p2 = hub.state.sim.p2
    p2.zet_apps(gotify_record())
    zet(p2)
    inv = lezer.inventaris()
    assert (inv.status, inv.reden) == (status, reden)
    assert inv.inventory is None and inv.records == {} and inv.view()["aantal_apps"] is None


def test_hub_vraagt_te_wachten_geeft_retry_after_door(hub: Any, lezer: HubLezer) -> None:
    hub.state.sim.p2.inventaris_storing = (503, "service_unavailable", 30)
    inv = lezer.inventaris()
    assert (inv.status, inv.reden, inv.retry_after_s) == ("unknown", "hub_vraagt_te_wachten", 30.0)


def test_onbereikbare_hub_en_geweigerd_token(hub: Any) -> None:
    def weg(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("geen verbinding", request=request)

    with httpx.Client(transport=httpx.MockTransport(weg)) as client:
        lezer = HubLezer(client, URL, "token")
        assert (lezer.inventaris().status, lezer.inventaris().reden) == ("unknown", "hub_onbereikbaar")
        b = lezer.beschikbaarheid("stable", "ix-app", "1.4.8")
        assert (b.beschikbaar, b.status, b.reden, b.available_versions) == ("onbekend", "unknown", "hub_onbereikbaar",
                                                                            None)
    with TestClient(hub, base_url=URL) as client:
        fout = HubLezer(client, URL, "verkeerd-token").inventaris()
        assert (fout.status, fout.reden) == ("unknown", "service_token_geweigerd")


def test_onleesbaar_antwoord_is_unknown() -> None:
    antwoorden = iter([httpx.Response(200, text="<html>"), httpx.Response(200, json={"items": []}),
                       httpx.Response(200, json={"items": [], "inventaris": {"status": "ok"}}),
                       httpx.Response(200, json={"beschikbaar": "misschien"}),
                       httpx.Response(200, json={"beschikbaar": "ja"}),
                       httpx.Response(200, json=["ja"])])
    with httpx.Client(transport=httpx.MockTransport(lambda _r: next(antwoorden))) as client:
        lezer = HubLezer(client, URL, "token")
        assert lezer.inventaris().reden == "antwoord_ongeldig"
        assert lezer.inventaris().reden == "antwoord_ongeldig"
        assert lezer.inventaris().reden == "meting_ontbreekt"  # zonder tijdstip is het geen meting
        assert lezer.beschikbaarheid("stable", "ix-app", "1.4.8").reden == "antwoord_ongeldig"
        zonder_tijd = lezer.beschikbaarheid("stable", "ix-app", "1.4.8")
        assert (zonder_tijd.beschikbaar, zonder_tijd.reden) == ("onbekend", "meting_zonder_tijdstip")
        assert lezer.beschikbaarheid("stable", "ix-app", "1.4.8").reden == "antwoord_ongeldig"


def test_catalogusbeschikbaarheid_ja_nee_onbekend(hub: Any, lezer: HubLezer) -> None:
    p2 = hub.state.sim.p2
    p2.zet_beschikbaar("stable", "ix-app", "1.4.8", "ja")
    p2.zet_beschikbaar("community", "authentik", "1.3.60", "nee")
    ja = lezer.beschikbaarheid("stable", "ix-app", "1.4.8")
    assert (ja.beschikbaar, ja.status, ja.available_versions) == ("ja", "ok", ["1.4.8"]) and ja.gemeten_op
    nee = lezer.beschikbaarheid("community", "authentik", "1.3.60")
    assert (nee.beschikbaar, nee.status, nee.available_versions) == ("nee", "ok", []) and nee.gemeten_op
    onbekend = lezer.beschikbaarheid("stable", "ix-app", "9.9.9")
    assert (onbekend.beschikbaar, onbekend.reden, onbekend.available_versions) == ("onbekend", "niet_gemeten", None)
    assert ja.view() == {"train": "stable", "app": "ix-app", "version": "1.4.8", "beschikbaar": "ja", "status": "ok",
                         "reden": None, "gemeten_op": ja.gemeten_op}


def test_catalogus_zonder_route_of_scope_is_capability_unavailable(hub: Any, lezer: HubLezer) -> None:
    p2 = hub.state.sim.p2
    p2.zet_beschikbaar("stable", "ix-app", "1.4.8", "ja")
    p2.catalogus_route = False  # zoals de echte Hub nu: de route bestaat nog niet
    zonder_route = lezer.beschikbaarheid("stable", "ix-app", "1.4.8")
    assert (zonder_route.beschikbaar, zonder_route.status, zonder_route.reden) == \
        ("onbekend", "capability_unavailable", "route_ontbreekt")
    p2.catalogus_route = True
    p2.scopes["jelly-leon-platform"] = {"inventaris.lezen"}
    zonder_scope = lezer.beschikbaarheid("stable", "ix-app", "1.4.8")
    assert (zonder_scope.beschikbaar, zonder_scope.status, zonder_scope.reden) == \
        ("onbekend", "capability_unavailable", "scope_ontbreekt")
