"""Byte-gelijke kopie van de manifeststandaard (validator, TrueNAS-vertaling en conflictanalyse) van de eigenaar.

Eigenaar van de manifeststandaard (B08-001, contract 1.3 §9) is ``lvos04/appstore-en-fork``
(``apps/api/src/appstore/manifest/``). De lifecycle-planner van deze repo (B08-003/004) valideert en vertaalt elk
manifest **zelf** opnieuw (verdediging in de diepte) met exact dezelfde code, zonder die code dubbel te bouwen
(ADR-0021). Daarom staat in ``apps/api/src/appstore/`` een **byte-gelijke** kopie van ``appstore/__init__.py`` en de
map ``appstore/manifest/`` (Python en JSON-schema's), met de herkomst in ``apps/api/src/appstore/KOPIE.json``:
bron-repo, vaste bron-commit en per bestand de sha256. Een kopie wordt nooit lokaal aangepast.

Gebruik (alleen ontwikkelaars; de bron wordt alleen gelezen)::

    python tools/validator_kopie.py controleer
    python tools/validator_kopie.py bijwerken --bron ../appstore-en-fork [--ref <commit|branch>]

``bijwerken`` leest met ``git show <ref>:<pad>`` (alleen gecommitte inhoud) en schrijft de kopie en de pin opnieuw.
``tests/repo/test_validator_kopie.py`` faalt bij een handmatige wijziging, een extra of ontbrekend bestand en een pin
die niet bij de bron past; een nieuwere bron geeft alleen een waarschuwing (overnemen is een bewuste stap).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KOPIE_DIR = ROOT / "apps" / "api" / "src" / "appstore"
PIN = KOPIE_DIR / "KOPIE.json"
BRON_REPO = "lvos04/appstore-en-fork"
BRON_DIR = "apps/api/src/appstore"
# Alleen deze bestanden horen bij de kopie (relatief aan BRON_DIR / KOPIE_DIR); de rest van de appstore-dienst niet.
PATROON = re.compile(r"^(__init__\.py|manifest/[A-Za-z0-9_.-]+\.(py|json))$")


class KopieFout(Exception):
    """De kopie of de pin is niet in orde."""


@dataclass(frozen=True)
class Pin:
    repo: str
    commit: str
    sha256: dict[str, str]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lees_pin(pin: Path = PIN) -> Pin:
    try:
        raw = json.loads(pin.read_text(encoding="utf-8"))
        result = Pin(str(raw["bron_repo"]), str(raw["bron_commit"]),
                     {str(k): str(v["sha256"]) for k, v in dict(raw["bestanden"]).items()})
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise KopieFout(f"{pin.name} ontbreekt of is onleesbaar ({type(exc).__name__})") from exc
    if result.repo != BRON_REPO or not re.fullmatch(r"[0-9a-f]{40}", result.commit):
        raise KopieFout(f"{pin.name}: bron moet {BRON_REPO} op een volledige commit-SHA zijn")
    if not result.sha256 or any(not PATROON.fullmatch(n) for n in result.sha256):
        raise KopieFout(f"{pin.name}: alleen __init__.py en manifest/*.py|*.json horen in de kopie")
    if any(not re.fullmatch(r"[0-9a-f]{64}", s) for s in result.sha256.values()):
        raise KopieFout(f"{pin.name}: elke sha256 moet 64 hex-tekens zijn")
    return result


def lokale_bestanden(kopie_dir: Path = KOPIE_DIR) -> set[str]:
    """Bestanden in de kopiemap die onder het patroon vallen (``__pycache__`` en de pin zelf niet)."""
    out = set()
    for pad in kopie_dir.rglob("*"):
        if pad.is_file() and "__pycache__" not in pad.parts:
            rel = pad.relative_to(kopie_dir).as_posix()
            if rel != PIN.name:
                out.add(rel)
    return out


def controleer_kopie(kopie_dir: Path = KOPIE_DIR, pin_path: Path | None = None) -> Pin:
    """Elk gepind bestand heeft exact de gepinde sha256; er staat niets extra's in de kopiemap."""
    pin = lees_pin(pin_path or kopie_dir / PIN.name)
    aanwezig = lokale_bestanden(kopie_dir)
    if extra := sorted(aanwezig - set(pin.sha256)):
        raise KopieFout(f"bestanden in de kopie die niet in de pin staan: {', '.join(extra)}")
    if ontbreekt := sorted(set(pin.sha256) - aanwezig):
        raise KopieFout(f"gepinde bestanden ontbreken in de kopie: {', '.join(ontbreekt)}")
    for naam, verwacht in sorted(pin.sha256.items()):
        if sha256((kopie_dir / naam).read_bytes()) != verwacht:
            raise KopieFout(f"{naam} wijkt af van de gepinde sha256 (lokaal gewijzigd? alleen 'bijwerken' mag)")
    return pin


