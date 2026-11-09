"""Peil-kopie (ONTWERPTAAL §13.2/§14, O-14): de versie-gepinde kopie van het statuspaginasjabloon in
``design/ontwerptaal/`` is intact, de dienst serveert precies die kopie, en er is geen drift met de bron in
``lvos04/ai-server-manager`` (gecommitte inhoud, gelezen met ``git show``).

Bron: ``PEIL_BRON`` (git-checkout van ai-server-manager; standaard de naburige map ``../ai-server-manager``) en
``PEIL_BRON_REF`` (standaard ``HEAD``). In CI zet de workflow ``PEIL_BRON_VERPLICHT=1``: dan faalt de test als de bron
ontbreekt in plaats van over te slaan.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import peil_kopie as pk  # noqa: E402

BRON = Path(os.environ.get("PEIL_BRON") or ROOT.parent / "ai-server-manager")
BRON_REF = os.environ.get("PEIL_BRON_REF", "HEAD")
VERPLICHT = os.environ.get("PEIL_BRON_VERPLICHT") == "1"


def bron_beschikbaar() -> bool:
    ok = (BRON / ".git").exists() and subprocess.run(
        ["git", "-C", str(BRON), "rev-parse", "--verify", f"{BRON_REF}^{{commit}}"], capture_output=True, check=False
    ).returncode == 0
    if not ok and VERPLICHT:
        pytest.fail(f"Peil-bron niet beschikbaar in {BRON} ({BRON_REF}); CI moet ai-server-manager ophalen")
    if not ok:
        pytest.skip(f"Peil-bron niet naast deze repo ({BRON}); in CI verplicht")
    return ok


def test_kopie_is_intact_and_is_what_the_service_serves() -> None:
    from lifecycle.peil import PEIL_VERSIE

    herkomst = pk.controleer_kopie()
    assert {h.versie for h in herkomst.values()} == {PEIL_VERSIE}
    assert len({h.commit for h in herkomst.values()}) == 1


@pytest.mark.parametrize("schade", ["inhoud", "kop", "runtime", "css-in-html"])
def test_tampering_with_the_copy_is_detected(tmp_path: Path, schade: str) -> None:
    kopie = tmp_path / "kopie"
    shutil.copytree(pk.KOPIE_DIR, kopie, ignore=shutil.ignore_patterns("*.md"))
    runtime = tmp_path / "statuspagina.html"
    shutil.copy(pk.RUNTIME_HTML, runtime)
    pk.controleer_kopie(kopie, runtime)  # onbeschadigd: in orde
    html = kopie / "statuspagina.html"
    if schade == "inhoud":
        html.write_text(html.read_text(encoding="utf-8").replace("Koppelen met de Hub", "Koppel nu"), encoding="utf-8")
    elif schade == "kop":
        html.write_text(html.read_text(encoding="utf-8").split("\n", 2)[2], encoding="utf-8")
    elif schade == "runtime":
        runtime.write_text(runtime.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    else:  # beide bestanden consistent gepind, maar de CSS in het sjabloon wijkt af van statuspagina.css
        css = kopie / "statuspagina.css"
        h, rest = pk.splits("statuspagina.css", css.read_bytes())
        nieuw = rest.replace(b"--primary:#123DA3", b"--primary:#FF0000", 1)
        css.write_bytes(pk.maak_kop("statuspagina.css", h.versie, h.commit, nieuw) + nieuw)
    with pytest.raises(pk.KopieFout):
        pk.controleer_kopie(kopie, runtime)


def test_pinned_sha256_is_the_source_at_the_pinned_commit() -> None:
    """De herkomstkop klopt: het bronbestand op de gepinde commit heeft precies de gepinde sha256."""
    bron_beschikbaar()
    for naam, h in pk.controleer_kopie().items():
        assert pk.sha256(pk.git_show(BRON, h.commit, h.pad)) == h.sha256, naam


def test_no_drift_with_the_current_source() -> None:
    """ONTWERPTAAL §13.2: CI vergelijkt de checksum met de versie in ai-server-manager en faalt bij drift."""
    bron_beschikbaar()
    for naam, h in pk.controleer_kopie().items():
        actueel = pk.git_show(BRON, BRON_REF, h.pad)
        if pk.sha256(actueel) != h.sha256:
            pytest.fail(
                f"Drift: {h.pad} in ai-server-manager ({BRON_REF}, Peil {pk.bron_versie(naam, actueel)}) wijkt af van "
                f"de gepinde kopie (Peil {h.versie} @ {h.commit[:12]}). Werk de kopie bij met "
                f"'python tools/peil_kopie.py bijwerken --bron <ai-server-manager>' en pas de statuspagina en de "
                f"tests aan de nieuwe versie aan."
            )
