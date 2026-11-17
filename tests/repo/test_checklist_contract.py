"""B-6: checklistwaarden volgen contract §11, in de lokale controle en de gepinde CLI."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import checklists as cl  # noqa: E402

HUB = "NIET GEVALIDEERD (echte Hub)"


def test_checklist_values_follow_section_11_and_both_schemas_match() -> None:
    tekst = (cl.DOCS / "PLATFORM_CONTRACTEN.md").read_text(encoding="utf-8")
    begin = tekst.index("## 11. Bewijs en Excel")
    p11 = tekst[begin:tekst.index("## 12.", begin)]
    tabel: set[str] = set()
    for regel in p11.splitlines():
        if regel.strip().startswith("| `"):
            tabel.update(re.findall(r"`([^`]+)`", regel.strip().split(" | ")[0]))
    echt = {v for v in tabel if v.startswith("NIET GEVALIDEERD (")} - {"NIET GEVALIDEERD (Mac/Xcode)"}
    assert HUB in echt and echt == cl.ECHT_NODIG
    runtime = ROOT / "apps/api/src/appstore/manifest/checklist-v1.schema.json"
    assert cl.SCHEMA.read_bytes() == runtime.read_bytes()
    schema = json.loads(cl.SCHEMA.read_text(encoding="utf-8"))
    waarden = set(schema["$defs"]["Stap"]["properties"]["validatie_nu"]["items"]["enum"])
    assert waarden == tabel - {"Intel-iGPU-getest (ontwikkelmachine)", "Sim-geslaagd", "Sim-mislukt"}


@pytest.mark.parametrize("schade", ["aanpak", "installatie", "eigenaar_repo", "versie"])
def test_local_checklist_requirements_survive_the_shared_schema(schade: str) -> None:
    data = yaml.safe_load((cl.CHECKLIST_DIR / "I01_TRUENAS_BASIS.checklist.yaml").read_text(encoding="utf-8"))
    assert cl.controleer_lijst(data, cl.excel_taken()) == []
    if schade in {"aanpak", "installatie"}:
        del data[schade]
    elif schade == "eigenaar_repo":
        data[schade] = "lvos04/andere-repo"
    else:
        methode = next(m for s in data["stappen"] for m in s.get("truenas_methoden", []))
        methode["versies"] = ["26.04"]
    assert cl.controleer_lijst(data, cl.excel_taken())


@pytest.mark.parametrize("waarden,geldig", [([HUB], True), (["Unit-getest", HUB], True),
                                         (["NIET GEVALIDEERD (andere Hub)"], False)])
def test_hub_value_is_accepted_without_claiming_a_live_test(
    tmp_path: Path, waarden: list[str], geldig: bool,
) -> None:
    data = yaml.safe_load((cl.CHECKLIST_DIR / "I01_TRUENAS_BASIS.checklist.yaml").read_text(encoding="utf-8"))
    data["stappen"][0]["validatie_nu"] = waarden
    assert (cl.controleer_lijst(data, cl.excel_taken()) == []) is geldig
    validator = os.environ.get("JL_MANIFEST")
    if not validator or not Path(validator).is_file():
        if os.environ.get("MANIFEST_VALIDATOR_VERPLICHT") == "1":
            pytest.fail("jl-manifest niet gevonden (JL_MANIFEST)")
        pytest.skip("jl-manifest niet beschikbaar; in CI verplicht")
    pad = tmp_path / "hub.checklist.yaml"
    pad.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    res = subprocess.run([validator, "checklist", str(pad), "--format", "json"], capture_output=True,
                         text=True, check=False)
    report = json.loads(res.stdout)
    assert res.returncode == (0 if geldig else 1), report["findings"]
    assert report["valid"] is geldig, report["findings"]
    if not geldig:
        assert any(f["code"] == "invalid_value" and f["path"] == "stappen[0].validatie_nu[0]"
                   for f in report["findings"])
