"""Manifeststandaard (validator, TrueNAS-vertaling, conflictanalyse): de byte-gelijke kopie in
``apps/api/src/appstore/`` is intact en gelijk aan ``lvos04/appstore-en-fork`` op de gepinde commit (ADR-0021).

Bron: ``APPSTORE_BRON`` (git-checkout; standaard ``../appstore-en-fork``) en ``APPSTORE_BRON_REF`` (standaard
``HEAD``). Met ``VALIDATOR_BRON_VERPLICHT=1`` (CI) faalt de test als de bron ontbreekt. Pinregel zoals bij Peil en het
contract: de kopie is exact de bron op de gepinde commit (anders rood); een nieuwere bron geeft een waarschuwing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import validator_kopie as vk  # noqa: E402

BRON = Path(os.environ.get("APPSTORE_BRON") or ROOT.parent / "appstore-en-fork")
BRON_REF = os.environ.get("APPSTORE_BRON_REF", "HEAD")
VERPLICHT = os.environ.get("VALIDATOR_BRON_VERPLICHT") == "1"


def bron_beschikbaar() -> None:
    ok = (BRON / ".git").exists() and subprocess.run(
        ["git", "-C", str(BRON), "rev-parse", "--verify", f"{vk.lees_pin().commit}^{{commit}}"],
        capture_output=True, check=False,
    ).returncode == 0
    if not ok and VERPLICHT:
        pytest.fail(f"bron appstore-en-fork (met de gepinde commit) niet beschikbaar in {BRON}")
    if not ok:
        pytest.skip(f"bron appstore-en-fork niet naast deze repo ({BRON}); in CI verplicht")


def _kopie(tmp_path: Path) -> Path:
    doel = tmp_path / "appstore"
    shutil.copytree(vk.KOPIE_DIR, doel, ignore=shutil.ignore_patterns("__pycache__"))
    return doel


def test_kopie_is_intact() -> None:
    pin = vk.controleer_kopie()
    assert "manifest/translate.py" in pin.sha256 and "manifest/conflicts.py" in pin.sha256
    assert "manifest/manifest-v1.schema.json" in pin.sha256


@pytest.mark.parametrize("schade", ["inhoud", "extra", "ontbreekt", "pin-commit", "pin-vreemd-bestand"])
def test_schade_wordt_gezien(tmp_path: Path, schade: str) -> None:
    kopie = _kopie(tmp_path)
    vk.controleer_kopie(kopie)
    pin_pad = kopie / vk.PIN.name
    if schade == "inhoud":
        with (kopie / "manifest" / "rules.py").open("a", encoding="utf-8") as fh:
            fh.write("\n# lokaal aangepast\n")
    elif schade == "extra":
        (kopie / "manifest" / "eigen_regel.py").write_text("X = 1\n", encoding="utf-8")
    elif schade == "ontbreekt":
        (kopie / "manifest" / "conflicts.py").unlink()
    else:
        raw = json.loads(pin_pad.read_text(encoding="utf-8"))
        if schade == "pin-commit":
            raw["bron_commit"] = "HEAD"
        else:
            raw["bestanden"]["../lifecycle/app.py"] = {"sha256": "0" * 64}
        pin_pad.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(vk.KopieFout):
        vk.controleer_kopie(kopie)


def test_kopie_is_de_bron_op_de_gepinde_commit() -> None:
    bron_beschikbaar()
    pin, nieuwer = vk.controleer_tegen_bron(BRON, BRON_REF)
    assert pin.repo == "lvos04/appstore-en-fork"
    if nieuwer:
        warnings.warn(f"appstore-en-fork ({BRON_REF}) heeft een nieuwere manifeststandaard dan de gepinde kopie "
                      f"({pin.commit[:12]}); overnemen is een bewuste stap (tools/validator_kopie.py bijwerken)",
                      UserWarning, stacklevel=1)





def test_dienst_gebruikt_de_kopie_en_geen_eigen_regels() -> None:
    """De planner importeert de standaard uit ``appstore.manifest``; in ``lifecycle`` staat geen tweede validator."""
    bron = (ROOT / "apps" / "api" / "src" / "lifecycle" / "planner.py").read_text(encoding="utf-8")
    assert "from appstore.manifest" in bron
    lifecycle = ROOT / "apps" / "api" / "src" / "lifecycle"
    assert not list(lifecycle.rglob("*.schema.json")), "het manifestschema hoort alleen in de kopie"
