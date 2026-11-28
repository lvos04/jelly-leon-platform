"""Versie-gepinde kopieën van manifesten van andere repos: de I23-manifesten van de Hub en de executor
(``lvos04/ai-server-manager``), MediaGuard (I03), Media Source Optimizer (I04), my_house (I05) en de zoekdienst (I25).

De manifesten zijn van hun eigen repo (``deploy/manifest/``); deze repo houdt in ``install/manifests/`` een kopie met
een **herkomstkop van 2 commentaarregels** (I-document, bronbestand met commit en sha256 van het bronbestand), zodat
alle installeerbare manifesten per I-document op één plek staan. Vanaf regel 3 is de kopie byte-gelijk aan de bron.

Gebruik (alleen ontwikkelaars; de bron wordt alleen gelezen)::

    python tools/manifest_kopie.py controleer
    python tools/manifest_kopie.py bijwerken --repo <repo> --bron ../<repo> [--ref <commit|branch>]

``--repo`` is de naam uit ``BRONNEN`` (standaard ``ai-server-manager``). ``bijwerken`` leest met
``git show <ref>:<pad>`` (alleen gecommitte inhoud) en schrijft de kopieën van die bron opnieuw.
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
BRON_DIR = "deploy/manifest"


@dataclass(frozen=True)
class Bron:
    repo: str  # eigenaar op GitHub-naam, zoals in owner_repo van het manifest
    idoc: str  # I-document van de installatie; het woord in de kop is "<idoc>-manifest"
    bestanden: tuple[str, ...]
    regel2: str

    @property
    def naam(self) -> str:
        return self.repo.split("/", 1)[1]


def _regel2(idoc: str, document: str) -> str:
    return (f"# Versie-gepinde kopie ({idoc}, installatie via het formulier van de standaard Custom App; "
            f"docs/platform-v2/03_INSTALLATIE/{document}.md). Niet met de hand wijzigen: tools/manifest_kopie.py "
            "bijwerken; CI faalt bij drift.")


BRONNEN = (
    Bron("lvos04/ai-server-manager", "I23",
         ("ai-server-manager.manifest.yaml", "ai-server-manager-executor.manifest.yaml"),
         "# Versie-gepinde kopie (I23, installatie via install/truenas/INSTALLATIE.md). Niet met de hand wijzigen: "
         "tools/manifest_kopie.py bijwerken; CI faalt bij drift."),
    Bron("lvos04/media-guard-ultimate", "I03", ("mediaguard.manifest.yaml",), _regel2("I03", "I03_MEDIAGUARD")),
    Bron("lvos04/media-source-optimizer", "I04", ("media-source-optimizer.manifest.yaml",),
         _regel2("I04", "I04_MEDIA_SOURCE_OPTIMIZER")),
    Bron("lvos04/my_house", "I05", ("my_house.manifest.yaml",), _regel2("I05", "I05_MY_HOUSE")),
    Bron("lvos04/zoeken-en-persoonlijke-ai", "I25", ("zoeken-en-persoonlijke-ai.manifest.yaml",),
         _regel2("I25", "I25_SEARCH")),
)
PER_BESTAND = {naam: b for b in BRONNEN for naam in b.bestanden}
BESTANDEN = tuple(PER_BESTAND)

KOP_RE = re.compile(
    r"^# HERKOMST — (?P<idoc>I\d{2})-manifest · bron: (?P<repo>[\w.-]+/[\w.-]+)@(?P<commit>[0-9a-f]{40}):(?P<pad>\S+)"
    r" · sha256 van het bronbestand \(zonder deze kop\): (?P<sha>[0-9a-f]{64})$"
)


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


def bron_van(naam: str) -> Bron:
    if naam not in PER_BESTAND:
        raise KopieFout(f"{naam}: geen bekende manifestkopie")
    return PER_BESTAND[naam]


def maak_kop(naam: str, commit: str, bron: bytes) -> bytes:
    b = bron_van(naam)
    eerste = (f"# HERKOMST — {b.idoc}-manifest · bron: {b.repo}@{commit}:{BRON_DIR}/{naam} · sha256 van het "
              f"bronbestand (zonder deze kop): {sha256(bron)}")
    return f"{eerste}\n{b.regel2}\n".encode()


def splits(naam: str, data: bytes) -> tuple[Herkomst, bytes]:
    """Herkomstkop (2 regels) en de rest (byte-gelijk aan de bron). Gooit ``KopieFout``."""
    b = bron_van(naam)
    delen = data.split(b"\n", 2)
    if len(delen) != 3:
        raise KopieFout(f"{naam}: geen herkomstkop van 2 regels")
    m = KOP_RE.match(delen[0].decode("utf-8"))
    if not m or delen[1].decode("utf-8") != b.regel2:
        raise KopieFout(f"{naam}: herkomstkop ontbreekt of is ongeldig")
    if m["pad"] != f"{BRON_DIR}/{naam}" or m["repo"] != b.repo or m["idoc"] != b.idoc:
        raise KopieFout(f"{naam}: herkomstkop verwijst naar een ander bronbestand ({m['repo']}:{m['pad']})")
    return Herkomst(m["repo"], m["commit"], m["pad"], m["sha"]), delen[2]


def controleer_bestand(naam: str, kopie_dir: Path = KOPIE_DIR) -> Herkomst:
    """Eén kopie intact: herkomstkop geldig en sha256 van de rest gelijk aan de kop."""
    pad = kopie_dir / naam
    if not pad.is_file():
        raise KopieFout(f"{naam}: kopie ontbreekt")
    h, rest = splits(naam, pad.read_bytes())
    if sha256(rest) != h.sha256:
        raise KopieFout(f"{naam}: inhoud wijkt af van de gepinde sha256 (met de hand gewijzigd?)")
    return h


def controleer_kopie(kopie_dir: Path = KOPIE_DIR) -> dict[str, Herkomst]:
    """Elke kopie intact (sha256 van de rest = kop) en de bestanden van één bron uit dezelfde commit."""
    herkomst = {naam: controleer_bestand(naam, kopie_dir) for naam in BESTANDEN}
    for b in BRONNEN:
        if len({herkomst[n].commit for n in b.bestanden}) != 1:
            raise KopieFout(f"de manifesten van {b.repo} komen uit verschillende commits")
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


def bijwerken(bron: Path, ref: str, kopie_dir: Path = KOPIE_DIR, repo: str = "ai-server-manager") -> str:
    """Schrijf de kopieën van één bron opnieuw; de andere kopieën blijven ongemoeid."""
    gekozen = next((b for b in BRONNEN if b.naam == repo), None)
    if gekozen is None:
        raise KopieFout(f"onbekende bronrepo {repo}; bekend: {', '.join(b.naam for b in BRONNEN)}")
    commit = git_commit(bron, ref)
    for naam in gekozen.bestanden:
        data = git_show(bron, commit, f"{BRON_DIR}/{naam}")
        (kopie_dir / naam).write_bytes(maak_kop(naam, commit, data) + data)
        controleer_bestand(naam, kopie_dir)
    return commit


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("controleer", help="kopieën intact")
    b = sub.add_parser("bijwerken", help="kopieën van één bron opnieuw maken (alleen gecommitte inhoud)")
    b.add_argument("--repo", default="ai-server-manager", choices=[x.naam for x in BRONNEN])
    b.add_argument("--bron", type=Path, required=True)
    b.add_argument("--ref", default="HEAD")
    args = parser.parse_args(argv)
    try:
        if args.cmd == "controleer":
            for naam, h in controleer_kopie().items():
                print(f"ok: {naam} = {h.repo}@{h.commit[:12]}")
        else:
            print(f"bijgewerkt: {args.repo}@{bijwerken(args.bron, args.ref, repo=args.repo)}")
    except KopieFout as exc:
        print(f"FOUT: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
