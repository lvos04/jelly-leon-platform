"""Bouw lokale testimages of voer de geselecteerde proeven één voor één uit."""

from __future__ import annotations

import argparse
import ast
import json
import secrets
import signal
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # Ook rechtstreeks aanroepbaar vanuit de Makefile.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from e2e.integral.bewijs import PRAKTIJK, schrijf, voorstel
from e2e.integral.project import KERN, MAP, ROOT, BouwNodig, Proeffout, Project, broncommit, opdracht


@dataclass(frozen=True)
class Scenario:
    id: str
    pad: Path
    eigenaar: str
    verwachting: str
    groepen: tuple[str, ...]
    overslagredenen: tuple[str, ...] = ()
    diensten: tuple[str, ...] = ()
    soort: str = "Sim"
    blokkade: str = ""
    toelichting: str = ""


def catalogus(map_: Path) -> list[Scenario]:
    kandidaten: list[Scenario] = []
    for pad in sorted([*map_.glob("test_rook.py"), *map_.glob("EX[0-9][0-9]_*.py")]):
        # Metadata lezen zonder bij selectie testcode te starten.
        toekenning = next((n for n in ast.parse(pad.read_text()).body if isinstance(n, ast.Assign)
                           and any(isinstance(t, ast.Name) and t.id == "SCENARIO" for t in n.targets)), None)
        if toekenning is None:
            raise ValueError(f"SCENARIO ontbreekt in {pad.name}")
        rid = "rook" if pad.name == "test_rook.py" else pad.name[:4]
        if rid != "rook" and not 1 <= int(rid[2:]) <= 50:
            raise ValueError(f"Onbekend testmatrix-ID {rid}")
        scenario = Scenario(id=rid, pad=pad, **ast.literal_eval(toekenning.value))
        if any(s.id == rid for s in kandidaten):
            raise ValueError(f"Dubbel scenario {rid}")
        if scenario.soort not in {"Sim", "Sim+", "Echt"}:
            raise ValueError(f"Ongeldige uitvoerbaarheid voor {rid}")
        if scenario.soort != "Sim" and (scenario.blokkade not in PRAKTIJK or not scenario.toelichting.strip()):
            raise ValueError(f"Praktijkonderdeel ontbreekt voor {rid}")
        kandidaten.append(scenario)
    return kandidaten


def selectie(aanbod: list[Scenario], ids: str | None, groep: str | None) -> list[Scenario]:
    if ids is not None:
        gevraagd = list(dict.fromkeys(s.strip() for s in ids.split(",")))
        index = {s.id: s for s in aanbod}
        if any(s not in index for s in gevraagd):
            raise ValueError("Onbekend scenario-ID of lege selectie")
        return [index[s] for s in gevraagd]
    gekozen = [s for s in aanbod if groep is None or groep in s.groepen]
    if not gekozen:
        raise ValueError("Onbekende groep of lege selectie")
    return gekozen


