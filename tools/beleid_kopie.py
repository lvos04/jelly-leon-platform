"""Byte-gelijke kopie van de app-regels van het Hub-beleid (``policy/apps.yaml`` van ``lvos04/ai-server-manager``).

De lifecycle-planner (B08-003/004) geeft voor een beschermde app (Emby, de mediaserver, MediaGuard; contract 1.3 §2.3
regels 3 en 7) nooit een stap vrij. Welke apps dat zijn en hoe de Hub ze herkent, staat alleen in het beleid van de Hub
(``policy/apps.yaml``, koppen ``herkenning`` en ``beschermd``). Deze repo houdt daarom in
``apps/api/src/lifecycle/beleid/apps.yaml`` een **byte-gelijke** kopie, met de herkomst in ``beleid/KOPIE.json``:
bron-repo, vaste bron-commit, bronpad en sha256 (review P2, S-P2-395, R-HARDE-EIS-2; ADR-0025). Een kopie wordt nooit
lokaal aangepast; de Hub blijft de eigenaar en weigert zelf ook.

Gebruik (alleen ontwikkelaars; de bron wordt alleen gelezen)::

    python tools/beleid_kopie.py controleer
    python tools/beleid_kopie.py bijwerken --bron ../ai-server-manager [--ref <commit|branch>]

``bijwerken`` leest met ``git show <ref>:policy/apps.yaml`` (alleen gecommitte inhoud) en schrijft de kopie en de pin.
``tests/repo/test_beleid_kopie.py`` faalt bij een handmatige wijziging en bij een pin die niet bij de bron past; een
nieuwere bron geeft alleen een waarschuwing (overnemen is een bewuste stap).
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
KOPIE = ROOT / "apps" / "api" / "src" / "lifecycle" / "beleid" / "apps.yaml"
PIN = KOPIE.with_name("KOPIE.json")
BRON_REPO = "lvos04/ai-server-manager"
BRON_PAD = "policy/apps.yaml"


class KopieFout(Exception):
    """De kopie of de pin is niet in orde."""


@dataclass(frozen=True)
class Pin:
    repo: str
    commit: str
    pad: str
    sha256: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def lees_pin(pin: Path = PIN) -> Pin:
    try:
        raw = json.loads(pin.read_text(encoding="utf-8"))
        result = Pin(str(raw["bron_repo"]), str(raw["bron_commit"]), str(raw["bron_pad"]), str(raw["sha256"]))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise KopieFout(f"{pin.name} ontbreekt of is onleesbaar ({type(exc).__name__})") from exc
    if result.repo != BRON_REPO or result.pad != BRON_PAD or not re.fullmatch(r"[0-9a-f]{40}", result.commit):
        raise KopieFout(f"{pin.name}: bron moet {BRON_REPO}:{BRON_PAD} op een volledige commit-SHA zijn")
    if not re.fullmatch(r"[0-9a-f]{64}", result.sha256):
        raise KopieFout(f"{pin.name}: sha256 moet 64 hex-tekens zijn")
    return result


def controleer_kopie(kopie: Path = KOPIE, pin_path: Path | None = None) -> Pin:
    """De kopie heeft exact de gepinde sha256."""
    pin = lees_pin(pin_path or kopie.with_name(PIN.name))
    try:
        data = kopie.read_bytes()
    except OSError as exc:
        raise KopieFout(f"{kopie.name} ontbreekt") from exc
    if sha256(data) != pin.sha256:
        raise KopieFout(f"{kopie.name} wijkt af van de gepinde sha256 (lokaal gewijzigd? alleen 'bijwerken' mag)")
    return pin


def bron_bestand(bron: Path, ref: str) -> bytes:
    res = subprocess.run(["git", "-C", str(bron), "show", f"{ref}:{BRON_PAD}"],  # noqa: S603, S607
                         capture_output=True, check=False)
    if res.returncode != 0:
        raise KopieFout(f"git show {ref}:{BRON_PAD} faalde: {res.stderr.decode(errors='replace').strip()[:200]}")
    return res.stdout


def controleer_tegen_bron(bron: Path, ref: str = "HEAD", pin_path: Path = PIN) -> tuple[Pin, bool]:
    """De pin past bij de bron op de gepinde commit; geeft ook terug of de bron op ``ref`` anders is (waarschuwing)."""
    pin = lees_pin(pin_path)
    if sha256(bron_bestand(bron, pin.commit)) != pin.sha256:
        raise KopieFout(f"pin past niet bij {BRON_REPO}@{pin.commit[:12]}:{BRON_PAD}")
    return pin, sha256(bron_bestand(bron, ref)) != pin.sha256


def bijwerken(bron: Path, ref: str, kopie: Path = KOPIE) -> Pin:
    res = subprocess.run(["git", "-C", str(bron), "rev-parse", f"{ref}^{{commit}}"],  # noqa: S603, S607
                         capture_output=True, check=False)
    if res.returncode != 0:
        raise KopieFout(f"onbekende ref {ref!r} in {bron}")
    commit = res.stdout.decode().strip()
    data = bron_bestand(bron, commit)
    kopie.parent.mkdir(parents=True, exist_ok=True)
    kopie.write_bytes(data)
    pin = {
        "uitleg": "Herkomst van de byte-gelijke kopie van de app-regels van het Hub-beleid (herkenning en beschermd; "
                  "ADR-0025). Niet met de hand wijzigen: python tools/beleid_kopie.py bijwerken --bron "
                  "<ai-server-manager> [--ref <commit>].",
        "bron_repo": BRON_REPO,
        "bron_commit": commit,
        "bron_pad": BRON_PAD,
        "sha256": sha256(data),
    }
    pin_pad = kopie.with_name(PIN.name)
    pin_pad.write_text(json.dumps(pin, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return lees_pin(pin_pad)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("controleer")
    b = sub.add_parser("bijwerken")
    b.add_argument("--bron", required=True, type=Path)
    b.add_argument("--ref", default="HEAD")
    args = p.parse_args(argv)
    try:
        if args.cmd == "controleer":
            pin = controleer_kopie()
            print(f"kopie in orde: {BRON_PAD} van {pin.repo}@{pin.commit[:12]}")
        else:
            pin = bijwerken(args.bron, args.ref)
            print(f"bijgewerkt naar {pin.repo}@{pin.commit}")
    except KopieFout as exc:
        print(f"FOUT: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
