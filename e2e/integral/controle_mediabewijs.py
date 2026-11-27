"""Vergelijk het media-inhoudsbewijs en de vijf bewijsrijen met een bewaard integratiedossier."""

import argparse
import json
from pathlib import Path
from typing import Any

SELECTIE = ["EX03", "EX04", "EX05", "EX09", "EX37"]
BEWIJS = "docs/platform-v2/bewijs/mediaguard-inhoud.json"


def lees(pad: Path) -> Any:
    return json.loads(pad.read_text())


def vergelijk(root: Path, run: Path) -> None:
    bewijs = lees(root / BEWIJS)
    rijen = lees(root / "docs/platform-v2/EVIDENCE.json")
    voorstellen = lees(run / "EVIDENCE-voorstellen.json")
    if [v["id"] for v in voorstellen] != SELECTIE or bewijs["selectie"] != SELECTIE or bewijs["run"] != run.name:
        raise ValueError("Afwijkende run of selectie voor het mediabewijs")
    images: dict[str, dict[str, str]] = {}
    for v in voorstellen:
        rid = v["id"]
        dossier = run / rid
        proef, toets = (lees(dossier / f"{naam}.json") for naam in ("proef", "pytest"))
        if (proef["id"] != rid or proef["broncommit"] != bewijs["broncommit"] or proef["uitkomst"] != v["validatie"]
                or (proef["exitcode"], proef["pytest_exitcode"], toets["exitcode"]) != (0, 0, 0)
                or not toets["getoetst"] or toets["overgeslagen"]):
            raise ValueError(f"{rid} heeft geen overeenkomstig positief testresultaat")
        bestand, = (root / "e2e/integral/scenarios").glob(f"{rid}_*.py")
        pad = bestand.relative_to(root).as_posix()
        verwacht = {"bestand": pad, "validatie": v["validatie"], "exitcode": 0, "diensten": sorted(proef["images"]),
                    "metingen": lees(dossier / "inhoud.json")}
        if bewijs["scenarios"].get(rid) != verwacht:
            raise ValueError(f"{rid} wijkt af in het vastgelegde scenariobewijs")
        voorstel = {k: v[k] for k in ("status", "bijgewerkt", "validatie")}
        voorstel["bewijs"] = f"{pad}, {BEWIJS} ({rid}, run {run.name})"
        selectie = [r for r in rijen if r["id"] == rid]
        if len(selectie) != 1 or any(selectie[0].get(k) != waarde for k, waarde in voorstel.items()):
            raise ValueError(f"{rid} wijkt af in EVIDENCE.json")
        for dienst, beeld in proef["images"].items():
            herkomst = {k: beeld[k] for k in ("broncommit", "image_id")}
            if images.setdefault(dienst, herkomst) != herkomst:
                raise ValueError(f"{rid} bevat een afwijkende image voor {dienst}")
    if set(bewijs["scenarios"]) != set(SELECTIE) or bewijs["images"] != images:
        raise ValueError("De vastgelegde scenario's of images wijken af van het rundossier")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run", type=Path)
    args = parser.parse_args()
    try:
        vergelijk(Path(__file__).resolve().parents[2], args.run)
    except (OSError, ValueError, KeyError) as fout:
        parser.exit(1, f"{fout}\n")
    print("Vijf bewijsrijen, vijf scenariometingen en alle gebruikte images komen overeen met de run.")
