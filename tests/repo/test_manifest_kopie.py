"""Manifestkopieën van andere repos (I23 Hub en executor, I03 MediaGuard, I04 MSO, I05 my_house en I25 zoeken): de
kopieën in ``install/manifests/`` zijn intact en wijken niet af van de bron (``deploy/manifest/``, gelezen met
``git show``).

Bron per repo (git-checkout of kale clone): ``ASM_BRON`` (of ``PEIL_BRON``), ``MGU_BRON``, ``MSO_BRON``,
``MY_HOUSE_BRON`` en ``ZOEKEN_BRON``; standaard ``../<repo>``. ``<VAR>_REF`` (standaard ``HEAD``) is de stand voor een
nieuwere bron. Met ``PEIL_BRON_VERPLICHT=1`` (CI) faalt de pintest als een bron of de gepinde commit ontbreekt.

Pinregel (ADR-0019, naar Peil 1.1.1 E08): de kopie moet exact de bron op de **gepinde commit** zijn (anders rood). Een
nieuwere inhoud in de bron geeft een **waarschuwing**: overnemen is een bewuste stap (``tools/manifest_kopie.py
bijwerken`` + ``tools/manifesten.py`` met de validator), geen rode CI door een commit in een andere repo.
"""

from __future__ import annotations

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

import manifest_kopie as mk  # noqa: E402

VERPLICHT = os.environ.get("PEIL_BRON_VERPLICHT") == "1"
VAR = {"ai-server-manager": "ASM_BRON", "media-guard-ultimate": "MGU_BRON", "media-source-optimizer": "MSO_BRON",
       "my_house": "MY_HOUSE_BRON", "zoeken-en-persoonlijke-ai": "ZOEKEN_BRON"}
KOPIEN = {"ai-server-manager.manifest.yaml", "ai-server-manager-executor.manifest.yaml", "mediaguard.manifest.yaml",
          "media-source-optimizer.manifest.yaml", "my_house.manifest.yaml", "zoeken-en-persoonlijke-ai.manifest.yaml"}


def bron_pad(b: mk.Bron) -> Path:
    var = VAR[b.naam]
    extra = os.environ.get("PEIL_BRON") if var == "ASM_BRON" else None
    return Path(os.environ.get(var) or extra or ROOT.parent / b.naam)


