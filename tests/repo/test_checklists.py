"""Controlelijsten (eigen formaat checklist v1): I01/I02 (ADR-0015) en de bestaande apps I03…I20 (ADR-0027).

Alle tests gelden voor elke lijst in ``install/checklists/`` (``cl.lijsten()``), niet voor vaste bestandsnamen.

- Vorm (JSON Schema), dekking van precies de Excel-taken met titel/acceptatie/risico uit de Excel, alleen-lezen
  TrueNAS-methoden, geen terminal, geen voorbeeldwaarden, eerlijke validatie per stap (``tools/checklists.py``).
- Verzameling: elke lijst hoort bij precies één document onder ``03_INSTALLATIE/`` (bestandsnaam = documentnaam) en
  geen document heeft twee lijsten (ook ``python tools/checklists.py`` faalt dan).
- Negatieve tests: elke overtreding wordt herkend, in elke lijst.
- Simulator: elke genoemde methode bestaat in het TrueNAS-profiel waarvoor ze geldt en is aan te roepen met de
  alleen-lezen-key van de Hub (25.04.2.6 en 25.10.8) — en een methode die in een profiel niet bestaat, wordt daar ook
  echt geweigerd (``-32601``), zodat de versie-indeling klopt.
"""

from __future__ import annotations

import copy
import json
import sys
from collections import Counter
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
    # ondergrens (V-003): nooit groen op nul lijsten; I01 en I02 blijven er altijd
    assert {"I01_TRUENAS_BASIS.checklist.yaml", "I02_BACKUP_EN_RESTORE.checklist.yaml"} <= set(uitkomst)
    assert set(uitkomst) == {p.name for p in cl.lijsten()}
    assert uitkomst == {naam: [] for naam in uitkomst}
    taken = cl.excel_taken()
    documenten: Counter[str] = Counter()
    for naam in uitkomst:
        data = _load(naam)
        document = data["document"]
        documenten[document] += 1
        # hoort bij precies één document onder 03_INSTALLATIE/, dat bestaat en Excel-taken heeft
        assert document.startswith("03_INSTALLATIE/") and (cl.DOCS / document).is_file(), (naam, document)
        assert naam == Path(document).stem + ".checklist.yaml", (naam, document)
        # aantal stappen = aantal Excel-taken van dat document (geen vast getal)
        assert len(data["stappen"]) == len(taken[document]) > 0, naam
        assert [s["id"] for s in data["stappen"]] == [t["ID"] for t in taken[document]], naam
        assert data["installatie"] == "geen" and data["mutaties_door_platform"] is False
    # geen document heeft twee lijsten
    assert all(n == 1 for n in documenten.values()), documenten


def _kopie(map_: Path, naam: str, doel: str | None = None, wijzig: Any = None) -> Path:
    """Proefkopie van een bestaande lijst in een eigen map (nooit in install/checklists/, V-022)."""
    data = copy.deepcopy(_load(naam))
    if wijzig is not None:
        wijzig(data)
    pad = map_ / (doel or naam)
    pad.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return pad


LIJSTEN = [p.name for p in cl.lijsten()]


def test_copies_of_the_real_lists_pass_in_a_separate_folder(tmp_path: Path) -> None:
    """Controle op de proefmap zelf: zonder overtreding is alles groen (anders bewijzen de tests hieronder niets)."""
    for naam in LIJSTEN:
        _kopie(tmp_path, naam)
    assert cl.controleer_alle(tmp_path) == {naam: [] for naam in LIJSTEN}
    assert cl.main(tmp_path) == 0


@pytest.mark.parametrize("naam", LIJSTEN)
def test_a_copy_with_one_step_less_than_the_excel_tasks_fails(tmp_path: Path, naam: str) -> None:
    _kopie(tmp_path, naam, wijzig=lambda d: d["stappen"].pop())
    fout = cl.controleer_alle(tmp_path)[naam]
    assert any("dekken de Excel-taken niet exact" in f for f in fout), fout
    assert cl.main(tmp_path) == 1


@pytest.mark.parametrize("naam", LIJSTEN)
def test_a_second_list_for_the_same_document_fails(tmp_path: Path, naam: str) -> None:
    _kopie(tmp_path, naam)
    tweede = _kopie(tmp_path, naam, doel=naam.replace(".checklist.yaml", "_TWEEDE.checklist.yaml"))
    uitkomst = cl.controleer_alle(tmp_path)
    for bestand in (naam, tweede.name):
        assert any("heeft twee lijsten" in f for f in uitkomst[bestand]), (bestand, uitkomst)
    assert cl.main(tmp_path) == 1


def test_a_list_whose_file_name_does_not_match_its_document_fails(tmp_path: Path) -> None:
    _kopie(tmp_path, "I01_TRUENAS_BASIS.checklist.yaml", doel="I03_MEDIAGUARD.checklist.yaml")
    fout = cl.controleer_alle(tmp_path)["I03_MEDIAGUARD.checklist.yaml"]
    assert any("bestandsnaam past niet bij het document" in f for f in fout), fout
    assert cl.main(tmp_path) == 1


def test_a_list_for_a_document_that_does_not_exist_fails(tmp_path: Path) -> None:
    def wijzig(d: dict[str, Any]) -> None:
        d["document"] = "03_INSTALLATIE/I01_BESTAAT_NIET.md"

    _kopie(tmp_path, "I01_TRUENAS_BASIS.checklist.yaml", doel="I01_BESTAAT_NIET.checklist.yaml", wijzig=wijzig)
    fout = cl.controleer_alle(tmp_path)["I01_BESTAAT_NIET.checklist.yaml"]
    assert any("bestaat niet" in f for f in fout), fout


