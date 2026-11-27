"""De lokale taakvelden en het installatiedocument leiden naar het standaardformulier."""

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(("nr", "begrippen"), [(2, ("formulier", "hostpoort")), (3, ("formulier", "healthcheck"))])
def test_i05_uitvoering_en_acceptatie(nr, begrippen):
    rid = f"I05-05-0{nr}"
    docs = ROOT / "docs/platform-v2"
    taak, = [r for r in json.loads((docs / "EXCEL_TAKEN.json").read_text()) if r["ID"] == rid]
    md = (docs / "03_INSTALLATIE/I05_MY_HOUSE.md").read_text().split(f"### Stap {nr}.")[1].split("### Stap ")[0]
    lijst = yaml.safe_load((ROOT / "install/checklists/I05_MY_HOUSE.checklist.yaml").read_text())
    stap, = [s for s in lijst["stappen"] if s["id"] == rid]
    assert stap["acceptatie_nl"] == taak["Acceptatie"]
    assert taak["Uitvoering"] in md and taak["Acceptatie"] in md
    for tekst in (taak["Uitvoering"] + taak["Acceptatie"], stap["uitvoeren_nl"] + stap["acceptatie_nl"]):
        assert "yaml" not in tekst.lower() and "render_truenas" not in tekst.lower()
        assert all(b in tekst.lower() for b in begrippen)
