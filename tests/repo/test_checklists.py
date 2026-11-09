"""Controlelijsten I01/I02 (eigen formaat checklist v1, ADR-0015).

- Vorm (JSON Schema), dekking van precies de Excel-taken met titel/acceptatie/risico uit de Excel, alleen-lezen
  TrueNAS-methoden, geen terminal, geen voorbeeldwaarden, eerlijke validatie per stap (``tools/checklists.py``).
- Negatieve tests: elke overtreding wordt herkend.
- Simulator: elke genoemde methode bestaat in het TrueNAS-profiel waarvoor ze geldt en is aan te roepen met de
  alleen-lezen-key van de Hub (25.04.2.6 en 25.10.8) — en een methode die in een profiel niet bestaat, wordt daar ook
  echt geweigerd (``-32601``), zodat de versie-indeling klopt.
"""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import checklists as cl  # noqa: E402


def _load(naam: str) -> dict[str, Any]:
    data = yaml.safe_load((cl.CHECKLIST_DIR / naam).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_checklists_are_valid_and_cover_the_excel_tasks() -> None:
    uitkomst = cl.controleer_alle()
    assert set(uitkomst) == {"I01_TRUENAS_BASIS.checklist.yaml", "I02_BACKUP_EN_RESTORE.checklist.yaml"}
    assert uitkomst == {naam: [] for naam in uitkomst}
    taken = cl.excel_taken()
    for naam in uitkomst:
        data = _load(naam)
        assert len(data["stappen"]) == len(taken[data["document"]]) == 8
        assert data["installatie"] == "geen" and data["mutaties_door_platform"] is False


def _mutate(naam: str, wijzig: Any) -> list[str]:
    data = copy.deepcopy(_load(naam))
    wijzig(data)
    return cl.controleer_lijst(data, cl.excel_taken())


def _set_method(d: dict[str, Any], naam: str) -> None:
    d["stappen"][0]["truenas_methoden"].append({"naam": naam, "versies": ["25.10"]})


@pytest.mark.parametrize(
    ("beschrijving", "wijzig", "verwacht"),
    [
        ("schrijvende methode", lambda d: _set_method(d, "app.create"), "geen alleen-lezen-methode"),
        ("snapshot maken", lambda d: _set_method(d, "pool.snapshot.create"), "geen alleen-lezen-methode"),
        ("dataset-rechten", lambda d: _set_method(d, "filesystem.setperm"), "geen alleen-lezen-methode"),
        ("stap ontbreekt", lambda d: d["stappen"].pop(), "dekken de Excel-taken niet exact"),
        ("acceptatie herschreven", lambda d: d["stappen"][1].update(acceptatie_nl="Alles groen."),
         "acceptatie ≠ Excel"),
        ("terminal", lambda d: d["stappen"][2].update(uitvoeren_nl="Voer `ls -l /dev/dri` uit op de NAS."),
         "terminalopdracht"),
        ("voorbeeld-IP", lambda d: d["stappen"][3].update(uitvoeren_nl="Verbind met wss://192.168.1.10/api/current."),
         "voorbeeldwaarde"),
        ("voorbeeldpool", lambda d: d["stappen"][6].update(uitvoeren_nl="Maak datasets onder /mnt/tank/apps aan."),
         "voorbeeldwaarde"),
        ("alleen Sim-getest", lambda d: d["stappen"][0].update(validatie_nu=["Sim-getest"]), "nooit alleen Sim-getest"),
        ("installeert iets", lambda d: d.update(installatie="custom_app"), "schema"),
        ("muteert", lambda d: d.update(mutaties_door_platform=True), "schema"),
        ("besluit onbekend", lambda d: d["stappen"][0].update(besluiten=["D18"]), "besluit niet in de lijst"),
    ],
)
def test_violations_are_detected(beschrijving: str, wijzig: Any, verwacht: str) -> None:
    fout = _mutate("I01_TRUENAS_BASIS.checklist.yaml", wijzig)
    assert any(verwacht in f for f in fout), (beschrijving, fout)


def test_account_steps_are_never_claimed_without_the_account() -> None:
    def wijzig(d: dict[str, Any]) -> None:
        d["stappen"][1]["validatie_nu"] = ["NIET GEVALIDEERD (echte NAS)"]  # I02-02-02: controle in OneDrive

    fout = _mutate("I02_BACKUP_EN_RESTORE.checklist.yaml", wijzig)
    assert any("NIET GEVALIDEERD (account)" in f for f in fout), fout


# ------------------------------------------------------------------ tegen de TrueNAS-simulator (alleen lezen)
truenas_sim = pytest.importorskip("truenas_sim.server")
pytestmark = pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient` is deprecated")


@pytest.fixture(params=["25.04.2.6", "25.10.8"])
def ro(request: pytest.FixtureRequest) -> Iterator[tuple[str, Any]]:
    from starlette.testclient import TestClient

    server = truenas_sim.SimServer(request.param)
    with TestClient(truenas_sim.create_app(server)) as client:
        key = client.get("/_sim/keys").json()["readonly"]
        with client.websocket_connect("/api/current") as ws:
            seq = iter(range(1, 10_000))

            def call(method: str, *params: Any) -> dict[str, Any]:
                ident = next(seq)
                ws.send_text(json.dumps({"jsonrpc": "2.0", "id": ident, "method": method, "params": list(params)}))
                while True:
                    msg = json.loads(ws.receive_text())
                    if msg.get("id") == ident:
                        return dict(msg)

            assert call("auth.login_with_api_key", key)["result"] is True
            yield str(request.param)[:5], call


def test_every_listed_method_is_callable_read_only_on_its_versions(ro: tuple[str, Any]) -> None:
    familie, call = ro
    gezien_elders: set[str] = set()
    for naam in ("I01_TRUENAS_BASIS.checklist.yaml", "I02_BACKUP_EN_RESTORE.checklist.yaml"):
        for stap in _load(naam)["stappen"]:
            for m in stap.get("truenas_methoden", []):
                antwoord = call(m["naam"])
                if familie in m["versies"]:
                    assert "result" in antwoord, (stap["id"], m["naam"], familie, antwoord.get("error"))
                else:
                    gezien_elders.add(m["naam"])
                    assert antwoord.get("error", {}).get("code") == -32601, (m["naam"], familie)
    # de versieverschillen uit TRUENAS_APP_API.md §6 zitten echt in de lijsten
    assert gezien_elders, "geen versie-afhankelijke methode getest"
