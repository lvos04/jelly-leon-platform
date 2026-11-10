"""Contract 1.3 (kop, §10; C1.3-ALG-01, C1.3-JLP-13): de kopieën van ``PLATFORM_CONTRACTEN.md`` en
``CONTRACT_1.3_CONFORMITEIT.md`` in ``docs/platform-v2/`` zijn byte-gelijk aan de bron op de vaste bron-commit uit
``docs/platform-v2/CONTRACT_KOPIE.json``.

Bron: ``CONTRACT_BRON`` (of ``PEIL_BRON``; git-checkout van ai-server-manager, standaard de naburige map) en
``CONTRACT_BRON_REF`` (standaard ``HEAD``). In CI zet de workflow ``PEIL_BRON_VERPLICHT=1``: dan faalt de test als de
bron ontbreekt in plaats van over te slaan.

Driftregel (ADR-0019, naar Peil 1.1.1 E08): een afwijking van de pin faalt altijd; in de bron een **andere tekst onder
dezelfde contractversie** faalt ook (dan is "1.3" niet meer één tekst). Een nieuwere contractversie of een bijgewerkte
conformiteitslijst in de bron geeft een **waarschuwing**: overnemen is een bewuste stap van de orkestratie
(``tools/contract_kopie.py bijwerken``), geen rode CI door werk in een andere repo.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import contract_kopie as ck  # noqa: E402

BRON = Path(os.environ.get("CONTRACT_BRON") or os.environ.get("PEIL_BRON") or ROOT.parent / "ai-server-manager")
BRON_REF = os.environ.get("CONTRACT_BRON_REF", "HEAD")
VERPLICHT = "1" in {os.environ.get("CONTRACT_BRON_VERPLICHT"), os.environ.get("PEIL_BRON_VERPLICHT")}


def bron_beschikbaar() -> None:
    ok = (BRON / ".git").exists() and subprocess.run(
        ["git", "-C", str(BRON), "rev-parse", "--verify", f"{BRON_REF}^{{commit}}"], capture_output=True, check=False
    ).returncode == 0
    if not ok and VERPLICHT:
        pytest.fail(f"contractbron niet beschikbaar in {BRON} ({BRON_REF}); CI moet ai-server-manager ophalen")
    if not ok:
        pytest.skip(f"contractbron niet naast deze repo ({BRON}); in CI verplicht")


def test_kopie_is_byte_gelijk_aan_de_pin() -> None:
    pin = ck.controleer_kopie()
    assert pin.versie == "1.3"
    contract = (ck.KOPIE_DIR / ck.CONTRACT).read_bytes()
    assert b"Versie: `contracts 1.3`" in contract
    lijst = (ck.KOPIE_DIR / ck.CONFORMITEIT).read_text(encoding="utf-8")
    assert "## 4. jelly-leon-platform" in lijst and "| C1.3-JLP-13 |" in lijst
    # CONTRACT_1.2_CONFORMITEIT.md mag als historie blijven, maar is niet meer het bindende werkdocument
    readme = (ck.KOPIE_DIR / "README.md").read_text(encoding="utf-8")
    assert "contracts 1.3" in readme and "CONTRACT_1.3_CONFORMITEIT.md" in readme


@pytest.mark.parametrize("schade", ["contract", "conformiteit", "pin-commit", "pin-versie", "pin-ontbreekt"])
def test_lokale_wijziging_wordt_gevonden(tmp_path: Path, schade: str) -> None:
    kopie = tmp_path / "pv2"
    kopie.mkdir()
    for naam in ck.BESTANDEN:
        shutil.copy(ck.KOPIE_DIR / naam, kopie / naam)
    pin = tmp_path / "pin.json"
    shutil.copy(ck.PIN, pin)
    ck.controleer_kopie(kopie, pin)  # onbeschadigd: in orde
    if schade == "contract":  # "een link repareren" telt ook als lokale wijziging
        p = kopie / ck.CONTRACT
        p.write_bytes(p.read_bytes().replace(b"ai-server-manager:docs", b"../ai-server-manager/docs", 1))
    elif schade == "conformiteit":
        p = kopie / ck.CONFORMITEIT
        p.write_bytes(p.read_bytes() + b"\n")
    else:
        raw = json.loads(pin.read_text(encoding="utf-8"))
        if schade == "pin-commit":
            raw["bron_commit"] = "main"
        elif schade == "pin-versie":
            raw["contractversie"] = "1.2"
        else:
            del raw["bestanden"][ck.CONFORMITEIT]
        pin.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ck.KopieFout):
        ck.controleer_kopie(kopie, pin)


def test_pin_is_de_bron_op_de_gepinde_commit() -> None:
    """De pin klopt: op de vaste bron-commit hebben beide bronbestanden precies de gepinde sha256."""
    bron_beschikbaar()
    pin = ck.controleer_kopie()
    for naam in ck.BESTANDEN:
        assert ck.sha256(ck.git_show(BRON, pin.commit, f"{ck.BRON_DIR}/{naam}")) == pin.sha256[naam], naam


def _drift(pin: ck.Pin, contract: bytes, lijst: bytes) -> list[tuple[str, str]]:
    """(soort, melding) per verschil met de bron: ``fout`` of ``waarschuwing`` (driftregel van ADR-0019)."""
    uit: list[tuple[str, str]] = []
    versie = ck.contractversie(contract)
    if ck.sha256(contract) != pin.sha256[ck.CONTRACT]:
        if versie == pin.versie:
            uit.append(("fout", f"{ck.CONTRACT}: in de bron staat een andere tekst onder dezelfde versie "
                                f"contracts {versie}; neem de kopie opnieuw over (tools/contract_kopie.py bijwerken)"))
        else:
            uit.append(("waarschuwing", f"{ck.CONTRACT}: de bron is nu contracts {versie} (gepind: {pin.versie}); "
                                        "overnemen is een stap van de orkestratie"))
    if ck.sha256(lijst) != pin.sha256[ck.CONFORMITEIT]:
        uit.append(("waarschuwing", f"{ck.CONFORMITEIT}: de bron is bijgewerkt na {pin.commit[:12]}; overnemen is "
                                    "een stap van de orkestratie"))
    return uit


def test_driftregel_zelfde_versie_faalt_nieuwere_versie_waarschuwt() -> None:
    pin = ck.controleer_kopie()
    contract = (ck.KOPIE_DIR / ck.CONTRACT).read_bytes()
    lijst = (ck.KOPIE_DIR / ck.CONFORMITEIT).read_bytes()
    assert _drift(pin, contract, lijst) == []
    stil_gewijzigd = contract.replace(b"Peildatum 2026-11-10", b"Peildatum 2026-11-11", 1)
    assert [s for s, _ in _drift(pin, stil_gewijzigd, lijst)] == ["fout"]
    nieuwer = stil_gewijzigd.replace(b"Versie: `contracts 1.3`", b"Versie: `contracts 1.4`", 1)
    assert [s for s, _ in _drift(pin, nieuwer, lijst)] == ["waarschuwing"]
    assert [s for s, _ in _drift(pin, contract, lijst + b"\n")] == ["waarschuwing"]


def test_geen_drift_met_de_huidige_bron() -> None:
    bron_beschikbaar()
    pin = ck.controleer_kopie()
    contract = ck.git_show(BRON, BRON_REF, f"{ck.BRON_DIR}/{ck.CONTRACT}")
    lijst = ck.git_show(BRON, BRON_REF, f"{ck.BRON_DIR}/{ck.CONFORMITEIT}")
    bevindingen = _drift(pin, contract, lijst)
    for soort, melding in bevindingen:
        if soort == "waarschuwing":
            warnings.warn(melding, UserWarning, stacklevel=1)
    fouten = [m for s, m in bevindingen if s == "fout"]
    assert not fouten, fouten


def test_geen_relatieve_links_naar_de_bron_in_eigen_docs() -> None:
    """Contract 1.3 (kop): verwijzingen naar ai-server-manager als tekst ``ai-server-manager:<pad>`` of absolute URL;
    een relatieve link (``../ai-server-manager/…``) in een eigen document breekt buiten deze werkmap."""
    eigen = [p for p in (ROOT / "docs").rglob("*.md") if "platform-v2" not in p.parts or p.name == "README.md"]
    eigen += [ROOT / "README.md", ROOT / "install/truenas/INSTALLATIE.md", ROOT / "deploy/truenas/CUSTOM_APP.md"]
    link = re.compile(r"\]\((?:\.\./)+ai-server-manager/")
    fout = [str(p.relative_to(ROOT)) for p in eigen if link.search(p.read_text(encoding="utf-8"))]
    assert not fout, fout
