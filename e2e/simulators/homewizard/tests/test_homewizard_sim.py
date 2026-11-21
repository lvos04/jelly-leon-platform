"""Toestandstabel HomeWizard-simulator: protocol (v1, alleen lezen), meterreset, zomertijd en het schakelverbod."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from homewizard_sim import create_app, zomertijdwissel


@pytest.fixture
def p1() -> Iterator[TestClient]:
    with TestClient(create_app("p1")) as c:
        yield c


@pytest.fixture
def socket() -> Iterator[TestClient]:
    with TestClient(create_app("socket")) as c:
        yield c


def test_p1_identiteit_en_meting(p1: TestClient) -> None:
    assert p1.get("/api").json()["product_type"] == "HWE-P1"
    d = p1.get("/api/v1/data").json()
    assert d["total_power_import_kwh"] == d["total_power_import_t1_kwh"] + d["total_power_import_t2_kwh"] == 1500.0
    assert d["total_power_export_kwh"] == 0.0 and d["active_power_w"] == 450.0
    assert isinstance(d["gas_timestamp"], int) and len(str(d["gas_timestamp"])) == 12


def test_p1_heeft_geen_schakelstand(p1: TestClient) -> None:
    assert p1.get("/api/v1/state").status_code == 404


def test_socket_identiteit_meting_en_stand(socket: TestClient) -> None:
    assert socket.get("/api").json()["product_type"] == "HWE-SKT"
    d = socket.get("/api/v1/data").json()
    assert d["total_power_import_kwh"] == 30.0 and "gas_timestamp" not in d
    assert socket.get("/api/v1/state").json() == {"power_on": True, "switch_lock": False, "brightness": 255}


@pytest.mark.parametrize("aan", [False, True])
def test_schakelopdracht_wordt_geregistreerd_en_geweigerd(socket: TestClient, aan: bool) -> None:
    r = socket.put("/api/v1/state", json={"power_on": aan})
    assert r.status_code == 403 and "alleen-lezen" in r.json()["error"]["description"]
    assert socket.get("/api/v1/state").json()["power_on"] is True  # niets geschakeld
    verzoeken = socket.get("/_sim/toestand").json()["schakelverzoeken"]
    assert [(v["methode"], v["pad"], v["details"]) for v in verzoeken] == [
        ("PUT", "/api/v1/state", {"power_on": str(aan).lower()})]


@pytest.mark.parametrize("methode,pad", [("POST", "/api/v1/identify"), ("PUT", "/api/v1/system"),
                                         ("DELETE", "/api/user"), ("PATCH", "/api/v1/state")])
def test_elk_ander_schrijvend_verzoek_wordt_geweigerd(socket: TestClient, methode: str, pad: str) -> None:
    assert socket.request(methode, pad, json={}).status_code == 403
    assert [v["methode"] for v in socket.get("/_sim/verzoeken").json() if v["pad"] == pad] == [methode]


def test_meterreset_laat_de_tellerstand_terugspringen(p1: TestClient) -> None:
    voor = p1.get("/api/v1/data").json()["total_power_import_kwh"]
    na = p1.post("/_sim/meterreset", json={"stand": 2.5}).json()
    assert voor == 1500.0 and na["total_power_import_kwh"] == 2.5 and na["total_power_import_t2_kwh"] == 0.0
    assert p1.get("/api/v1/data").json()["total_power_import_kwh"] == 2.5


def test_meterreset_met_negatieve_stand_wordt_geweigerd(p1: TestClient) -> None:
    assert p1.post("/_sim/meterreset", json={"stand": -1}).status_code == 422
    assert p1.get("/api/v1/data").json()["total_power_import_kwh"] == 1500.0


def test_wissel_naar_zomertijd_slaat_een_uur_over(p1: TestClient) -> None:
    r = p1.post("/_sim/zomertijd", json={"richting": "naar_zomertijd", "jaar": 2026}).json()
    assert r["wissel_utc"] == "2026-03-29T01:00:00+00:00"
    assert p1.get("/api/v1/data").json()["gas_timestamp"] == 260329013000
    p1.post("/_sim/klok", json={"verder_s": 3600})
    assert p1.get("/api/v1/data").json()["gas_timestamp"] == 260329033000


def test_wissel_naar_wintertijd_herhaalt_een_lokaal_uur(p1: TestClient) -> None:
    p1.post("/_sim/zomertijd", json={"richting": "naar_wintertijd", "jaar": 2026})
    eerst = p1.get("/api/v1/data").json()
    p1.post("/_sim/meting", json={"waarden": {"total_power_import_t1_kwh": 1001.0}})
    p1.post("/_sim/klok", json={"verder_s": 3600})
    later = p1.get("/api/v1/data").json()
    # Zelfde lokale tijd, een uur later: alleen de UTC-klok van de simulator onderscheidt ze.
    assert eerst["gas_timestamp"] == later["gas_timestamp"] == 261025023000
    assert later["total_power_import_kwh"] == eerst["total_power_import_kwh"] + 1.0


def test_wisselmomenten() -> None:
    assert zomertijdwissel(2027, "naar_zomertijd").isoformat() == "2027-03-28T01:00:00+00:00"
    assert zomertijdwissel(2027, "naar_wintertijd").isoformat() == "2027-10-31T01:00:00+00:00"


def test_klok_zonder_tijdzone_wordt_geweigerd(p1: TestClient) -> None:
    assert p1.post("/_sim/klok", json={"utc": "2026-10-25T00:30:00"}).status_code == 422


def test_onbekend_meetveld_wordt_geweigerd(p1: TestClient, socket: TestClient) -> None:
    assert p1.post("/_sim/meting", json={"waarden": {"serial": 1}}).status_code == 422
    assert socket.post("/_sim/meting", json={"waarden": {"total_gas_m3": 1}}).status_code == 422


def test_reset_herstelt_begintoestand(p1: TestClient) -> None:
    p1.post("/_sim/meterreset", json={})
    p1.post("/_sim/klok", json={"utc": "2026-01-01T00:00:00Z"})
    p1.put("/api/v1/state", json={"power_on": False})
    p1.post("/_sim/reset")
    t = p1.get("/_sim/toestand").json()
    assert t["data"]["total_power_import_kwh"] == 1500.0 and t["klok_vast"] is False and t["schakelverzoeken"] == []


@pytest.mark.parametrize("naam", ["http_503", "traag", "ongeldige_json"])
def test_algemene_foutinjectie(p1: TestClient, foutproef, naam: str) -> None:
    p1.post("/_sim/klok", json={"utc": "2026-06-01T12:00:00Z"})  # vaste tijd: gelijke antwoorden
    foutproef(p1, "/api/v1/data", {}, naam)


def test_onbekend_apparaat() -> None:
    with pytest.raises(ValueError):
        create_app("boiler")


def test_my_house_leest_de_p1_en_lost_het_dubbele_uur_op(p1: TestClient, my_house_bron) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from shc_integrations.homewizard.adapter import normalize_v1

    p1.post("/_sim/zomertijd", json={"richting": "naar_wintertijd", "jaar": 2026})
    gezien = []
    for _ in range(2):
        t = p1.get("/_sim/toestand").json()
        ontvangen = datetime.fromisoformat(t["klok"])
        readings = {r.metric: r for r in normalize_v1("5e0000000001", t["data"], ontvangen,
                                                      ZoneInfo("Europe/Amsterdam"))}
        assert readings["energy_import_kwh"].value == 1500.0
        gezien.append(readings["gas_m3"].observed_at.isoformat())
        p1.post("/_sim/klok", json={"verder_s": 3600})
    assert gezien == ["2026-10-25T00:30:00+00:00", "2026-10-25T01:30:00+00:00"]
