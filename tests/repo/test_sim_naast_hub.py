"""De Hub-stand-in (``e2e/simulators/hub/contract8.py``) naast het echte risicobeleid van de Hub (S-P2-420).

Risicoklasse, klasse bij een kritieke app, doelsoort en ``requires_backup`` per actie, en de lijst kritieke apps,
moeten gelijk zijn aan ``policy/acties.yaml`` en ``policy/apps.yaml`` van ai-server-manager. Een verschil wordt in de
stand-in hersteld, nooit in de Hub. Bron zoals in ``test_beleid_kopie.py`` (``BELEID_BRON``/``PEIL_BRON``, ref
``BELEID_BRON_REF``); met ``PEIL_BRON_VERPLICHT=1`` (CI) faalt de test als de bron ontbreekt.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "e2e" / "simulators" / "hub"))

import contract8 as c8  # noqa: E402

BRON = Path(os.environ.get("BELEID_BRON") or os.environ.get("PEIL_BRON") or ROOT.parent / "ai-server-manager")
BRON_REF = os.environ.get("BELEID_BRON_REF", "HEAD")
VERPLICHT = "1" in {os.environ.get("BELEID_BRON_VERPLICHT"), os.environ.get("PEIL_BRON_VERPLICHT")}


def _lees(pad: str) -> Any:
    r = subprocess.run(["git", "-C", str(BRON), "show", f"{BRON_REF}:{pad}"], capture_output=True, text=True,
                       check=False)
    if r.returncode != 0:
        if VERPLICHT:
            pytest.fail(f"{pad} niet leesbaar in {BRON} op {BRON_REF}")
        pytest.skip(f"bron ai-server-manager niet naast deze repo ({BRON}); in CI verplicht")
    return yaml.safe_load(r.stdout)


def vergelijk(stand_in: dict[str, tuple[str, str | None, str, bool]], beleid: dict[str, Any]) -> list[str]:
    """De verschillen tussen de allowlist van de stand-in en de acties van het Hub-beleid (leeg = gelijk)."""
    fouten = []
    for actie, (klasse, kritiek, doel, backup) in sorted(stand_in.items()):
        regel = beleid.get(actie)
        if not isinstance(regel, dict):
            fouten.append(f"{actie}: niet in het Hub-beleid")
            continue
        echt = (regel.get("klasse"), regel.get("klasse_kritiek"), regel.get("doel"), regel.get("requires_backup"))
        if echt != (klasse, kritiek, doel, backup):
            fouten.append(f"{actie}: stand-in {(klasse, kritiek, doel, backup)} != Hub {echt}")
    return fouten


def test_allowlist_en_kritieke_apps_gelijk_aan_het_hub_beleid() -> None:
    acties = _lees("policy/acties.yaml")["acties"]
    assert vergelijk(c8.ACTIES, acties) == []
    assert set(_lees("policy/apps.yaml")["kritiek"]) == set(c8.KRITIEKE_APPS)


def test_een_afwijkende_klasse_wordt_gezien() -> None:
    beleid = {"app.install": {"klasse": "R2", "klasse_kritiek": None, "doel": "app_nieuw", "requires_backup": False}}
    assert vergelijk({"app.install": ("R2", None, "app_nieuw", False)}, beleid) == []
    assert vergelijk({"app.install": ("R1", None, "app_nieuw", False)}, beleid)
    assert vergelijk({"app.update": ("R2", None, "app", True)}, beleid) == ["app.update: niet in het Hub-beleid"]