def test_an_empty_folder_is_never_green(tmp_path: Path) -> None:
    assert cl.controleer_alle(tmp_path) == {}
    assert cl.main(tmp_path) == 1


def _mutate(naam: str, wijzig: Any) -> list[str]:
    data = copy.deepcopy(_load(naam))
    wijzig(data)
    return cl.controleer_lijst(data, cl.excel_taken())


def _stap(d: dict[str, Any], index: int) -> dict[str, Any]:
    """Stap ``index``, of de laatste als de lijst korter is (I20 heeft 5 stappen; I01 met 8 houdt dezelfde stap)."""
    stappen: list[dict[str, Any]] = d["stappen"]
    return stappen[min(index, len(stappen) - 1)]


def _set_method(d: dict[str, Any], naam: str) -> None:
    _stap(d, 0).setdefault("truenas_methoden", []).append({"naam": naam, "versies": ["25.10"]})


def _onbekend_besluit(d: dict[str, Any]) -> None:
    vrij = [b for b in (f"D{n:02d}" for n in range(18, 0, -1)) if b not in d["besluiten"]]
    assert vrij, "alle besluiten D01-D18 staan al in de lijst"
    _stap(d, 0)["besluiten"] = [vrij[0]]


MUTATIES = [
    ("schrijvende methode", lambda d: _set_method(d, "app.create"), "geen alleen-lezen-methode"),
    ("snapshot maken", lambda d: _set_method(d, "pool.snapshot.create"), "geen alleen-lezen-methode"),
    ("dataset-rechten", lambda d: _set_method(d, "filesystem.setperm"), "geen alleen-lezen-methode"),
    ("stap ontbreekt", lambda d: d["stappen"].pop(), "dekken de Excel-taken niet exact"),
    ("acceptatie herschreven", lambda d: _stap(d, 1).update(acceptatie_nl="Alles groen."), "acceptatie ≠ Excel"),
    ("terminal", lambda d: _stap(d, 2).update(uitvoeren_nl="Voer `ls -l /dev/dri` uit op de NAS."),
     "terminalopdracht"),
    ("voorbeeld-IP", lambda d: _stap(d, 3).update(uitvoeren_nl="Verbind met wss://192.168.1.10/api/current."),
     "voorbeeldwaarde"),
    ("voorbeeldpool", lambda d: _stap(d, 6).update(uitvoeren_nl="Maak datasets onder /mnt/tank/apps aan."),
     "voorbeeldwaarde"),
    ("alleen Sim-getest", lambda d: _stap(d, 0).update(validatie_nu=["Sim-getest"]), "nooit alleen Sim-getest"),
    ("installeert iets", lambda d: d.update(installatie="custom_app"), "schema"),
    ("muteert", lambda d: d.update(mutaties_door_platform=True), "schema"),
    ("besluit onbekend", _onbekend_besluit, "besluit niet in de lijst"),
]


@pytest.mark.parametrize("naam", LIJSTEN)
@pytest.mark.parametrize(("beschrijving", "wijzig", "verwacht"), MUTATIES, ids=[m[0] for m in MUTATIES])
def test_violations_are_detected(naam: str, beschrijving: str, wijzig: Any, verwacht: str) -> None:
    fout = _mutate(naam, wijzig)
    assert any(verwacht in f for f in fout), (naam, beschrijving, fout)


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
    for pad in cl.lijsten():
        for stap in _load(pad.name)["stappen"]:
            for m in stap.get("truenas_methoden", []):
                antwoord = call(m["naam"])
                if familie in m["versies"]:
                    assert "result" in antwoord, (stap["id"], m["naam"], familie, antwoord.get("error"))
                else:
                    gezien_elders.add(m["naam"])
                    assert antwoord.get("error", {}).get("code") == -32601, (m["naam"], familie)
    # de versieverschillen uit TRUENAS_APP_API.md §6 zitten echt in de lijsten
    assert gezien_elders, "geen versie-afhankelijke methode getest"


def test_checklists_are_kind_checklist_for_the_appstore_validator() -> None:
    """Contract 1.3 §9 (C1.3-JLP-09): elke controlelijst is documentsoort ``kind: checklist`` (``install.method:
    none``); ``jl-manifest checklist`` accepteert ze en de manifestvalidator weigert ze als installatie."""
    import os
    import subprocess

    validator = os.environ.get("JL_MANIFEST")
    if not validator or not Path(validator).is_file():
        if os.environ.get("MANIFEST_VALIDATOR_VERPLICHT") == "1":
            pytest.fail("jl-manifest niet gevonden (JL_MANIFEST)")
        pytest.skip("jl-manifest (appstore-en-fork) niet beschikbaar; in CI verplicht")
    for pad in cl.lijsten():
        data = yaml.safe_load(pad.read_text(encoding="utf-8"))
        assert data["kind"] == "checklist" and data["install"] == {"method": "none"}
        goed = json.loads(subprocess.run([validator, "checklist", str(pad), "--format", "json"], capture_output=True,
                                         text=True, check=False).stdout)
        assert goed["valid"] is True and goed["errors"] == 0, (pad.name, goed["findings"])
        als_manifest = json.loads(subprocess.run([validator, "validate", "--strict", str(pad), "--format", "json"],
                                                 capture_output=True, text=True, check=False).stdout)
        assert als_manifest["valid"] is False
        assert "checklist_not_installable" in {f["code"] for f in als_manifest["findings"]}
