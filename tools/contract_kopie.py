"""Byte-gelijke kopie van het platformcontract en de conformiteitslijst (contract 1.3, kop en §10; C1.3-ALG-01).

Bron van waarheid: ``lvos04/ai-server-manager`` → ``docs/platform-v2/PLATFORM_CONTRACTEN.md`` en
``docs/platform-v2/CONTRACT_1.3_CONFORMITEIT.md``. Deze repo houdt in ``docs/platform-v2/`` een **byte-gelijke** kopie
(zonder herkomstkop: het contract verbiedt elke lokale wijziging) en legt de herkomst vast in
``docs/platform-v2/CONTRACT_KOPIE.json``: bron-repo, vaste bron-commit, contractversie en per bestand de sha256.

Gebruik (alleen ontwikkelaars; de bron wordt alleen gelezen)::

    python tools/contract_kopie.py controleer
    python tools/contract_kopie.py bijwerken --bron ../ai-server-manager [--ref <commit|branch>]

``bijwerken`` leest met ``git show <ref>:<pad>`` (alleen gecommitte inhoud), schrijft de kopieën en de pin opnieuw.
``tests/repo/test_contract_kopie.py`` faalt bij een handmatige wijziging, bij een pin die niet bij de bron past en bij
een andere tekst onder dezelfde contractversie in de bron (ADR-0019).
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
KOPIE_DIR = ROOT / "docs" / "platform-v2"
PIN = KOPIE_DIR / "CONTRACT_KOPIE.json"
BRON_REPO = "lvos04/ai-server-manager"
BRON_DIR = "docs/platform-v2"
CONTRACT = "PLATFORM_CONTRACTEN.md"
CONFORMITEIT = "CONTRACT_1.3_CONFORMITEIT.md"
BESTANDEN = (CONTRACT, CONFORMITEIT)
VERSIE_RE = re.compile(rb"^Versie: `contracts (\d+\.\d+)`", re.M)


class KopieFout(Exception):
    """De kopie of de pin is niet in orde."""


@dataclass(frozen=True)
class Pin:
    repo: str
    commit: str
    versie: str
    sha256: dict[str, str]


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def contractversie(data: bytes) -> str | None:
    """``1.3`` uit de regel ``Versie: `contracts 1.3``` van het contract (``None`` als die regel ontbreekt)."""
    m = VERSIE_RE.search(data)
    return m.group(1).decode() if m else None


def lees_pin(pin: Path = PIN) -> Pin:
    try:
        raw = json.loads(pin.read_text(encoding="utf-8"))
        bestanden = raw["bestanden"]
        result = Pin(str(raw["bron_repo"]), str(raw["bron_commit"]), str(raw["contractversie"]),
                     {naam: str(bestanden[naam]["sha256"]) for naam in BESTANDEN})
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise KopieFout(f"{pin.name} ontbreekt of is onleesbaar ({type(exc).__name__})") from exc
    if result.repo != BRON_REPO or not re.fullmatch(r"[0-9a-f]{40}", result.commit):
        raise KopieFout(f"{pin.name}: bron moet {BRON_REPO} op een volledige commit-SHA zijn")
    if set(bestanden) != set(BESTANDEN) or any(not re.fullmatch(r"[0-9a-f]{64}", s) for s in result.sha256.values()):
        raise KopieFout(f"{pin.name}: precies {', '.join(BESTANDEN)} met een sha256")
    return result


def controleer_kopie(kopie_dir: Path = KOPIE_DIR, pin_path: Path = PIN) -> Pin:
    """Elke kopie heeft exact de gepinde sha256 en de contractversie in de pin is die van de kopie."""
    pin = lees_pin(pin_path)
    for naam in BESTANDEN:
        data = (kopie_dir / naam).read_bytes()
        if sha256(data) != pin.sha256[naam]:
            raise KopieFout(f"{naam}: wijkt af van de gepinde bron ({pin.commit[:12]}); nooit lokaal aanpassen, "
                            f"opnieuw kopiëren met tools/contract_kopie.py bijwerken")
    versie = contractversie((kopie_dir / CONTRACT).read_bytes())
    if versie != pin.versie:
        raise KopieFout(f"contractversie in {CONTRACT} ({versie}) ≠ pin ({pin.versie})")
    return pin


def git_show(bron: Path, ref: str, pad: str) -> bytes:
    res = subprocess.run(["git", "-C", str(bron), "show", f"{ref}:{pad}"], capture_output=True, check=False)  # noqa: S603, S607
    if res.returncode != 0:
        raise KopieFout(f"{ref}:{pad} niet leesbaar in {bron}: {res.stderr.decode(errors='replace').strip()}")
    return res.stdout


def git_commit(bron: Path, ref: str) -> str:
    res = subprocess.run(["git", "-C", str(bron), "rev-parse", f"{ref}^{{commit}}"], capture_output=True,  # noqa: S603, S607
                         text=True, check=False)
    if res.returncode != 0:
        raise KopieFout(f"commit {ref} niet gevonden in {bron}")
    return res.stdout.strip()


def bijwerken(bron: Path, ref: str) -> str:
    commit = git_commit(bron, ref)
    inhoud = {naam: git_show(bron, commit, f"{BRON_DIR}/{naam}") for naam in BESTANDEN}
    versie = contractversie(inhoud[CONTRACT])
    if versie is None:
        raise KopieFout(f"geen regel 'Versie: `contracts x.y`' in {CONTRACT} op {commit}")
    for naam, data in inhoud.items():
        (KOPIE_DIR / naam).write_bytes(data)
    pin = {
        "uitleg": "Herkomst van de byte-gelijke kopieën van het platformcontract (C1.3-ALG-01). Niet met de hand "
                  "wijzigen: python tools/contract_kopie.py bijwerken --bron <ai-server-manager> [--ref <commit>].",
        "bron_repo": BRON_REPO,
        "bron_commit": commit,
        "contractversie": versie,
        "bestanden": {naam: {"pad": f"{BRON_DIR}/{naam}", "sha256": sha256(data)} for naam, data in inhoud.items()},
    }
    PIN.write_text(json.dumps(pin, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    controleer_kopie()
    return f"contracts {versie} @ {commit}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="actie", required=True)
    sub.add_parser("controleer")
    bij = sub.add_parser("bijwerken")
    bij.add_argument("--bron", type=Path, required=True, help="git-checkout van ai-server-manager (alleen lezen)")
    bij.add_argument("--ref", default="HEAD", help="commit of branch in de bron (standaard HEAD)")
    args = parser.parse_args(argv)
    try:
        if args.actie == "controleer":
            pin = controleer_kopie()
            print(f"kopie intact: contracts {pin.versie} @ {pin.commit}")
        else:
            print(f"bijgewerkt naar {bijwerken(args.bron, args.ref)}")
    except KopieFout as exc:
        print(f"FOUT: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
