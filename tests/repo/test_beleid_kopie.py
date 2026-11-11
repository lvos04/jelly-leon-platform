"""App-regels van het Hub-beleid (``herkenning`` en ``beschermd``): de byte-gelijke kopie in
``apps/api/src/lifecycle/beleid/apps.yaml`` is intact en gelijk aan ``lvos04/ai-server-manager`` ``policy/apps.yaml`` op
de gepinde commit (review P2, S-P2-395, R-HARDE-EIS-2; ADR-0025).

Bron: ``BELEID_BRON`` (of ``PEIL_BRON``; git-checkout van ai-server-manager, standaard de naburige map) en
``BELEID_BRON_REF`` (standaard ``HEAD``). Met ``PEIL_BRON_VERPLICHT=1`` (CI) faalt de test als de bron ontbreekt.
Pinregel zoals bij het contract en de validator: een afwijking van de pin is rood; een nieuwere bron geeft een
waarschuwing (overnemen is een bewuste stap: ``tools/beleid_kopie.py bijwerken``).
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
sys.path.insert(0, str(ROOT / "apps" / "api" / "src"))

import beleid_kopie as bk  # noqa: E402

from lifecycle import beschermd  # noqa: E402

BRON = Path(os.environ.get("BELEID_BRON") or os.environ.get("PEIL_BRON") or ROOT.parent / "ai-server-manager")
BRON_REF = os.environ.get("BELEID_BRON_REF", "HEAD")
VERPLICHT = "1" in {os.environ.get("BELEID_BRON_VERPLICHT"), os.environ.get("PEIL_BRON_VERPLICHT")}


def bron_beschikbaar() -> None:
    ok = (BRON / ".git").exists() and subprocess.run(
        ["git", "-C", str(BRON), "rev-parse", "--verify", f"{bk.lees_pin().commit}^{{commit}}"],
        capture_output=True, check=False,
    ).returncode == 0
    if not ok and VERPLICHT:
        pytest.fail(f"bron ai-server-manager (met de gepinde commit) niet beschikbaar in {BRON}")
    if not ok:
        pytest.skip(f"bron ai-server-manager niet naast deze repo ({BRON}); in CI verplicht")


def test_kopie_is_intact_en_de_planner_leest_haar() -> None:
    pin = bk.controleer_kopie()
    assert pin.pad == "policy/apps.yaml" and bk.KOPIE == beschermd.BELEID
    beleid = beschermd.beleid()
    regels = {r.id: r for r in beleid.beschermd}
    assert regels["APP-EMBY"].acties is None and regels["APP-EMBY"].code == "emby_nooit_door_platform"
    for rid in ("APP-MEDIASERVER-JL", "APP-MEDIASERVER-JF", "APP-MEDIAGUARD"):
        assert {"app.update", "app.rollback", "app.uninstall"} <= set(regels[rid].acties or ())
    assert beschermd.herken(beleid, "jelly-leon-platform") == ("jelly-leon-platform",)
    assert beschermd.herken(beleid, "kijkdoos", ["docker.io/emby/embyserver:4.9@sha256:" + "0" * 64]) == ("emby",)


@pytest.mark.parametrize("schade", ["inhoud", "ontbreekt", "pin-commit", "pin-pad", "pin-sha"])
def test_schade_wordt_gezien(tmp_path: Path, schade: str) -> None:
    kopie = tmp_path / "apps.yaml"
    shutil.copyfile(bk.KOPIE, kopie)
    shutil.copyfile(bk.PIN, tmp_path / bk.PIN.name)
    bk.controleer_kopie(kopie)
    pin_pad = tmp_path / bk.PIN.name
    raw = json.loads(pin_pad.read_text(encoding="utf-8"))
    if schade == "inhoud":
        with kopie.open("a", encoding="utf-8") as fh:
            fh.write("\n# lokaal aangepast\n")
    elif schade == "ontbreekt":
        kopie.unlink()
    elif schade == "pin-commit":
        raw["bron_commit"] = "HEAD"
    elif schade == "pin-pad":
        raw["bron_pad"] = "policy/acties.yaml"
    else:
        raw["sha256"] = "0" * 64
    pin_pad.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(bk.KopieFout):
        bk.controleer_kopie(kopie)


def test_onleesbaar_beleid_is_een_fout_geen_lege_lijst(tmp_path: Path) -> None:
    """Fail-closed: een kapotte kopie wordt nooit gelezen als "niets beschermd"."""
    for inhoud in ("", "schema: asm-policy/1\n", "herkenning: {}\nbeschermd: []\n", "herkenning: [\n"):
        pad = tmp_path / "apps.yaml"
        pad.write_text(inhoud, encoding="utf-8")
        with pytest.raises(beschermd.BeleidFout):
            beschermd._lees(pad)


def test_kopie_is_de_bron_op_de_gepinde_commit() -> None:
    bron_beschikbaar()
    pin, nieuwer = bk.controleer_tegen_bron(BRON, BRON_REF)
    assert pin.repo == "lvos04/ai-server-manager"
    if nieuwer:
        warnings.warn(f"ai-server-manager {BRON_REF} heeft een nieuwer policy/apps.yaml dan de gepinde kopie "
                      f"({pin.commit[:12]}); overnemen: python tools/beleid_kopie.py bijwerken", stacklevel=1)
