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
- geen terminalopdrachten en geen voorbeeldwaarden (IP-adres, poolnaam) bevat, en in ``uitvoeren_nl``,
  ``bij_fout_nl`` en ``handmatig_ui_pad_nl`` nooit een YAML-, compose- of scriptweg noemt (titel en acceptatie zijn de
  letterlijke Excel-tekst en mogen zulke woorden dragen; besluit Z-18 optie 1);
- per stap een eerlijke validatie heeft (accounts → NIET GEVALIDEERD (account); hardware → … (hardware));
- hoort bij precies één bestaand document onder ``03_INSTALLATIE/``: de bestandsnaam is de documentnaam met
  ``.checklist.yaml`` in plaats van ``.md``, en geen document heeft twee lijsten;
- niet hoort bij een document dat al een installeerbaar manifest heeft in ``install/manifests/`` (``docs``-veld van
  een manifest met ``install.method`` ``custom_app`` of ``catalog_app``).

Elk ander bestand in ``install/checklists/`` (bijvoorbeeld ``.yml`` of zonder ``.checklist``) is een fout.

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
MANIFEST_DIR = ROOT / "install" / "manifests"
INSTALLEERBAAR = frozenset({"custom_app", "catalog_app"})
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
#: Terminalopdrachten horen niet in een controlelijst voor de gebruiker (contract §1: geen terminal): alles tussen
#: backticks, en een commando uit de lijst gevolgd door een vlag, een pad of een argument. Een gewoon Nederlands
#: lidwoord, voorzetsel of voegwoord na de commandonaam is geen argument: de letterlijke Excel-acceptatie
#: "Geen recursieve chmod/chown op bibliotheek" (I03-03-02) is tekst, "chown -R 568:568 /data" blijft een opdracht.
GEWOON_WOORD = r"(?i:op|of|en|in|met|van|voor|naar|bij|aan|niet|nooit|geen|is|zijn|wordt|de|het|een|zonder)(?=\s|$)"
TERMINAL = re.compile(
    rf"(`[^`]+`|\b(ls|stat|sudo|ssh|zfs|zpool|docker|chown|chmod|midclt|rm)\s+(?!{GEWOON_WOORD})-?[\w/])"
)
#: Nooit YAML, compose of een installatiescript als weg (contract §1/§2, regel R5): alleen TrueNAS-webinterface of het
#: formulier van de standaard Custom App. Geldt voor de velden die de weg beschrijven, niet voor titel en acceptatie.
YAML_WEG = re.compile(r"yaml|\byml\b|compose|\.sh\b", re.I)
WEG_VELDEN = ("uitvoeren_nl", "bij_fout_nl", "handmatig_ui_pad_nl")
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


def inhoud(map_: Path = CHECKLIST_DIR) -> list[Path]:
    """Alles in de map (bestanden en mappen), los van het naampatroon van een lijst."""
    return sorted(map_.iterdir()) if map_.is_dir() else []


def lijsten(map_: Path = CHECKLIST_DIR) -> list[Path]:
    return [p for p in inhoud(map_) if p.is_file() and p.name.endswith(".checklist.yaml")]


def documenten_met_manifest(map_: Path = MANIFEST_DIR) -> dict[str, list[str]]:
    """Documentnummer (``I13``) → installeerbare manifesten in ``map_`` die dat document in ``docs`` noemen."""
    uit: dict[str, list[str]] = {}
    for p in sorted(map_.glob("*.manifest.yaml")) if map_.is_dir() else []:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or (data.get("install") or {}).get("method") not in INSTALLEERBAAR:
            continue
        for doc in data.get("docs") or []:
            uit.setdefault(str(doc).split("_", 1)[0], []).append(p.name)
    return uit


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
        for veld in WEG_VELDEN:
            if YAML_WEG.search(str(stap.get(veld, ""))):
                fout.append(f"{sid}: YAML-, compose- of scriptweg in {veld} (alleen TrueNAS-webinterface of het "
                            "formulier van de standaard Custom App)")
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


def controleer_alle(map_: Path = CHECKLIST_DIR, manifesten: Path = MANIFEST_DIR) -> dict[str, list[str]]:
    """Elke lijst afzonderlijk (``controleer_lijst``) én de verzameling: één lijst per document, naam = document,
    geen lijst naast een installeerbaar manifest, en geen ander bestand in de map."""
    taken = excel_taken()
    met_manifest = documenten_met_manifest(manifesten)
    uitkomst: dict[str, list[str]] = {}
    per_document: dict[str, list[str]] = {}
    gevonden = lijsten(map_)
    for p in inhoud(map_):
        if p not in gevonden:
            uitkomst[p.name] = [f"geen controlelijst: in {map_.name}/ hoort alleen <DOCUMENT>.checklist.yaml"]
    for p in gevonden:
        data = yaml.safe_load(p.read_text(encoding="utf-8"))
        fout = controleer_lijst(data, taken)
        document = data.get("document") if isinstance(data, dict) else None
        if isinstance(document, str):
            per_document.setdefault(document, []).append(p.name)
            if p.name != bestandsnaam(document):
                fout.append(f"bestandsnaam past niet bij het document: verwacht {bestandsnaam(document)}")
            if namen := met_manifest.get(Path(document).name[:3]):
                fout.append(f"document {document} heeft een installeerbaar manifest ({', '.join(namen)}): een "
                            "controlelijst is alleen voor documenten zonder installatie")
        uitkomst[p.name] = fout
    for document, namen in per_document.items():
        if len(namen) > 1:
            for naam in namen:
                uitkomst[naam].append(f"document {document} heeft twee lijsten: {', '.join(namen)}")
    return uitkomst


def main(map_: Path = CHECKLIST_DIR, manifesten: Path = MANIFEST_DIR) -> int:
    uitkomst = controleer_alle(map_, manifesten)
    if not uitkomst:
        print(f"FOUT geen controlelijsten gevonden in {map_}")
    for naam, fout in uitkomst.items():
        print(f"{'OK  ' if not fout else 'FOUT'} {naam}")
        for f in fout:
            print(f"     - {f}")
    return 0 if uitkomst and all(not f for f in uitkomst.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
