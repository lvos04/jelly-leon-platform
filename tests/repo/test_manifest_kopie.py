"""I23-manifesten (Hub en executor): de versie-gepinde kopieën in ``install/manifests/`` zijn intact en wijken niet
af van de bron in ``lvos04/ai-server-manager`` (``deploy/manifest/``, gelezen met ``git show``).

Bron: ``ASM_BRON`` (of ``PEIL_BRON``; git-checkout van ai-server-manager; standaard ``../ai-server-manager``) en
``ASM_BRON_REF`` (standaard ``HEAD``). Met ``PEIL_BRON_VERPLICHT=1`` (CI) faalt de test als de bron ontbreekt.
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

import manifest_kopie as mk  # noqa: E402

BRON = Path(os.environ.get("ASM_BRON") or os.environ.get("PEIL_BRON") or ROOT.parent / "ai-server-manager")
BRON_REF = os.environ.get("ASM_BRON_REF", "HEAD")
VERPLICHT = os.environ.get("PEIL_BRON_VERPLICHT") == "1"


def bron_beschikbaar() -> None:
    ok = (BRON / ".git").exists() and subprocess.run(
        ["git", "-C", str(BRON), "rev-parse", "--verify", f"{BRON_REF}^{{commit}}"], capture_output=True, check=False
    ).returncode == 0
    if not ok and VERPLICHT:
        pytest.fail(f"bron ai-server-manager niet beschikbaar in {BRON} ({BRON_REF})")
    if not ok:
        pytest.skip(f"bron ai-server-manager niet naast deze repo ({BRON}); in CI verplicht")


def test_copies_are_intact_and_from_one_commit() -> None:
    herkomst = mk.controleer_kopie()
    assert set(herkomst) == set(mk.BESTANDEN)


@pytest.mark.parametrize("schade", ["inhoud", "kop", "andere-commit"])
def test_tampering_is_detected(tmp_path: Path, schade: str) -> None:
    for naam in mk.BESTANDEN:
        shutil.copy(mk.KOPIE_DIR / naam, tmp_path / naam)
    mk.controleer_kopie(tmp_path)
    doel = tmp_path / mk.BESTANDEN[0]
    if schade == "inhoud":
        doel.write_text(doel.read_text(encoding="utf-8").replace("cpus: 2", "cpus: 8"), encoding="utf-8")
    elif schade == "kop":
        doel.write_text(doel.read_text(encoding="utf-8").split("\n", 2)[2], encoding="utf-8")
    else:
        _, rest = mk.splits(mk.BESTANDEN[0], doel.read_bytes())
        doel.write_bytes(mk.maak_kop(mk.BESTANDEN[0], "0" * 40, rest) + rest)
    with pytest.raises(mk.KopieFout):
        mk.controleer_kopie(tmp_path)


def test_pinned_sha256_is_the_source_at_the_pinned_commit() -> None:
    bron_beschikbaar()
    for naam, h in mk.controleer_kopie().items():
        assert mk.sha256(mk.git_show(BRON, h.commit, h.pad)) == h.sha256, naam


def test_no_drift_with_the_current_source() -> None:
    """Wijzigt ai-server-manager zijn manifest, dan faalt deze test tot de kopie is bijgewerkt
    (``python tools/manifest_kopie.py bijwerken --bron ../ai-server-manager``)."""
    bron_beschikbaar()
    for naam, h in mk.controleer_kopie().items():
        actueel = mk.git_show(BRON, BRON_REF, h.pad)
        assert mk.sha256(actueel) == h.sha256, (
            f"{naam}: ai-server-manager ({BRON_REF}) heeft een ander manifest dan de gepinde kopie "
            f"({h.commit[:12]}); werk de kopie bij"
        )
