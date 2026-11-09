"""Versie-gepinde kopie van de I23-manifesten van de Hub en de executor (eigenaar: ``lvos04/ai-server-manager``).

I23 = de twee handmatige Custom Apps van de eerste installatie (``install/truenas/INSTALLATIE.md``). De manifesten
zijn van ``ai-server-manager`` (``deploy/manifest/``); deze repo houdt in ``install/manifests/`` een kopie met een
**herkomstkop van 2 commentaarregels** (bronbestand met commit en sha256 van het bronbestand), zodat alle
installeerbare manifesten per I-document op één plek staan. Vanaf regel 3 is de kopie byte-gelijk aan de bron.

Gebruik (alleen ontwikkelaars; de bron wordt alleen gelezen)::

    python tools/manifest_kopie.py controleer
    python tools/manifest_kopie.py bijwerken --bron ../ai-server-manager [--ref <commit|branch>]

``bijwerken`` leest met ``git show <ref>:<pad>`` (alleen gecommitte inhoud) en schrijft de kopieën opnieuw.
``tests/repo/test_manifest_kopie.py`` faalt bij handmatige wijziging en bij drift met de bron.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KOPIE_DIR = ROOT / "install" / "manifests"
BRON_REPO = "lvos04/ai-server-manager"
BRON_DIR = "deploy/manifest"
BESTANDEN = ("ai-server-manager.manifest.yaml", "ai-server-manager-executor.manifest.yaml")

KOP_RE = re.compile(
    r"^# HERKOMST — I23-manifest · bron: (?P<repo>[\w.-]+/[\w.-]+)@(?P<commit>[0-9a-f]{40}):(?P<pad>\S+)"
    r" · sha256 van het bronbestand \(zonder deze kop\): (?P<sha>[0-9a-f]{64})$"
)
REGEL2 = ("# Versie-gepinde kopie (I23, installatie via install/truenas/INSTALLATIE.md). Niet met de hand wijzigen: "
          "tools/manifest_kopie.py bijwerken; CI faalt bij drift.")


class KopieFout(Exception):
    """De kopie is niet intact of wijkt af van de bron."""


@dataclass(frozen=True)
class Herkomst:
    repo: str
    commit: str
    pad: str
    sha256: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def maak_kop(naam: str, commit: str, bron: bytes) -> bytes:
    eerste = (f"# HERKOMST — I23-manifest · bron: {BRON_REPO}@{commit}:{BRON_DIR}/{naam} · sha256 van het "
              f"bronbestand (zonder deze kop): {sha256(bron)}")
    return f"{eerste}\n{REGEL2}\n".encode()


def splits(naam: str, data: bytes) -> tuple[Herkomst, bytes]:
    """Herkomstkop (2 regels) en de rest (byte-gelijk aan de bron). Gooit ``KopieFout``."""
    delen = data.split(b"\n", 2)
    if len(delen) != 3:
        raise KopieFout(f"{naam}: geen herkomstkop van 2 regels")
    m = KOP_RE.match(delen[0].decode("utf-8"))
    if not m or delen[1].decode("utf-8") != REGEL2:
        raise KopieFout(f"{naam}: herkomstkop ontbreekt of is ongeldig")
    if m["pad"] != f"{BRON_DIR}/{naam}" or m["repo"] != BRON_REPO:
        raise KopieFout(f"{naam}: herkomstkop verwijst naar een ander bronbestand ({m['repo']}:{m['pad']})")
    return Herkomst(m["repo"], m["commit"], m["pad"], m["sha"]), delen[2]


def controleer_kopie(kopie_dir: Path = KOPIE_DIR) -> dict[str, Herkomst]:
    """Elke kopie intact (sha256 van de rest = kop) en beide uit dezelfde commit."""
    herkomst: dict[str, Herkomst] = {}
    for naam in BESTANDEN:
        h, rest = splits(naam, (kopie_dir / naam).read_bytes())
        if sha256(rest) != h.sha256:
            raise KopieFout(f"{naam}: inhoud wijkt af van de gepinde sha256 (met de hand gewijzigd?)")
        herkomst[naam] = h
    if len({h.commit for h in herkomst.values()}) != 1:
        raise KopieFout("de I23-manifesten komen uit verschillende commits")
    return herkomst


def git_show(bron: Path, ref: str, pad: str) -> bytes:
    res = subprocess.run(["git", "-C", str(bron), "show", f"{ref}:{pad}"], capture_output=True, check=False)  # noqa: S603, S607
    if res.returncode != 0:
        raise KopieFout(f"{ref}:{pad} niet leesbaar in {bron}: {res.stderr.decode(errors='replace').strip()}")
    return res.stdout


def git_commit(bron: Path, ref: str) -> str:
    res = subprocess.run(  # noqa: S603
        ["git", "-C", str(bron), "rev-parse", "--verify", f"{ref}^{{commit}}"],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if res.returncode != 0:
        raise KopieFout(f"{ref} is geen commit in {bron}")
    return res.stdout.decode().strip()


def bijwerken(bron: Path, ref: str, kopie_dir: Path = KOPIE_DIR) -> str:
    commit = git_commit(bron, ref)
    for naam in BESTANDEN:
        data = git_show(bron, commit, f"{BRON_DIR}/{naam}")
        (kopie_dir / naam).write_bytes(maak_kop(naam, commit, data) + data)
    controleer_kopie(kopie_dir)
    return commit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("controleer", help="kopieën intact")
    b = sub.add_parser("bijwerken", help="kopieën opnieuw maken uit de bron (alleen gecommitte inhoud)")
    b.add_argument("--bron", type=Path, required=True)
    b.add_argument("--ref", default="HEAD")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "controleer":
            for naam, h in controleer_kopie().items():
                print(f"ok: {naam} = {h.repo}@{h.commit[:12]}")
        else:
            print(f"bijgewerkt naar {BRON_REPO}@{bijwerken(args.bron, args.ref)}")
    except KopieFout as exc:
        print(f"FOUT: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
