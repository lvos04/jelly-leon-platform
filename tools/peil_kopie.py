"""Versie-gepinde kopie van het Peil-statuspaginasjabloon (ONTWERPTAAL §13.2/§14, O-14).

Bron van waarheid: ``lvos04/ai-server-manager`` → ``design/ontwerptaal/statuspagina.html`` + ``statuspagina.css``.
Deze repo houdt in ``design/ontwerptaal/`` een kopie met een **herkomstkop van 2 regels** (versie, bronbestand met
commit, sha256 van het bronbestand); vanaf regel 3 is de kopie byte-gelijk aan de bron. De dienst serveert
``apps/api/src/lifecycle/peil/statuspagina.html``: byte-gelijk aan de kopie in ``design/ontwerptaal/`` (zelfde kop).

Gebruik (alleen ontwikkelaars; de bron wordt alleen gelezen)::

    python tools/peil_kopie.py controleer                        # kopie intact, runtime-kopie gelijk
    python tools/peil_kopie.py bijwerken --bron ../ai-server-manager [--ref <commit|branch>]

``bijwerken`` leest de bestanden met ``git show <ref>:<pad>`` (dus alleen gecommitte inhoud) en schrijft de kopieën
opnieuw. Daarna: tests draaien, schermafdrukken opnieuw maken, CHANGELOG en ``design/ontwerptaal/HERKOMST.md``
bijwerken.
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
KOPIE_DIR = ROOT / "design" / "ontwerptaal"
RUNTIME_HTML = ROOT / "apps" / "api" / "src" / "lifecycle" / "peil" / "statuspagina.html"
BRON_REPO = "lvos04/ai-server-manager"
BRON_DIR = "design/ontwerptaal"
BESTANDEN = ("statuspagina.html", "statuspagina.css")

KOP_RE = re.compile(
    r"HERKOMST — Peil (?P<versie>\d+\.\d+\.\d+) · bron: (?P<repo>[\w.-]+/[\w.-]+)@(?P<commit>[0-9a-f]{40})"
    r":(?P<pad>\S+) · sha256 van het bronbestand \(zonder deze kop\): (?P<sha>[0-9a-f]{64})"
)
# Versie zoals de bron die zelf noemt (eerste regels van het sjabloon en de CSS).
BRON_VERSIE_RE = {
    "statuspagina.html": re.compile(r"<!-- Peil statuspagina-sjabloon (\d+\.\d+\.\d+) · bron ai-server-manager"),
    "statuspagina.css": re.compile(r"/\*! Peil statuspagina\.css (\d+\.\d+\.\d+) · bron ai-server-manager"),
}
REGEL2 = ("Versie-gepinde kopie (O-14). Niet met de hand wijzigen: nieuwe versie = tools/peil_kopie.py bijwerken; "
          "CI faalt bij drift (tests/repo/test_peil_kopie.py).")


class KopieFout(Exception):
    """De kopie is niet intact of wijkt af van de bron."""


@dataclass(frozen=True)
class Herkomst:
    versie: str
    repo: str
    commit: str
    pad: str
    sha256: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def maak_kop(naam: str, versie: str, commit: str, bron: bytes) -> bytes:
    eerste = (f"HERKOMST — Peil {versie} · bron: {BRON_REPO}@{commit}:{BRON_DIR}/{naam} · sha256 van het "
              f"bronbestand (zonder deze kop): {sha256(bron)}")
    if naam.endswith(".html"):
        return f"<!-- {eerste}\n     {REGEL2} -->\n".encode()
    return f"/* {eerste}\n   {REGEL2} */\n".encode()


def splits(naam: str, data: bytes) -> tuple[Herkomst, bytes]:
    """Herkomstkop (2 regels) en de rest (byte-gelijk aan de bron). Gooit ``KopieFout``."""
    delen = data.split(b"\n", 2)
    if len(delen) != 3:
        raise KopieFout(f"{naam}: geen herkomstkop van 2 regels")
    kop = (delen[0] + b"\n" + delen[1]).decode("utf-8")
    m = KOP_RE.search(delen[0].decode("utf-8"))
    sluit = "-->" if naam.endswith(".html") else "*/"
    if not m or not kop.rstrip().endswith(sluit):
        raise KopieFout(f"{naam}: herkomstkop ontbreekt of is ongeldig")
    if m["pad"] != f"{BRON_DIR}/{naam}" or m["repo"] != BRON_REPO:
        raise KopieFout(f"{naam}: herkomstkop verwijst naar een ander bronbestand ({m['repo']}:{m['pad']})")
    return Herkomst(m["versie"], m["repo"], m["commit"], m["pad"], m["sha"]), delen[2]


def bron_versie(naam: str, bron: bytes) -> str | None:
    m = BRON_VERSIE_RE[naam].search(bron.decode("utf-8"))
    return m.group(1) if m else None


def controleer_kopie(kopie_dir: Path = KOPIE_DIR, runtime_html: Path = RUNTIME_HTML) -> dict[str, Herkomst]:
    """Kopie intact (sha256 van de rest = kop), zelfde versie/commit voor beide bestanden, de CSS in het sjabloon is
    gelijk aan ``statuspagina.css`` en de runtime-kopie van de dienst is byte-gelijk aan de kopie."""
    herkomst: dict[str, Herkomst] = {}
    rest: dict[str, bytes] = {}
    for naam in BESTANDEN:
        h, r = splits(naam, (kopie_dir / naam).read_bytes())
        if sha256(r) != h.sha256:
            raise KopieFout(f"{naam}: inhoud wijkt af van de gepinde sha256 (met de hand gewijzigd?)")
        if bron_versie(naam, r) != h.versie:
            raise KopieFout(f"{naam}: versie in de kop ({h.versie}) ≠ versie in het bestand ({bron_versie(naam, r)})")
        herkomst[naam], rest[naam] = h, r
    html, css = herkomst["statuspagina.html"], herkomst["statuspagina.css"]
    if (html.versie, html.commit) != (css.versie, css.commit):
        raise KopieFout("statuspagina.html en statuspagina.css komen uit verschillende versies of commits")
    if b"<style>" + rest["statuspagina.css"].strip() + b"</style>" not in rest["statuspagina.html"]:
        raise KopieFout("de CSS in statuspagina.html is niet gelijk aan statuspagina.css")
    if runtime_html.read_bytes() != (kopie_dir / "statuspagina.html").read_bytes():
        raise KopieFout("de runtime-kopie van de dienst is niet byte-gelijk aan design/ontwerptaal/statuspagina.html")
    return herkomst


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
    versies = {bron_versie(naam, data) for naam, data in inhoud.items()}
    if len(versies) != 1 or None in versies:
        raise KopieFout(f"versie in de bron niet eenduidig: {versies}")
    versie = str(versies.pop())
    for naam, data in inhoud.items():
        (KOPIE_DIR / naam).write_bytes(maak_kop(naam, versie, commit, data) + data)
    RUNTIME_HTML.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_HTML.write_bytes((KOPIE_DIR / "statuspagina.html").read_bytes())
    controleer_kopie()
    return f"Peil {versie} @ {commit}"


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
            h = controleer_kopie()["statuspagina.html"]
            print(f"kopie intact: Peil {h.versie} @ {h.commit}")
        else:
            print(f"bijgewerkt naar {bijwerken(args.bron, args.ref)}")
    except KopieFout as exc:
        print(f"FOUT: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