def _git(bron: Path, *args: str) -> bytes:
    res = subprocess.run(["git", "-C", str(bron), *args], capture_output=True, check=False)  # noqa: S603, S607
    if res.returncode != 0:
        raise KopieFout(f"git {' '.join(args)} faalde: {res.stderr.decode(errors='replace').strip()[:200]}")
    return res.stdout


def bron_bestanden(bron: Path, ref: str) -> dict[str, bytes]:
    """Alle bestanden van de kopie zoals ze in de bron op ``ref`` staan (alleen gecommitte inhoud)."""
    namen = _git(bron, "ls-tree", "-r", "--name-only", ref, "--", BRON_DIR).decode().split()
    out: dict[str, bytes] = {}
    for pad in namen:
        rel = pad.removeprefix(BRON_DIR + "/")
        if PATROON.fullmatch(rel):
            out[rel] = _git(bron, "show", f"{ref}:{pad}")
    if "manifest/translate.py" not in out or "manifest/manifest-v1.schema.json" not in out:
        raise KopieFout(f"{ref}: geen manifeststandaard onder {BRON_DIR}/manifest")
    return out


def controleer_tegen_bron(bron: Path, ref: str = "HEAD", pin_path: Path = PIN) -> tuple[Pin, bool]:
    """De pin-commit bestaat in de bron en de bestanden daar zijn byte-gelijk aan de pin. Geeft ook terug of de bron
    op ``ref`` nieuwer (anders) is dan de pin — dat is alleen een waarschuwing."""
    pin = lees_pin(pin_path)
    op_pin = bron_bestanden(bron, pin.commit)
    if {k: sha256(v) for k, v in op_pin.items()} != pin.sha256:
        raise KopieFout(f"pin past niet bij {BRON_REPO}@{pin.commit[:12]} (andere bestanden of andere inhoud)")
    nu = bron_bestanden(bron, ref)
    return pin, {k: sha256(v) for k, v in nu.items()} != pin.sha256


def bijwerken(bron: Path, ref: str, kopie_dir: Path = KOPIE_DIR) -> Pin:
    commit = _git(bron, "rev-parse", f"{ref}^{{commit}}").decode().strip()
    bestanden = bron_bestanden(bron, commit)
    for rel in lokale_bestanden(kopie_dir) - set(bestanden):
        (kopie_dir / rel).unlink()
    for rel, data in bestanden.items():
        doel = kopie_dir / rel
        doel.parent.mkdir(parents=True, exist_ok=True)
        doel.write_bytes(data)
    pin = {
        "uitleg": "Herkomst van de byte-gelijke kopie van de manifeststandaard (B08-001, eigenaar appstore-en-fork; "
                  "ADR-0021). Niet met de hand wijzigen: python tools/validator_kopie.py bijwerken --bron "
                  "<appstore-en-fork> [--ref <commit>].",
        "bron_repo": BRON_REPO,
        "bron_commit": commit,
        "bron_map": BRON_DIR,
        "bestanden": {rel: {"sha256": sha256(data)} for rel, data in sorted(bestanden.items())},
    }
    (kopie_dir / PIN.name).write_text(json.dumps(pin, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return lees_pin(kopie_dir / PIN.name)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("controleer")
    b = sub.add_parser("bijwerken")
    b.add_argument("--bron", required=True, type=Path)
    b.add_argument("--ref", default="HEAD")
    args = p.parse_args(argv)
    try:
        if args.cmd == "controleer":
            pin = controleer_kopie()
            print(f"kopie in orde: {len(pin.sha256)} bestanden van {pin.repo}@{pin.commit[:12]}")
        else:
            pin = bijwerken(args.bron, args.ref)
            print(f"bijgewerkt naar {pin.repo}@{pin.commit}")
    except KopieFout as exc:
        print(f"FOUT: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