def draai(project: Project, scenario: Scenario, map_: Path, commit: str,
          bewaar: bool) -> tuple[int, dict[str, str] | None]:
    datum = datetime.now(UTC).date().isoformat()
    map_.mkdir(mode=0o700)
    verslag: dict[str, Any] = {
        "id": scenario.id, "eigenaar": scenario.eigenaar, "datum": datum, "broncommit": commit,
        "project": project.naam, "verwachting": scenario.verwachting, "images": {}, "uitkomst": "onderbroken",
        "exitcode": 130, "overslagreden": None,
    }
    schrijf(map_ / "proef.json", {**verslag, "uitkomst": "gestart", "exitcode": None})
    gestart = False
    sim = None
    rij = None
    try:
        diensten = tuple(dict.fromkeys((*KERN, *scenario.diensten)))
        if set(diensten) - project.diensten.keys():
            raise Proeffout("Scenario vraagt een onbekende Compose-dienst", 2)
        if scenario.soort == "Echt":
            verslag.update(uitkomst="overgeslagen", overslagreden=scenario.toelichting, exitcode=0)
        else:
            images = project.images(diensten)
            verslag["images"] = images
            if "jelly-leon" in diensten and "jelly-leon" not in images:
                verslag.update(uitkomst="overgeslagen", overslagreden="jelly-leon:p6 ontbreekt (S-P6-30)", exitcode=0)
            else:
                gestart = True  # Ook een gedeeltelijk mislukte up kan een netwerk of volumes achterlaten.
                project.env["INTEGRAAL_IMAGE_IDS"] = json.dumps({d: i["image_id"] for d, i in images.items()})
                project.start(tuple(d for d in diensten if d != "lifecycle"))
                env = {**project.env, "INTEGRAAL_PROJECT": project.naam, "INTEGRAAL_DOSSIER": str(map_),
                       "INTEGRAAL_OVERSLAGREDENEN": json.dumps(scenario.overslagredenen)}
                res = opdracht([sys.executable, "-m", "pytest", "-q", "--tb=no", "-p", "no:cacheprovider",
                                str(scenario.pad)], env=env, tijd=900)
                # Pytest-uitvoer kan headers, URL's, locals en koppelcodes bevatten. Bewaar alleen meetgegevens.
                resultaat = json.loads((map_ / "pytest.json").read_text()) if (map_ / "pytest.json").is_file() else {}
                verslag["pytest_exitcode"] = res.returncode
                if res.returncode in {0, 5} and resultaat.get("overgeslagen"):
                    verslag.update(uitkomst="overgeslagen", overslagreden=resultaat["reden"], exitcode=0)
                elif res.returncode == 0 and resultaat.get("getoetst"):
                    sim = "Sim-geslaagd"
                    verslag.update(uitkomst=sim, exitcode=0)
                else:
                    code = res.returncode or 1
                    if resultaat.get("getoetst") and code == 1:
                        sim = "Sim-mislukt"
                    verslag.update(uitkomst=sim or "proeffout", exitcode=code)
        if scenario.id != "rook" and (sim or scenario.soort != "Sim"):
            rij = voorstel(scenario, sim, datum, str((map_ / "proef.json").relative_to(ROOT)))
    except Proeffout as fout:
        verslag.update(uitkomst="proeffout", melding=str(fout), exitcode=fout.code)
        if isinstance(fout, BouwNodig):
            raise
    except ValueError:
        verslag.update(uitkomst="proeffout", melding="Ongeldig meetresultaat of EVIDENCE-voorstel", exitcode=2)
    finally:
        if gestart and not bewaar:
            try:
                project.ruim()
            except Proeffout as fout:
                rij = None
                verslag.update(uitkomst="opruimfout", simuitkomst=sim, melding=str(fout), exitcode=fout.code)
        schrijf(map_ / "proef.json", verslag)
        print(f"{scenario.id}: {verslag['uitkomst']} ({verslag['exitcode']}) "
              f"{verslag.get('melding') or verslag['overslagreden'] or ''}", flush=True)
    return int(verslag["exitcode"]), rij


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    keuze = parser.add_mutually_exclusive_group(required=True)
    keuze.add_argument("--bouw", action="store_true")
    keuze.add_argument("--scenario")
    keuze.add_argument("--groep")
    keuze.add_argument("--alles", action="store_true")
    parser.add_argument("--profiel", help="beperk de bouw tot één profiel")
    parser.add_argument("--bewaar", action="store_true", help="laat het laatste geselecteerde project staan")
    args = parser.parse_args(argv)
    if (args.profiel and not args.bouw) or (args.bouw and args.bewaar):
        parser.error("--profiel hoort bij --bouw en --bewaar bij een proef")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ").lower() + "-" + secrets.token_hex(5)
    try:
        if args.bouw:
            Project("jlp-bouw-" + run_id).bouw(args.profiel)
            return 0
        gekozen = selectie(catalogus(MAP / "scenarios"), args.scenario, args.groep)
        runmap = MAP / "evidence" / run_id
        runmap.mkdir(parents=True, mode=0o700)
        commit = broncommit(ROOT)
        print(f"Runmap: {runmap.relative_to(ROOT)}", flush=True)
        voorstellen: list[dict[str, str]] = []
        schrijf(runmap / "EVIDENCE-voorstellen.json", voorstellen)
        code = 0
        for nummer, scenario in enumerate(gekozen, 1):
            # Kort genoeg voor de vrije-tekstredactie; een eigen naam per proef, ook bij --bewaar.
            project = Project("jlp-it-" + secrets.token_hex(10))
            status, rij = draai(project, scenario, runmap / scenario.id, commit, args.bewaar and nummer == len(gekozen))
            if rij:
                voorstellen.append(rij)
            schrijf(runmap / "EVIDENCE-voorstellen.json", voorstellen)
            code = code or status
        return code
    except BouwNodig as fout:
        if args.bouw:
            print(fout, file=sys.stderr)
        return fout.code  # Bij draaien bevat de proefregel de bouwinstructie.
    except (ValueError, Proeffout) as fout:
        print(fout, file=sys.stderr)
        return fout.code if isinstance(fout, Proeffout) else 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    def onderbreek(_nummer: int, _frame: object) -> None:
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, onderbreek)
    sys.exit(main())