def heeft_commit(bron: Path, ref: str) -> bool:
    return subprocess.run(["git", "-C", str(bron), "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
                          capture_output=True, check=False).returncode == 0


def _kopieer(doel: Path) -> None:
    for naam in mk.BESTANDEN:
        shutil.copy(mk.KOPIE_DIR / naam, doel / naam)


def test_every_source_is_copied_intact() -> None:
    herkomst = mk.controleer_kopie()
    assert set(herkomst) == set(mk.BESTANDEN) == KOPIEN
    assert {b.naam for b in mk.BRONNEN} == set(VAR)
    for naam, h in herkomst.items():
        assert h.repo == mk.bron_van(naam).repo and h.pad == f"{mk.BRON_DIR}/{naam}"


def test_existing_i23_header_keeps_its_form() -> None:
    """De twee I23-kopieën van vóór de uitbreiding blijven geldig met hun letterlijke kop."""
    for naam in ("ai-server-manager.manifest.yaml", "ai-server-manager-executor.manifest.yaml"):
        kop = (mk.KOPIE_DIR / naam).read_text(encoding="utf-8").split("\n", 2)
        assert kop[0].startswith("# HERKOMST — I23-manifest · bron: lvos04/ai-server-manager@")
        assert kop[1] == ("# Versie-gepinde kopie (I23, installatie via install/truenas/INSTALLATIE.md). Niet met de "
                          "hand wijzigen: tools/manifest_kopie.py bijwerken; CI faalt bij drift.")


@pytest.mark.parametrize("naam", sorted(KOPIEN))
@pytest.mark.parametrize("schade", ["een-teken", "geen-kop", "ander-document", "andere-repo"])
def test_tampering_is_detected(tmp_path: Path, naam: str, schade: str) -> None:
    _kopieer(tmp_path)
    mk.controleer_kopie(tmp_path)
    doel = tmp_path / naam
    data = doel.read_bytes()
    kop, rest = data.split(b"\n", 2)[:2], data.split(b"\n", 2)[2]
    if schade == "een-teken":
        i = rest.index(b"app_id: ") + len(b"app_id: ")
        doel.write_bytes(b"\n".join(kop) + b"\n" + rest[:i] + (b"X" if rest[i:i + 1] != b"X" else b"Y") + rest[i + 1:])
    elif schade == "geen-kop":
        doel.write_bytes(rest)
    elif schade == "ander-document":
        doel.write_bytes(re.sub(rb"I\d\d-manifest", b"I99-manifest", kop[0], count=1) + b"\n" + kop[1] + b"\n" + rest)
    else:
        doel.write_bytes(kop[0].replace(b"bron: lvos04/", b"bron: iemand/") + b"\n" + kop[1] + b"\n" + rest)
    with pytest.raises(mk.KopieFout):
        mk.controleer_kopie(tmp_path)


def test_missing_copy_fails(tmp_path: Path) -> None:
    _kopieer(tmp_path)
    (tmp_path / "my_house.manifest.yaml").unlink()
    with pytest.raises(mk.KopieFout, match="ontbreekt"):
        mk.controleer_kopie(tmp_path)


def test_files_of_one_source_must_share_a_commit(tmp_path: Path) -> None:
    _kopieer(tmp_path)
    naam = "ai-server-manager.manifest.yaml"
    _, rest = mk.splits(naam, (tmp_path / naam).read_bytes())
    (tmp_path / naam).write_bytes(mk.maak_kop(naam, "0" * 40, rest) + rest)
    with pytest.raises(mk.KopieFout, match="verschillende commits"):
        mk.controleer_kopie(tmp_path)


def test_one_changed_character_fails_on_the_sha256(tmp_path: Path) -> None:
    _kopieer(tmp_path)
    doel = tmp_path / "mediaguard.manifest.yaml"
    doel.write_bytes(doel.read_bytes().replace(b"app_id: mediaguard", b"app_id: mediaguarD", 1))
    with pytest.raises(mk.KopieFout, match="sha256"):
        mk.controleer_kopie(tmp_path)


@pytest.mark.parametrize("bron", mk.BRONNEN, ids=lambda b: b.naam)
def test_pinned_sha256_is_the_source_at_the_pinned_commit(bron: mk.Bron) -> None:
    pad = bron_pad(bron)
    herkomst = mk.controleer_kopie()
    commit = herkomst[bron.bestanden[0]].commit
    if not heeft_commit(pad, commit):
        if VERPLICHT:
            pytest.fail(f"bron {bron.naam} of gepinde commit {commit[:12]} niet beschikbaar in {pad}")
        pytest.skip(f"bron {bron.naam} niet beschikbaar in {pad}; in CI verplicht")
    for naam in bron.bestanden:
        h = herkomst[naam]
        assert mk.sha256(mk.git_show(pad, h.commit, h.pad)) == h.sha256, naam


@pytest.mark.parametrize("bron", mk.BRONNEN, ids=lambda b: b.naam)
def test_newer_source_only_warns(bron: mk.Bron) -> None:
    """Wijzigt de bron haar manifest na de gepinde commit, dan waarschuwt deze test (bijwerken met
    ``python tools/manifest_kopie.py bijwerken --repo <repo> --bron ../<repo>``); de pin zelf blijft hard gecontroleerd.
    Een bron zonder die stand (CI haalt alleen de gepinde commit op) slaat deze waarschuwing over."""
    pad, ref = bron_pad(bron), os.environ.get(f"{VAR[bron.naam]}_REF", "HEAD")
    if not heeft_commit(pad, ref):
        pytest.skip(f"{bron.naam}: stand {ref} niet beschikbaar in {pad}; alleen een waarschuwing")
    for naam in bron.bestanden:
        h = mk.controleer_kopie()[naam]
        actueel = mk.git_show(pad, ref, h.pad)
        if mk.sha256(actueel) != h.sha256:
            warnings.warn(f"{naam}: {bron.naam} ({ref}) heeft een nieuwer manifest dan de gepinde kopie "
                          f"({h.commit[:12]}); overnemen is een bewuste stap", UserWarning, stacklevel=1)
