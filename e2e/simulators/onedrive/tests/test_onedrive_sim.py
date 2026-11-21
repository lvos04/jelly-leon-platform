"""Toestandstabel OneDrive-simulator: geplande back-up, falen, verouderen, herstel en de taak voor de TrueNAS-sim."""

import hashlib
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from onedrive_sim import create_app

BRON = {"db/export.sql": "-- testdump 1\n", "config/app.yaml": "naam: sim\n"}


@pytest.fixture
def od() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def taak(od: TestClient) -> dict:
    [t] = od.get("/_sim/cloudsync_taken").json()
    return t


def test_nooit_gedraaid_is_geen_succes(od: TestClient) -> None:
    t = taak(od)
    assert t["job"] is None  # onbekend, niet "geslaagd"
    assert od.get("/herstel").json() == {"bestanden": {}}


def test_geplande_backup_schrijft_naar_het_doel(od: TestClient) -> None:
    t = od.post("/_sim/backup", json={"bestanden": BRON}).json()
    assert t["job"]["state"] == "SUCCESS" and t["job"]["error"] is None
    assert abs(t["job"]["time_finished"]["$date"] / 1000 - time.time()) < 5
    lijst = od.get("/herstel").json()["bestanden"]
    assert lijst == {p: hashlib.sha256(i.encode()).hexdigest() for p, i in BRON.items()}


def test_herstel_geeft_de_inhoud_terug(od: TestClient) -> None:
    od.post("/_sim/backup", json={"bestanden": BRON})
    assert od.get("/herstel/db/export.sql").json() == {"pad": "db/export.sql", "inhoud": BRON["db/export.sql"]}


def test_herstel_van_onbekend_bestand(od: TestClient) -> None:
    od.post("/_sim/backup", json={"bestanden": BRON})
    assert od.get("/herstel/db/ander.sql").status_code == 404


@pytest.mark.parametrize("naam,melding", [("doel_vol", "Quota exceeded"), ("token_verlopen", "Token expired")])
def test_storing_laat_de_run_mislukken_en_bewaart_de_vorige_stand(od: TestClient, naam: str, melding: str) -> None:
    od.post("/_sim/backup", json={"bestanden": BRON})
    assert od.post("/_sim/fouten", json={"naam": naam, "aantal": 1}).status_code == 200
    t = od.post("/_sim/backup", json={"bestanden": {"db/export.sql": "-- testdump 2\n"}}).json()
    assert t["job"]["state"] == "FAILED" and melding in t["job"]["error"]
    assert od.get("/herstel/db/export.sql").json()["inhoud"] == "-- testdump 1\n"  # niets verloren
    # Eenmalige storing: de volgende run slaagt weer.
    assert od.post("/_sim/backup", json={"bestanden": BRON}).json()["job"]["state"] == "SUCCESS"


def test_eigen_foutmelding(od: TestClient) -> None:
    od.post("/_sim/fouten", json={"naam": "doel_vol", "waarde": "eigen melding"})
    assert od.post("/_sim/backup", json={}).json()["job"]["error"] == "eigen melding"


def test_eerste_run_mislukt_laat_het_doel_leeg(od: TestClient) -> None:
    od.post("/_sim/fouten", json={"naam": "token_verlopen"})
    assert od.post("/_sim/backup", json={"bestanden": BRON}).json()["job"]["state"] == "FAILED"
    assert od.get("/herstel").json() == {"bestanden": {}}


def test_verouderen_schuift_de_laatste_run_terug(od: TestClient) -> None:
    voor = od.post("/_sim/backup", json={"bestanden": BRON}).json()["job"]
    na = od.post("/_sim/verouder", json={"uren": 30}).json()["job"]
    for k in ("time_started", "time_finished"):
        assert voor[k]["$date"] - na[k]["$date"] == 30 * 3600 * 1000
    assert na["state"] == "SUCCESS"  # de run zelf slaagde; alleen oud


def test_verouderen_zonder_run_wordt_geweigerd(od: TestClient) -> None:
    assert od.post("/_sim/verouder", json={"uren": 30}).status_code == 409
    assert taak(od)["job"] is None


@pytest.mark.parametrize("body", [{"uren": 0}, {"uren": -1}, {"uren": 30, "extra": 1}])
def test_ongeldige_veroudering(od: TestClient, body: dict) -> None:
    od.post("/_sim/backup", json={})
    assert od.post("/_sim/verouder", json=body).status_code == 422


def test_ongeldig_bronpad(od: TestClient) -> None:
    assert od.post("/_sim/backup", json={"pad": "relatief/pad"}).status_code == 422
    assert taak(od)["job"] is None


def test_taak_heeft_de_vorm_die_de_hub_leest(od: TestClient) -> None:
    t = od.post("/_sim/backup", json={"bestanden": BRON, "pad": "/mnt/simpool/apps/sim"}).json()
    # Velden uit hub.truenas.normalize.cloudsync (ai-server-manager): richting PUSH, ingeschakeld, provider en job.
    assert t["path"] == "/mnt/simpool/apps/sim" and t["direction"] == "PUSH" and t["enabled"] is True
    assert t["credentials"]["provider"] == {"type": "ONEDRIVE"}
    assert set(t["credentials"]) == {"id", "name", "provider"}  # geen attributen: geen token of geheim
    assert set(t["job"]) >= {"state", "time_started", "time_finished", "error"}


def test_truenas_simulator_neemt_de_taak_over(od: TestClient) -> None:
    from truenas_sim.server import create_app as truenas_app

    od.post("/_sim/backup", json={"bestanden": BRON})
    od.post("/_sim/verouder", json={"uren": 30})
    taken = od.get("/_sim/cloudsync_taken").json()
    with TestClient(truenas_app()) as tn:
        assert tn.post("/_sim/fixtures", json={"cloudsync_tasks": taken}).status_code == 200
        assert tn.app.state.sim.state.cloudsync_tasks == taken


def test_reset_herstelt_begintoestand(od: TestClient) -> None:
    od.post("/_sim/backup", json={"bestanden": BRON})
    od.post("/_sim/fouten", json={"naam": "doel_vol"})
    od.post("/_sim/reset")
    assert taak(od)["job"] is None and od.get("/herstel").json() == {"bestanden": {}}
    assert od.post("/_sim/backup", json={}).json()["job"]["state"] == "SUCCESS"


def test_onbekende_fout(od: TestClient) -> None:
    assert od.post("/_sim/fouten", json={"naam": "bestaat_niet"}).status_code == 422


@pytest.mark.parametrize("naam", ["http_503", "traag", "ongeldige_json"])
def test_algemene_foutinjectie_op_herstel(od: TestClient, foutproef, naam: str) -> None:
    od.post("/_sim/backup", json={"bestanden": BRON})
    foutproef(od, "/herstel", {}, naam)
