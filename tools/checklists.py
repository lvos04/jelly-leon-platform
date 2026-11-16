"""Controlelijsten (eigen formaat ``checklist v1``) voor I-documenten zonder installatie (ADR-0015, ADR-0027).

I01 (TrueNAS-basis) en I02 (back-up en herstel) zijn de eerste; de documenten van de bestaande apps (I03, I04, I05 en
I15 tot en met I20) krijgen een lijst in hetzelfde formaat. Niets hier geldt alleen voor bepaalde namen of aantallen:
elke lijst in ``install/checklists/`` wordt op dezelfde regels gecontroleerd.

De manifeststandaard (contract 1.2 §9) kent alleen ``custom_app`` en ``catalog_app``. Een controlelijst installeert
niets (contract 1.3 §9: ``kind: checklist``, ``install.method: none``) en:

- het JSON Schema ``schemas/checklist-v1.schema.json`` volgt;
- precies de Excel-taken van het document dekt, in volgorde, met dezelfde titel en hetzelfde acceptatiecriterium;
- alleen **alleen-lezen** TrueNAS-methoden noemt (het platform wijzigt hier niets; wijzigingen doet de gebruiker in de
  TrueNAS-web-UI of ze volgen later als goedgekeurde R1/R2-actie via de executor);
- geen terminalopdrachten en geen voorbeeldwaarden (IP-adres, poolnaam) bevat;
- per stap een eerlijke validatie heeft (accounts → NIET GEVALIDEERD (account); hardware → … (hardware));
- hoort bij precies één bestaand document onder ``03_INSTALLATIE/``: de bestandsnaam is de documentnaam met
  ``.checklist.yaml`` in plaats van ``.md``, en geen document heeft twee lijsten.

Gebruik: ``python tools/checklists.py`` (exitcode 0 = alles in orde).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import jsonschema
import yaml

ROOT = Path(__file__).resolve().parents[1]
CHECKLIST_DIR = ROOT / "install" / "checklists"
SCHEMA = ROOT / "schemas" / "checklist-v1.schema.json"
DOCS = ROOT / "docs" / "platform-v2"
EXCEL = DOCS / "EXCEL_TAKEN.json"

#: Alleen-lezen-methoden: het laatste segment moet hierin staan (en nooit in SCHRIJVEND).
LEZEND = frozenset({
    "query", "info", "version", "version_short", "config", "list", "used_ports", "gpu_choices", "temperatures",
    "results", "get_app_details", "trains", "available", "me", "get_methods", "rollback_versions",
})
SCHRIJVEND = re.compile(
    r"\.(create|update|delete|start|stop|redeploy|rollback|upgrade|clone|sync|setperm|set_|do_|run|restart|"
    r"login_with_api_key|login_ex|job_abort|subscribe)\b"
)
#: Terminalopdrachten horen niet in een controlelijst voor de gebruiker (contract §1: geen terminal).
TERMINAL = re.compile(r"(`[^`]+`|\b(ls|stat|sudo|ssh|zfs|zpool|docker|chown|chmod|midclt|rm)\s+-?[\w/])")
VOORBEELD = re.compile(r"(\b\d{1,3}(\.\d{1,3}){3}\b|/mnt/(tank|pool|data)\b|\btank\b)")
ACCOUNT_STAP = re.compile(r"OneDrive|account", re.I)
ECHT_NODIG = frozenset({"NIET GEVALIDEERD (echte NAS)", "NIET GEVALIDEERD (hardware)", "NIET GEVALIDEERD (account)"})


def excel_taken() -> dict[str, list[dict[str, Any]]]:
    taken: dict[str, list[dict[str, Any]]] = {}
    for t in json.loads(EXCEL.read_text(encoding="utf-8")):
        doc = str(t.get("Document", ""))
        if doc.startswith("03_INSTALLATIE/"):
            taken.setdefault(doc, []).append(t)
    return taken


def lijsten(map_: Path = CHECKLIST_DIR) -> list[Path]:
    return sorted(map_.glob("*.checklist.yaml"))


def controleer_lijst(data: dict[str, Any], taken: dict[str, list[dict[str, Any]]]) -> list[str]:
    fout: list[str] = []
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    for err in jsonschema.Draft202012Validator(schema).iter_errors(data):
        fout.append(f"schema: {'/'.join(map(str, err.path)) or '(root)'}: {err.message}")
    if fout:
        return fout
    if not (DOCS / data["document"]).is_file():
        fout.append(f"document bestaat niet onder docs/platform-v2/: {data['document']}")
    doc_taken = taken.get(data["document"], [])
    if not doc_taken:
        return [*fout, f"geen Excel-taken voor {data['document']}"]
    if not str(Path(data["document"]).name).startswith(data["id"] + "_"):
        fout.append("id past niet bij het document")
    stappen = data["stappen"]
    if [s["id"] for s in stappen] != [t["ID"] for t in doc_taken]:
        fout.append(f"stappen dekken de Excel-taken niet exact: {[t['ID'] for t in doc_taken]}")
    for stap, taak in zip(stappen, doc_taken, strict=False):
        sid = stap["id"]
        if stap["titel_nl"] != taak["Taak"]:
            fout.append(f"{sid}: titel ≠ Excel-taak ({taak['Taak']!r})")
        if stap["acceptatie_nl"] != taak["Acceptatie"]:
            fout.append(f"{sid}: acceptatie ≠ Excel ({taak['Acceptatie']!r})")
        if stap["risico"] != taak["Risico"]:
            fout.append(f"{sid}: risico ≠ Excel ({taak['Risico']!r})")
    for stap in stappen:
        sid = stap["id"]
        methoden = stap.get("truenas_methoden", [])
        if "meting_truenas_api" in stap["wijze"] and not methoden:
            fout.append(f"{sid}: meting zonder TrueNAS-methode")
        if methoden and "meting_truenas_api" not in stap["wijze"]:
            fout.append(f"{sid}: TrueNAS-methoden zonder wijze meting_truenas_api")
        if "handmatig_in_truenas_ui" in stap["wijze"] and not stap.get("handmatig_ui_pad_nl"):
            fout.append(f"{sid}: handmatige UI-stap zonder menupad")
        if "verwijzing" in stap["wijze"] and not stap.get("verwijzing"):
            fout.append(f"{sid}: verwijzing zonder doel")
        for m in methoden:
            naam = m["naam"]
            if SCHRIJVEND.search(naam) or naam.rsplit(".", 1)[-1] not in LEZEND:
                fout.append(f"{sid}: {naam} is geen alleen-lezen-methode")
        if not set(stap["besluiten"]) <= set(data["besluiten"]):
            fout.append(f"{sid}: besluit niet in de lijst van de controlelijst")
        for veld in ("uitvoeren_nl", "acceptatie_nl", "bij_fout_nl", "handmatig_ui_pad_nl"):
            tekst = str(stap.get(veld, ""))
            if TERMINAL.search(tekst):
                fout.append(f"{sid}: terminalopdracht in {veld}")
            if VOORBEELD.search(tekst):
                fout.append(f"{sid}: voorbeeldwaarde in {veld}")
        validatie = set(stap["validatie_nu"])
        if "Sim-getest" in validatie and not methoden:
            fout.append(f"{sid}: Sim-getest zonder iets dat tegen de simulator draait")
        if not validatie & ECHT_NODIG:
            fout.append(f"{sid}: elke stap vraagt een echte NAS, hardware of account (nooit alleen Sim-getest)")
        if ACCOUNT_STAP.search(stap["uitvoeren_nl"]) and "handmatig_bewijs" in stap["wijze"] and (
            "NIET GEVALIDEERD (account)" not in validatie
        ):
            fout.append(f"{sid}: OneDrive/account-stap zonder NIET GEVALIDEERD (account)")
    return fout


def bestandsnaam(document: str) -> str:
    """De enige toegestane bestandsnaam van de lijst bij ``document`` (``03_INSTALLATIE/I03_X.md`` → ``I03_X…``)."""
    return Path(document).stem + ".checklist.yaml"


def controleer_alle(map_: Path = CHECKLIST_DIR) -> dict[str, list[str]]:
    """Elke lijst afzonderlijk (``controleer_lijst``) én de verzameling: één lijst per document, naam = document."""
    taken = excel_taken()
    uitkomst: dict[str, list[str]] = {}
    per_document: dict[str, list[str]] = {}
    for p in lijsten(map_):
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        fout = controleer_lijst(data, taken)
        document = data.get("document") if isinstance(data, dict) else None
        if isinstance(document, str):
            per_document.setdefault(document, []).append(p.name)
            if p.name != bestandsnaam(document):
                fout.append(f"bestandsnaam past niet bij het document: verwacht {bestandsnaam(document)}")
        uitkomst[p.name] = fout
    for document, namen in per_document.items():
        if len(namen) > 1:
            for naam in namen:
                uitkomst[naam].append(f"document {document} heeft twee lijsten: {', '.join(namen)}")
    return uitkomst


def main(map_: Path = CHECKLIST_DIR) -> int:
    uitkomst = controleer_alle(map_)
    if not uitkomst:
        print(f"FOUT geen controlelijsten gevonden in {map_}")
    for naam, fout in uitkomst.items():
        print(f"{'OK  ' if not fout else 'FOUT'} {naam}")
        for f in fout:
            print(f"     - {f}")
    return 0 if uitkomst and all(not f for f in uitkomst.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
