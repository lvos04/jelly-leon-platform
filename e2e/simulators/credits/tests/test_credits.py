"""Alleen inbegrepen testtegoed kan tot een advies leiden."""
import tomllib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient


def test_registratie_credits():
    project = tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())
    assert project["project"]["scripts"].get("credits-sim") == "credits.__main__:main"


def test_laatste_eenheid_slechts_een_keer():
    from credits import create_app

    taak = {"max_eenheden": 1, "alleen_inbegrepen": True}
    with TestClient(create_app(eenheden=1)) as client, ThreadPoolExecutor(2) as pool:
        codes = sorted(pool.map(lambda _: client.post("/diagnose", json=taak).status_code, range(2)))
        assert codes == [200, 402]
        assert client.post("/diagnose", json=taak).status_code == 402
        assert client.get("/_sim/stand").json() == {"beschikbaar": 0, "adviezen": 1, "uitgeput": 2}


def test_uitputting_blijft_actief_en_betaalde_uitwijk_is_geweigerd():
    from credits import create_app

    taak = {"max_eenheden": 1, "alleen_inbegrepen": True}
    with TestClient(create_app(eenheden=2)) as client:
        for fout in ({"alleen_inbegrepen": False}, {"max_eenheden": True}, {"max_eenheden": -1}):
            assert client.post("/diagnose", json={**taak, **fout}).status_code == 422
        assert client.post("/diagnose", json=taak).json() == {"analyse": "Controleer de bronmeting."}
        client.post("/_sim/fouten", json={"naam": "tegoed_op", "aantal": 1}).raise_for_status()
        assert [client.post("/diagnose", json=taak).status_code for _ in range(2)] == [402, 402]
        assert client.get("/_sim/stand").json()["adviezen"] == 1
