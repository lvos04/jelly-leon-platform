"""Controlelijsten (eigen formaat checklist v1): I01/I02 (ADR-0015) en de bestaande apps I03…I20 (ADR-0027).

Alle tests gelden voor elke lijst in ``install/checklists/`` (``cl.lijsten()``), niet voor vaste bestandsnamen.

- Vorm (JSON Schema), dekking van precies de Excel-taken met titel/acceptatie/risico uit de Excel, alleen-lezen
  TrueNAS-methoden, geen terminal, geen voorbeeldwaarden, eerlijke validatie per stap (``tools/checklists.py``).
- Verzameling: elke lijst hoort bij precies één document onder ``03_INSTALLATIE/`` (bestandsnaam = documentnaam),
  geen document heeft twee lijsten, een document met een installeerbaar manifest van deze repo in
  ``install/manifests/`` heeft geen lijst (een manifestkopie van een andere repo mag ernaast; de lijst verwijst
  ernaar), en elk ander bestand in ``install/checklists/`` is een fout (ook ``python tools/checklists.py`` faalt dan).
  De test leest de map zelf, los van ``cl.lijsten()``.
- Tekstregels: de letterlijke Excel-titels en -acceptaties van alle documenten gaan door de terminal- en
  voorbeeldregel; een YAML-, compose- of scriptweg wordt geweigerd in ``uitvoeren_nl``, ``bij_fout_nl`` en
  ``handmatig_ui_pad_nl`` (titel, acceptatie en afwijking mogen de Excel-woorden dragen; Z-18 optie 2 hernoemde de
  I05-titels).
- Negatieve tests: elke overtreding wordt herkend, in elke lijst; de account-regel voor elke account-stap van elke
  lijst.
- Simulator: elke genoemde methode bestaat in het TrueNAS-profiel waarvoor ze geldt en is aan te roepen met de
  alleen-lezen-key van de Hub (25.04.2.6 en 25.10.8) — en een methode die in een profiel niet bestaat, wordt daar ook
  echt geweigerd (``-32601``), zodat de versie-indeling klopt.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import sys
from collections import Counter
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import checklists as cl  # noqa: E402


def _load(naam: str) -> dict[str, Any]:
    data = yaml.safe_load((cl.CHECKLIST_DIR / naam).read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_checklists_are_valid_and_cover_the_excel_tasks() -> None:
    uitkomst = cl.controleer_alle()
    # ondergrens (V-003): nooit groen op nul lijsten; I01 en I02 blijven er altijd
    assert {"I01_TRUENAS_BASIS.checklist.yaml", "I02_BACKUP_EN_RESTORE.checklist.yaml"} <= set(uitkomst)
    # de map zelf lezen (niet via cl.lijsten()): elk bestand in install/checklists/ is gecontroleerd en groen
    assert set(uitkomst) == {p.name for p in cl.CHECKLIST_DIR.iterdir()}
    assert uitkomst == {naam: [] for naam in uitkomst}
    taken = cl.excel_taken()
    documenten: Counter[str] = Counter()
    for naam in uitkomst:
        data = _load(naam)
        document = data["document"]
        documenten[document] += 1
        # hoort bij precies één document onder 03_INSTALLATIE/, dat bestaat en Excel-taken heeft
        assert document.startswith("03_INSTALLATIE/") and (cl.DOCS / document).is_file(), (naam, document)
        assert naam == Path(document).stem + ".checklist.yaml", (naam, document)
        # aantal stappen = aantal Excel-taken van dat document (geen vast getal)
        assert len(data["stappen"]) == len(taken[document]) > 0, naam
        assert [s["id"] for s in data["stappen"]] == [t["ID"] for t in taken[document]], naam
        assert data["installatie"] == "geen" and data["mutaties_door_platform"] is False
    # geen document heeft twee lijsten
    assert all(n == 1 for n in documenten.values()), documenten


def _kopie(map_: Path, naam: str, doel: str | None = None, wijzig: Any = None) -> Path:
    """Proefkopie van een bestaande lijst in een eigen map (nooit in install/checklists/, V-022)."""
    data = copy.deepcopy(_load(naam))
    if wijzig is not None:
        wijzig(data)
    pad = map_ / (doel or naam)
    pad.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return pad


LIJSTEN = [p.name for p in cl.lijsten()]


def test_evidence_dekt_de_praktijkproeven_van_de_checklists() -> None:
    bewijs = {r["id"]: r for r in json.loads((cl.DOCS / "EVIDENCE.json").read_text(encoding="utf-8"))}
    for naam in LIJSTEN:
        for stap in _load(naam)["stappen"]:
            verwacht = {v for v in stap["validatie_nu"] if v.startswith("NIET GEVALIDEERD (")}
            if {"meting_truenas_api", "handmatig_in_truenas_ui"}.intersection(stap["wijze"]):
                verwacht.add("NIET GEVALIDEERD (echte NAS)")
            assert verwacht <= set(bewijs[stap["id"]]["validatie"].split("; ")), stap["id"]


def test_evidence_uitvalmelding_vermeldt_nas_en_telefoon() -> None:
    rijen = json.loads((cl.DOCS / "EVIDENCE.json").read_text(encoding="utf-8"))
    melding = next(r for r in rijen if r["id"] == "I08-08-04")
    verwacht = {"NIET GEVALIDEERD (echte NAS)", "NIET GEVALIDEERD (hardware)"}
    assert verwacht <= set(melding["validatie"].split("; "))


def test_copies_of_the_real_lists_pass_in_a_separate_folder(tmp_path: Path) -> None:
    """Controle op de proefmap zelf: zonder overtreding is alles groen (anders bewijzen de tests hieronder niets)."""
    for naam in LIJSTEN:
        _kopie(tmp_path, naam)
    assert cl.controleer_alle(tmp_path) == {naam: [] for naam in LIJSTEN}
    assert cl.main(tmp_path) == 0


def test_i03_is_delivered_with_the_literal_excel_tasks() -> None:
    data = _load("I03_MEDIAGUARD.checklist.yaml")
    assert data["aanpak"] == "behouden_en_valideren"
    taken = cl.excel_taken()["03_INSTALLATIE/I03_MEDIAGUARD.md"]
    assert len(data["stappen"]) == len(taken) == 8
    assert cl.controleer_lijst(data, cl.excel_taken()) == []


def test_i06_koppelt_de_jellyfin_proeven_aan_d07_en_het_herstelbewijs() -> None:
    data = _load("I06_JELLYFIN.checklist.yaml")
    stappen = data["stappen"]
    assert [s["id"] for s in stappen] == [f"I06-06-{n:02d}" for n in range(1, 9)]
    assert data["aanpak"] == "behouden_en_valideren"
    assert "Emby blijft de mediaserver" in data["toelichting_nl"]
    for stap in stappen:
        assert stap["blokkeert_volgende"]
        assert "D07" in stap["besluiten"] and "D07" in stap["uitvoeren_nl"]
        assert "NIET GEVALIDEERD (echte NAS)" in stap["validatie_nu"]
    inventaris, backup = stappen[:2]
    assert "app.query" in {m["naam"] for m in inventaris["truenas_methoden"]}
    assert {"D04", "D05"} <= set(backup["besluiten"])
    assert "install/checklists/I02_BACKUP_EN_RESTORE.checklist.yaml" in backup["verwijzing"]
    assert "geïsoleerde proefinstallatie" in backup["uitvoeren_nl"]
    herstel = stappen[-1]
    assert "vorige image en de bijpassende snapshot samen" in herstel["uitvoeren_nl"]
    assert "vóór en na per gebruiker" in herstel["uitvoeren_nl"]
    assert "voortgang in MediaGuard" in herstel["uitvoeren_nl"]


def test_i11_bewaakt_de_passieve_basis_en_het_herstel() -> None:
    data = _load("I11_NETALERTX.checklist.yaml")
    stappen = data["stappen"]
    assert [s["id"] for s in stappen] == [f"I11-11-{nr:02d}" for nr in range(1, 7)]
    for stap in stappen:
        assert stap["blokkeert_volgende"] is True
        assert "NIET GEVALIDEERD (echte NAS)" in stap["validatie_nu"]
    scope, installatie, _, _, connector, backup = stappen
    assert {"D03", "D11"} <= set(scope["besluiten"])
    assert "Actief scannen staat standaard uit" in scope["uitvoeren_nl"]
    assert "Stop hier zolang een toegelaten manifest ontbreekt" in installatie["uitvoeren_nl"]
    assert "alleen-lezen-koppeling" in connector["uitvoeren_nl"]
    assert "install/checklists/I02_BACKUP_EN_RESTORE.checklist.yaml" in backup["verwijzing"]
    assert "versleutelde back-up" in backup["uitvoeren_nl"]
    assert "bijpassende snapshot op de proefkopie" in backup["bij_fout_nl"]


def test_i11_zonder_terugdraaistap_wordt_geweigerd(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def verwijder_terugweg(data: dict[str, Any]) -> None:
        del data["stappen"][-1]["bij_fout_nl"]

    _kopie(tmp_path, "I11_NETALERTX.checklist.yaml", wijzig=verwijder_terugweg)
    assert cl.main(tmp_path) == 1
    melding = capsys.readouterr().out
    assert "stappen/5" in melding and "'bij_fout_nl' is a required property" in melding


def test_i24_verwijst_naar_de_custom_app_en_beschermt_de_terugval() -> None:
    data = _load("I24_UNIVERSELE_MEDIASERVER.checklist.yaml")
    stappen = data["stappen"]
    assert [s["id"] for s in stappen] == [f"I24-24-{n:02d}" for n in range(1, 7)]
    assert data["install"] == {"method": "none"} and data["mutaties_door_platform"] is False
    for stap in stappen:
        assert stap["blokkeert_volgende"]
        assert "NIET GEVALIDEERD (echte NAS)" in stap["validatie_nu"]
    for stap in (stappen[1], stappen[4]):
        assert "NIET GEVALIDEERD (hardware)" in stap["validatie_nu"]
    staging = stappen[3]
    assert "verwijzing" in staging["wijze"]
    assert "jelly-leon: deploy/truenas/CUSTOM_APP.md" in staging["verwijzing"]
    assert {"D04", "D05", "D07"} <= set(staging["besluiten"])
    for tekst in ("nieuwe app naast Emby", "vrije poort", "eigen datasets", "media alleen-lezen"):
        assert tekst in staging["uitvoeren_nl"]
    terugval = stappen[-1]["uitvoeren_nl"]
    for tekst in ("vorige image en de bijpassende snapshot samen", "vóór en na per gebruiker",
                  "MediaGuard-voortgang", "uitdrukkelijk akkoord op elk verschil"):
        assert tekst in terugval


@pytest.mark.parametrize("naam", ["I06_JELLYFIN.checklist.yaml", "I24_UNIVERSELE_MEDIASERVER.checklist.yaml"])
@pytest.mark.parametrize(("veld", "waarde", "melding"), [
    ("uitvoeren_nl", "Open een terminal.", "terminalopdracht in uitvoeren_nl"),
    ("bij_fout_nl", "Lees de status met midclt call app.query.", "terminalopdracht in bij_fout_nl"),
    ("validatie_nu", None, "'validatie_nu' is a required property"),
    ("validatie_nu", ["Gereed"], "validatie_nu/0"),
], ids=["terminal", "shellcommando", "zonder-validatie", "ongeldige-validatie"])
def test_mediachecklist_weigert_ongeldige_stap(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], naam: str, veld: str, waarde: str | list[str] | None,
    melding: str,
) -> None:
    def wijzig(data: dict[str, Any]) -> None:
        stap = data["stappen"][0]
        if waarde is None:
            del stap[veld]
        else:
            stap[veld] = waarde

    _kopie(tmp_path, naam, wijzig=wijzig)
    assert cl.main(tmp_path) == 1
    assert melding in capsys.readouterr().out


def _i03_veiligheid(data: dict[str, Any]) -> None:
    """Bewaar de inhoudelijke gates van I03; de algemene validator bewaakt vorm en Excel-teksten."""
    backup, update = data["stappen"][2:4]
    assert backup["blokkeert_volgende"] is True
    for tekst in ("encoder", "vier forks", "versleuteld", "proefherstel", "I03-03-04", "Back-up nu",
                  "Versleutelde herstelbundel", "Herstelcode instellen", "Nu bundel maken", "apart"):
        assert tekst in backup["uitvoeren_nl"], tekst
    for tekst in ("I03-03-03", "upgradebewijs van die release", "gevulde database", "vorige image-versie",
                  "passende snapshot", "Custom App", "gebruiker", "Emby-koppeling", "niet bijwerken"):
        assert tekst in update["uitvoeren_nl"], tekst
    assert update["blokkeert_volgende"] is True
    assert "vorige image" in update["bij_fout_nl"] and "snapshot" in update["bij_fout_nl"]
    assert "voortgang" in update["bij_fout_nl"]
    assert "handmatig_in_truenas_ui" in update["wijze"]
    assert update["handmatig_ui_pad_nl"]


def test_i03_backup_precedes_update_and_rollback_preserves_progress() -> None:
    _i03_veiligheid(_load("I03_MEDIAGUARD.checklist.yaml"))


@pytest.mark.parametrize(("index", "veld", "tekst"), [
    (2, "blokkeert_volgende", None),
    (2, "uitvoeren_nl", "vier forks"),
    (2, "uitvoeren_nl", "Back-up nu"),
    (2, "uitvoeren_nl", "Versleutelde herstelbundel"),
    (2, "uitvoeren_nl", "Herstelcode instellen"),
    (2, "uitvoeren_nl", "proefherstel"),
    (3, "uitvoeren_nl", "upgradebewijs van die release"),
    (3, "uitvoeren_nl", "passende snapshot"),
    (3, "uitvoeren_nl", "Emby-koppeling"),
    (3, "bij_fout_nl", "voortgang"),
])
def test_i03_missing_safety_condition_is_detected(index: int, veld: str, tekst: str | None) -> None:
    data = copy.deepcopy(_load("I03_MEDIAGUARD.checklist.yaml"))
    stap = data["stappen"][index]
    if tekst is None:
        stap[veld] = False
    else:
        assert tekst in stap[veld]
        stap[veld] = stap[veld].replace(tekst, "ontbreekt")
    with pytest.raises(AssertionError):
        _i03_veiligheid(data)


def test_i03_manual_checks_do_not_claim_live_or_simulated_acceptance() -> None:
    data = _load("I03_MEDIAGUARD.checklist.yaml")
    for stap in data["stappen"]:
        assert stap["wijze"] and not stap.get("truenas_methoden")
        assert "Sim-getest" not in stap["validatie_nu"]
        assert ("NIET GEVALIDEERD (echte Hub)" in stap["validatie_nu"]) == (stap["id"] == "I03-03-08")
    for index in (1, 5, 6):
        assert "NIET GEVALIDEERD (hardware)" in data["stappen"][index]["validatie_nu"]
    hub = data["stappen"][7]
    assert hub["wijze"] == ["handmatig_bewijs"]
    for tekst in ("viewer-sleutel", "Hub-UI", "S-P3-070", "nog niet", "dezelfde status", "alleen lezen"):
        assert tekst in hub["uitvoeren_nl"], tekst


def test_i03_one_character_change_in_acceptance_is_rejected(tmp_path: Path) -> None:
    def wijzig(data: dict[str, Any]) -> None:
        stap = data["stappen"][0]
        origineel = stap["acceptatie_nl"]
        stap["acceptatie_nl"] = "X" + origineel[1:]
        assert sum(a != b for a, b in zip(origineel, stap["acceptatie_nl"], strict=True)) == 1

    naam = "I03_MEDIAGUARD.checklist.yaml"
    _kopie(tmp_path, naam, wijzig=wijzig)
    assert any("I03-03-01: acceptatie ≠ Excel" in f for f in cl.controleer_alle(tmp_path)[naam])
    assert cl.main(tmp_path) == 1


def _i04_veiligheid(data: dict[str, Any]) -> None:
    """Bewaar akkoord, terugval en de nog ontbrekende gescopete Hub-toegang."""
    for stap in data["stappen"]:
        assert stap["blokkeert_volgende"] is True
        assert not stap.get("truenas_methoden")
        assert "Sim-getest" not in stap["validatie_nu"]
        assert ("NIET GEVALIDEERD (echte Hub)" in stap["validatie_nu"]) == (stap["id"] == "I04-04-08")
    for index in (3, 5):
        stap = data["stappen"][index]
        assert "T-12" in stap["uitvoeren_nl"] and "T-12" in stap["bij_fout_nl"]
        assert "toestemming" in stap["uitvoeren_nl"] and "testbronnen" in stap["uitvoeren_nl"]
        assert "NIET GEVALIDEERD (echte NAS)" in stap["validatie_nu"]
    backup = data["stappen"][6]
    for tekst in ("Back-up maken", "Terugzetten", "geïsoleerde", "sleutels", "vorige image", "snapshot",
                  "geen dubbele", "nieuwere", "niet omzeilen"):
        assert tekst in backup["uitvoeren_nl"], tekst
    assert "ontbreekt een aantoonbare veilige overdracht via de webinterface, stop dan hier." in backup["uitvoeren_nl"]
    hub = data["stappen"][7]
    assert hub["wijze"] == ["handmatig_bewijs"]
    for tekst in ("S-P3-220", "nog niet", "machine-sleutel", "read", "Hub-UI", "vault", "geen enrollment",
                  "menselijk wachtwoord", "CSRF", "geen directe", "onbekend", "gelijktijdige goedkeuring"):
        assert tekst in hub["uitvoeren_nl"], tekst


def test_i04_is_delivered_with_literal_tasks_and_safety_gates() -> None:
    data = _load("I04_MEDIA_SOURCE_OPTIMIZER.checklist.yaml")
    assert data["aanpak"] == "behouden_en_valideren"
    assert len(data["stappen"]) == 8
    assert cl.controleer_lijst(data, cl.excel_taken()) == []
    _i04_veiligheid(data)


@pytest.mark.parametrize(("index", "veld", "tekst"), [
    (3, "uitvoeren_nl", "T-12"),
    (3, "bij_fout_nl", "T-12"),
    (5, "uitvoeren_nl", "toestemming"),
    (5, "bij_fout_nl", "T-12"),
    (6, "blokkeert_volgende", None),
    (6, "uitvoeren_nl", "snapshot"),
    (6, "uitvoeren_nl", "niet omzeilen"),
    (7, "uitvoeren_nl", "nog niet"),
    (7, "uitvoeren_nl", "machine-sleutel"),
    (7, "uitvoeren_nl", "geen directe"),
])
def test_i04_missing_safety_condition_is_detected(index: int, veld: str, tekst: str | None) -> None:
    data = copy.deepcopy(_load("I04_MEDIA_SOURCE_OPTIMIZER.checklist.yaml"))
    stap = data["stappen"][index]
    if tekst is None:
        stap[veld] = False
    else:
        assert tekst in stap[veld]
        stap[veld] = stap[veld].replace(tekst, "ontbreekt")
    with pytest.raises(AssertionError):
        _i04_veiligheid(data)


def test_i04_one_character_change_in_acceptance_is_rejected(tmp_path: Path) -> None:
    def wijzig(data: dict[str, Any]) -> None:
        stap = data["stappen"][0]
        origineel = stap["acceptatie_nl"]
        stap["acceptatie_nl"] = "X" + origineel[1:]
        assert sum(a != b for a, b in zip(origineel, stap["acceptatie_nl"], strict=True)) == 1

    naam = "I04_MEDIA_SOURCE_OPTIMIZER.checklist.yaml"
    _kopie(tmp_path, naam, wijzig=wijzig)
    assert any("I04-04-01: acceptatie ≠ Excel" in f for f in cl.controleer_alle(tmp_path)[naam])
    assert cl.main(tmp_path) == 1


@pytest.mark.parametrize("naam", LIJSTEN)
def test_a_copy_with_one_step_less_than_the_excel_tasks_fails(tmp_path: Path, naam: str) -> None:
    _kopie(tmp_path, naam, wijzig=lambda d: d["stappen"].pop())
    fout = cl.controleer_alle(tmp_path)[naam]
    assert any("dekken de Excel-taken niet exact" in f for f in fout), fout
    assert cl.main(tmp_path) == 1


@pytest.mark.parametrize("naam", LIJSTEN)
def test_a_second_list_for_the_same_document_fails(tmp_path: Path, naam: str) -> None:
    _kopie(tmp_path, naam)
    tweede = _kopie(tmp_path, naam, doel=naam.replace(".checklist.yaml", "_TWEEDE.checklist.yaml"))
    uitkomst = cl.controleer_alle(tmp_path)
    for bestand in (naam, tweede.name):
        assert any("heeft twee lijsten" in f for f in uitkomst[bestand]), (bestand, uitkomst)
    assert cl.main(tmp_path) == 1


def test_a_list_whose_file_name_does_not_match_its_document_fails(tmp_path: Path) -> None:
    _kopie(tmp_path, "I01_TRUENAS_BASIS.checklist.yaml", doel="I03_MEDIAGUARD.checklist.yaml")
    fout = cl.controleer_alle(tmp_path)["I03_MEDIAGUARD.checklist.yaml"]
    assert any("bestandsnaam past niet bij het document" in f for f in fout), fout
    assert cl.main(tmp_path) == 1


def test_a_list_for_a_document_that_does_not_exist_fails(tmp_path: Path) -> None:
    def wijzig(d: dict[str, Any]) -> None:
        d["document"] = "03_INSTALLATIE/I01_BESTAAT_NIET.md"

    _kopie(tmp_path, "I01_TRUENAS_BASIS.checklist.yaml", doel="I01_BESTAAT_NIET.checklist.yaml", wijzig=wijzig)
    fout = cl.controleer_alle(tmp_path)["I01_BESTAAT_NIET.checklist.yaml"]
    assert any("bestaat niet" in f for f in fout), fout


def test_an_empty_folder_is_never_green(tmp_path: Path) -> None:
    assert cl.controleer_alle(tmp_path) == {}
    assert cl.main(tmp_path) == 1


@pytest.mark.parametrize("vreemd", ["I19_SEERR.checklist.yml", "I19_SEERR.yaml", "LEESMIJ.md"])
def test_any_other_file_in_the_folder_fails(tmp_path: Path, vreemd: str) -> None:
    """R-TECH-1: een bestand dat geen ``<DOCUMENT>.checklist.yaml`` is, ontloopt de controle niet."""
    for naam in LIJSTEN:
        _kopie(tmp_path, naam)
    _neplijst(tmp_path, "03_INSTALLATIE/I19_SEERR.md", naam=vreemd)
    uitkomst = cl.controleer_alle(tmp_path)
    assert set(uitkomst) == {p.name for p in tmp_path.iterdir()}
    assert any("geen controlelijst" in f for f in uitkomst[vreemd]), uitkomst
    assert cl.main(tmp_path) == 1


def _neplijst(map_: Path, document: str, wijzig: Any = None, naam: str | None = None) -> Path:
    """Proeflijst voor ``document`` met de letterlijke Excel-taken, op de stappen van I01 (alleen in tmp_path)."""
    basis = _load("I01_TRUENAS_BASIS.checklist.yaml")
    data = copy.deepcopy(basis)
    data.update(id=Path(document).name[:3], document=document, titel_nl=f"Proeflijst {Path(document).stem}")
    data["stappen"] = []
    for i, taak in enumerate(cl.excel_taken()[document]):
        stap = copy.deepcopy(basis["stappen"][min(i, len(basis["stappen"]) - 1)])
        stap.update(id=taak["ID"], titel_nl=taak["Taak"], acceptatie_nl=taak["Acceptatie"], risico=taak["Risico"])
        data["stappen"].append(stap)
    if wijzig is not None:
        wijzig(data)
    pad = map_ / (naam or cl.bestandsnaam(document))
    pad.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return pad


#: De documenten van ADR-0027 (bestaande apps); hun lijsten volgen in S-P3-410…440.
BESTAANDE_APPS = ["I03_MEDIAGUARD", "I04_MEDIA_SOURCE_OPTIMIZER", "I05_MY_HOUSE", "I15_IMMICH", "I16_PROWLARR",
                  "I17_SABNZBD", "I18_QBITTORRENT", "I19_SEERR", "I20_BAZARR"]


def test_the_literal_excel_texts_of_every_document_pass_the_text_rules() -> None:
    """R-MANIFEST-1: de lijst moet titel en acceptatie letterlijk uit de Excel nemen, dus mogen de terminal- en de
    voorbeeldregel geen Excel-tekst weigeren (anders kan zo'n lijst nooit groen worden)."""
    taken = [t for ts in cl.excel_taken().values() for t in ts]
    assert len(taken) >= 100, len(taken)
    gezien = {t["ID"]: t["Acceptatie"] for t in taken}
    assert "chown" in gezien["I03-03-02"], gezien.get("I03-03-02")  # het geval dat eerder fout ging
    for taak in taken:
        for veld in ("Taak", "Acceptatie"):
            tekst = str(taak[veld])
            assert not cl.TERMINAL.search(tekst), (taak["ID"], veld, tekst)
            assert not cl.VOORBEELD.search(tekst), (taak["ID"], veld, tekst)


def test_lists_with_the_literal_excel_texts_of_the_existing_apps_pass(tmp_path: Path) -> None:
    """R-MANIFEST-1/-2 en Z-18 optie 2: een lijst per document van ADR-0027 met de letterlijke Excel-titels en
    -acceptaties is groen, ook I03 (chmod/chown in de acceptatie) en I05 (hernoemde titel zonder YAML, YAML alleen nog
    in acceptatie en afwijking); de weg zelf staat in uitvoeren_nl zonder YAML."""

    def i05(d: dict[str, Any]) -> None:
        stap = next(s for s in d["stappen"] if s["id"] == "I05-05-03")
        assert "YAML" not in stap["titel_nl"] and "Custom App" in stap["titel_nl"]  # Z-18 optie 2: hernoemd
        stap["afwijking_van_document_nl"] = ("Het document noemt bij de uitvoering YAML; de weg is het formulier van "
                                             "de standaard Custom App (Z-18: optie 2).")

    def verwijs(doc: str) -> Any:
        """Heeft het document een manifestkopie (S-P3-450), dan verwijst de lijst ernaar."""
        kopie = KOPIE_PER_DOC.get(doc[:3])

        def wijzig(d: dict[str, Any]) -> None:
            if doc.startswith("I05"):
                i05(d)
            if kopie:
                d["stappen"][0]["wijze"].append("verwijzing")
                d["stappen"][0]["verwijzing"] = [f"install/manifests/{kopie}"]
        return wijzig

    for doc in BESTAANDE_APPS:
        _neplijst(tmp_path, f"03_INSTALLATIE/{doc}.md", wijzig=verwijs(doc))
    uitkomst = cl.controleer_alle(tmp_path)
    assert uitkomst == {f"{doc}.checklist.yaml": [] for doc in BESTAANDE_APPS}, uitkomst
    assert cl.main(tmp_path) == 0


def test_a_list_for_a_document_with_an_installable_manifest_fails(tmp_path: Path) -> None:
    """R-MANIFEST-3: I13 (Gotify) heeft ``install/manifests/gotify.manifest.yaml``; een lijst installeert niets en is
    alleen voor documenten zonder installatie."""
    _neplijst(tmp_path, "03_INSTALLATIE/I13_GOTIFY.md")
    fout = cl.controleer_alle(tmp_path)["I13_GOTIFY.checklist.yaml"]
    assert any("installeerbaar manifest" in f and "gotify.manifest.yaml" in f for f in fout), fout
    assert cl.main(tmp_path) == 1


KOPIE_PER_DOC = {"I03": "mediaguard.manifest.yaml", "I04": "media-source-optimizer.manifest.yaml",
                 "I05": "my_house.manifest.yaml"}


@pytest.mark.parametrize("idoc", sorted(KOPIE_PER_DOC))
def test_a_list_next_to_a_manifest_copy_must_refer_to_it(tmp_path: Path, idoc: str) -> None:
    """S-P3-450 bouwpunt 6: een manifestkopie van een andere repo mag naast de lijst van haar document staan; de lijst
    verwijst er dan naar (veld ``verwijzing``). Zonder die verwijzing faalt de lijst; met een verwijzing naar iets
    anders ook."""
    doc = next(d for d in BESTAANDE_APPS if d.startswith(idoc))
    kopie = KOPIE_PER_DOC[idoc]
    assert kopie in cl.documenten_met_manifest()[idoc] and kopie in cl.KOPIEEN

    def met(verwijzing: list[str]) -> Any:
        def wijzig(d: dict[str, Any]) -> None:
            if idoc == "I05":
                stap = next(s for s in d["stappen"] if s["id"] == "I05-05-03")
                stap["afwijking_van_document_nl"] = "Het document noemt YAML; de weg is het formulier (Z-18: optie 2)."
            d["stappen"][0]["wijze"].append("verwijzing")
            d["stappen"][0]["verwijzing"] = verwijzing
        return wijzig

    naam = cl.bestandsnaam(f"03_INSTALLATIE/{doc}.md")
    _neplijst(tmp_path, f"03_INSTALLATIE/{doc}.md", wijzig=met([f"install/manifests/{kopie}"]))
    assert cl.controleer_alle(tmp_path)[naam] == []
    for fout_verwijzing in ([], ["install/manifests/gotify.manifest.yaml"]):
        _neplijst(tmp_path, f"03_INSTALLATIE/{doc}.md", wijzig=met(fout_verwijzing))
        fout = cl.controleer_alle(tmp_path)[naam]
        assert any(f"manifestkopie {kopie}" in f for f in fout), fout
    _neplijst(tmp_path, f"03_INSTALLATIE/{doc}.md")
    assert any(f"manifestkopie {kopie}" in f for f in cl.controleer_alle(tmp_path)[naam])
    assert cl.main(tmp_path) == 1


def test_an_own_manifest_next_to_a_list_still_fails_even_with_a_reference(tmp_path: Path) -> None:
    """Alleen een kopie van een andere repo mag naast een lijst; een eigen manifest blijft de lijst weigeren."""
    lijsten, manifesten = tmp_path / "checklists", tmp_path / "manifests"
    lijsten.mkdir()
    manifesten.mkdir()
    _kopie(lijsten, "I03_MEDIAGUARD.checklist.yaml")
    shutil.copy(cl.MANIFEST_DIR / "mediaguard.manifest.yaml", manifesten / "mediaguard.manifest.yaml")
    assert cl.controleer_alle(lijsten, manifesten) == {"I03_MEDIAGUARD.checklist.yaml": []}
    (manifesten / "eigen.manifest.yaml").write_text(yaml.safe_dump(
        {"app_id": "eigen", "install": {"method": "custom_app"}, "docs": ["I03_MEDIAGUARD"]}), encoding="utf-8")
    fout = cl.controleer_alle(lijsten, manifesten)["I03_MEDIAGUARD.checklist.yaml"]
    assert any("installeerbaar manifest (eigen.manifest.yaml)" in f for f in fout), fout


def test_an_installable_manifest_for_an_existing_list_makes_it_fail(tmp_path: Path) -> None:
    lijsten, manifesten = tmp_path / "checklists", tmp_path / "manifests"
    lijsten.mkdir()
    manifesten.mkdir()
    _kopie(lijsten, "I01_TRUENAS_BASIS.checklist.yaml")
    assert cl.controleer_alle(lijsten, manifesten) == {"I01_TRUENAS_BASIS.checklist.yaml": []}
    (manifesten / "proef.manifest.yaml").write_text(yaml.safe_dump(
        {"app_id": "proef", "install": {"method": "custom_app"}, "docs": ["I01_TRUENAS_BASIS"]}), encoding="utf-8")
    fout = cl.controleer_alle(lijsten, manifesten)["I01_TRUENAS_BASIS.checklist.yaml"]
    assert any("installeerbaar manifest" in f for f in fout), fout
    assert cl.main(lijsten, manifesten) == 1


def _mutate(naam: str, wijzig: Any) -> list[str]:
    data = copy.deepcopy(_load(naam))
    wijzig(data)
    return cl.controleer_lijst(data, cl.excel_taken())


def _stap(d: dict[str, Any], index: int) -> dict[str, Any]:
    """Stap ``index``, of de laatste als de lijst korter is (I20 heeft 5 stappen; I01 met 8 houdt dezelfde stap)."""
    stappen: list[dict[str, Any]] = d["stappen"]
    return stappen[min(index, len(stappen) - 1)]


def _set_method(d: dict[str, Any], naam: str) -> None:
    _stap(d, 0).setdefault("truenas_methoden", []).append({"naam": naam, "versies": ["25.10"]})


def _onbekend_besluit(d: dict[str, Any]) -> None:
    vrij = [b for b in (f"D{n:02d}" for n in range(18, 0, -1)) if b not in d["besluiten"]]
    assert vrij, "alle besluiten D01-D18 staan al in de lijst"
    _stap(d, 0)["besluiten"] = [vrij[0]]


VOORBEELD_IP = ".".join(("192", "0", "2", "10"))
MUTATIES = [
    ("schrijvende methode", lambda d: _set_method(d, "app.create"), "geen alleen-lezen-methode"),
    ("snapshot maken", lambda d: _set_method(d, "pool.snapshot.create"), "geen alleen-lezen-methode"),
    ("dataset-rechten", lambda d: _set_method(d, "filesystem.setperm"), "geen alleen-lezen-methode"),
    ("stap ontbreekt", lambda d: d["stappen"].pop(), "dekken de Excel-taken niet exact"),
    ("acceptatie herschreven", lambda d: _stap(d, 1).update(acceptatie_nl="Alles groen."), "acceptatie ≠ Excel"),
    ("terminal", lambda d: _stap(d, 2).update(uitvoeren_nl="Voer `ls -l /dev/dri` uit op de NAS."),
     "terminalopdracht"),
    ("terminal chown", lambda d: _stap(d, 2).update(uitvoeren_nl="Zet de rechten met chown -R 568:568 /data."),
     "terminalopdracht"),
    ("terminal met subcommando",
     lambda d: _stap(d, 2).update(uitvoeren_nl="Lees de pools met midclt call pool.query."), "terminalopdracht"),
    ("terminal tussen backticks",
     lambda d: _stap(d, 2).update(uitvoeren_nl="Voer `cat /etc/os-release` uit op de NAS."), "terminalopdracht"),
    ("terminal met lijstwoord als pad",
     lambda d: _stap(d, 2).update(uitvoeren_nl="Ruim op met rm de/oude/map."), "terminalopdracht"),
    ("YAML-weg", lambda d: _stap(d, 2).update(
        uitvoeren_nl="Open Apps > Discover Apps > Install via YAML en plak de compose-YAML uit de repo."),
     "YAML-, compose- of scriptweg in uitvoeren_nl"),
    ("YAML-weg in het menupad",
     lambda d: _stap(d, 2).update(handmatig_ui_pad_nl="Apps > Discover Apps > Install via YAML"),
     "YAML-, compose- of scriptweg in handmatig_ui_pad_nl"),
    ("installatiescript bij fout",
     lambda d: _stap(d, 2).update(bij_fout_nl="Draai install.sh opnieuw voor de geheimen."),
     "YAML-, compose- of scriptweg in bij_fout_nl"),
    ("voorbeeld-IP", lambda d: _stap(d, 3).update(uitvoeren_nl=f"Verbind met wss://{VOORBEELD_IP}/api/current."),
     "voorbeeldwaarde in uitvoeren_nl"),
    ("voorbeeldpool", lambda d: _stap(d, 6).update(uitvoeren_nl="Maak datasets onder /mnt/tank/apps aan."),
     "voorbeeldwaarde"),
    ("alleen Sim-getest", lambda d: _stap(d, 0).update(validatie_nu=["Sim-getest"]), "nooit alleen Sim-getest"),
    ("installeert iets", lambda d: d.update(installatie="custom_app"), "schema"),
    ("muteert", lambda d: d.update(mutaties_door_platform=True), "schema"),
    ("besluit onbekend", _onbekend_besluit, "besluit niet in de lijst"),
]


@pytest.mark.parametrize("naam", LIJSTEN)
@pytest.mark.parametrize(("beschrijving", "wijzig", "verwacht"), MUTATIES, ids=[m[0] for m in MUTATIES])
def test_violations_are_detected(naam: str, beschrijving: str, wijzig: Any, verwacht: str) -> None:
    fout = _mutate(naam, wijzig)
    assert any(verwacht in f for f in fout), (naam, beschrijving, fout)


#: Elk paar (lijst, stap) met een account-stap: OneDrive/account in uitvoeren_nl en bewijs met de hand.
ACCOUNT_STAPPEN = [
    (naam, stap["id"])
    for naam in LIJSTEN
    for stap in _load(naam)["stappen"]
    if cl.ACCOUNT_STAP.search(stap["uitvoeren_nl"]) and "handmatig_bewijs" in stap["wijze"]
]


def test_the_account_steps_are_found() -> None:
    # ondergrens (V-003): de OneDrive-controle I02-02-02 is er altijd
    assert ("I02_BACKUP_EN_RESTORE.checklist.yaml", "I02-02-02") in ACCOUNT_STAPPEN, ACCOUNT_STAPPEN


@pytest.mark.parametrize(("naam", "stap_id"), ACCOUNT_STAPPEN, ids=[s for _, s in ACCOUNT_STAPPEN])
def test_account_steps_are_never_claimed_without_the_account(naam: str, stap_id: str) -> None:
    def wijzig(d: dict[str, Any]) -> None:
        next(s for s in d["stappen"] if s["id"] == stap_id)["validatie_nu"] = ["NIET GEVALIDEERD (echte NAS)"]

    fout = _mutate(naam, wijzig)
    assert any(f.startswith(f"{stap_id}: ") and "NIET GEVALIDEERD (account)" in f for f in fout), fout


# ------------------------------------------------------------------ tegen de TrueNAS-simulator (alleen lezen)
truenas_sim = pytest.importorskip("truenas_sim.server")
pytestmark = pytest.mark.filterwarnings("ignore:Using `httpx` with `starlette.testclient` is deprecated")


@pytest.fixture(params=["25.04.2.6", "25.10.8"])
def ro(request: pytest.FixtureRequest) -> Iterator[tuple[str, Any]]:
    from starlette.testclient import TestClient

    server = truenas_sim.SimServer(request.param)
    with TestClient(truenas_sim.create_app(server)) as client:
        key = client.get("/_sim/keys").json()["readonly"]
        with client.websocket_connect("/api/current") as ws:
            seq = iter(range(1, 10_000))

            def call(method: str, *params: Any) -> dict[str, Any]:
                ident = next(seq)
                ws.send_text(json.dumps({"jsonrpc": "2.0", "id": ident, "method": method, "params": list(params)}))
                while True:
                    msg = json.loads(ws.receive_text())
                    if msg.get("id") == ident:
                        return dict(msg)

            assert call("auth.login_with_api_key", key)["result"] is True
            yield str(request.param)[:5], call


def test_every_listed_method_is_callable_read_only_on_its_versions(ro: tuple[str, Any]) -> None:
    familie, call = ro
    gezien_elders: set[str] = set()
    for pad in cl.lijsten():
        for stap in _load(pad.name)["stappen"]:
            for m in stap.get("truenas_methoden", []):
                antwoord = call(m["naam"])
                if familie in m["versies"]:
                    assert "result" in antwoord, (stap["id"], m["naam"], familie, antwoord.get("error"))
                else:
                    gezien_elders.add(m["naam"])
                    assert antwoord.get("error", {}).get("code") == -32601, (m["naam"], familie)
    # de versieverschillen uit TRUENAS_APP_API.md §6 zitten echt in de lijsten
    assert gezien_elders, "geen versie-afhankelijke methode getest"


def test_checklists_are_kind_checklist_for_the_appstore_validator() -> None:
    """Contract 1.3 §9 (C1.3-JLP-09): elke controlelijst is documentsoort ``kind: checklist`` (``install.method:
    none``); ``jl-manifest checklist`` accepteert ze en de manifestvalidator weigert ze als installatie."""
    import os
    import subprocess

    validator = os.environ.get("JL_MANIFEST")
    if not validator or not Path(validator).is_file():
        if os.environ.get("MANIFEST_VALIDATOR_VERPLICHT") == "1":
            pytest.fail("jl-manifest niet gevonden (JL_MANIFEST)")
        pytest.skip("jl-manifest (appstore-en-fork) niet beschikbaar; in CI verplicht")
    for pad in cl.lijsten():
        data = yaml.safe_load(pad.read_text(encoding="utf-8"))
        assert data["kind"] == "checklist" and data["install"] == {"method": "none"}
        goed = json.loads(subprocess.run([validator, "checklist", str(pad), "--format", "json"], capture_output=True,
                                         text=True, check=False).stdout)
        assert goed["valid"] is True and goed["errors"] == 0, (pad.name, goed["findings"])
        als_manifest = json.loads(subprocess.run([validator, "validate", "--strict", str(pad), "--format", "json"],
                                                 capture_output=True, text=True, check=False).stdout)
        assert als_manifest["valid"] is False
        assert "checklist_not_installable" in {f["code"] for f in als_manifest["findings"]}


def test_i04_stop_bij_ontbrekende_hersteloverdracht() -> None:
    tekst = _load("I04_MEDIA_SOURCE_OPTIMIZER.checklist.yaml")["stappen"][6]["uitvoeren_nl"]
    assert "ontbreekt een aantoonbare veilige overdracht via de webinterface, stop dan hier." in tekst


def test_menupaden_gebruiken_letterlijke_bronnamen() -> None:
    for naam in ("I03_MEDIAGUARD.checklist.yaml", "I04_MEDIA_SOURCE_OPTIMIZER.checklist.yaml"):
        assert "Storage Configuration en Resources Configuration" in _load(naam)["stappen"][1]["handmatig_ui_pad_nl"]
    mso = _load("I04_MEDIA_SOURCE_OPTIMIZER.checklist.yaml")
    assert "Verbindingen → MediaGuard Ultimate" in mso["stappen"][2]["uitvoeren_nl"]


def test_i04_hersteloverdracht_mutatie_wordt_geweigerd() -> None:
    data = copy.deepcopy(_load("I04_MEDIA_SOURCE_OPTIMIZER.checklist.yaml"))
    stap = data["stappen"][6]
    assert "stop dan hier." in stap["uitvoeren_nl"]
    stap["uitvoeren_nl"] = stap["uitvoeren_nl"].replace("stop dan hier.", "ga dan toch verder.")
    with pytest.raises(AssertionError):
        _i04_veiligheid(data)


def test_i03_android_herkomst_zonder_onuitgebrachte_release() -> None:
    tekst = _load("I03_MEDIAGUARD.checklist.yaml")["stappen"][6]["uitvoeren_nl"]
    assert "de al geïnstalleerde app of de APK uit de CI" in tekst
    assert "eigen release-app" not in tekst


def _bestaande_app_basis(data: dict[str, Any], hub: set[int], account: set[int], aantal: int = 6) -> None:
    assert data["aanpak"] == "behouden_en_valideren"
    assert data["install"] == {"method": "none"}
    assert data["installatie"] == "geen" and data["mutaties_door_platform"] is False
    assert len(data["stappen"]) == aantal
    assert cl.controleer_lijst(data, cl.excel_taken()) == []
    for nummer, stap in enumerate(data["stappen"], 1):
        assert not stap.get("truenas_methoden")
        assert "Sim-getest" not in stap["validatie_nu"]
        assert "NIET GEVALIDEERD (echte NAS)" in stap["validatie_nu"]
        assert ("NIET GEVALIDEERD (echte Hub)" in stap["validatie_nu"]) == (nummer in hub)
        assert ("NIET GEVALIDEERD (account)" in stap["validatie_nu"]) == (nummer in account)
        assert stap["blokkeert_volgende"] is True
        assert stap["bij_fout_nl"].startswith("Stop hier;")


I15_GATES = [
    (0, "Zijn die niet aantoonbaar, stop dan hier"),
    (0, "Niets opnieuw importeren of verplaatsen"),
    (1, "uitsluitend in de geïsoleerde testomgeving"),
    (1, "foto's én database"),
    (1, "ontbreekt een aangetoonde veilige overdracht, stop dan hier"),
    (1, "nooit op de bestaande installatie"),
    (3, "wacht op de nog niet gebouwde Immich-adapter"),
    (3, "geen face embeddings"),
    (4, "wacht op de nog niet gebouwde Immich-adapter"),
    (5, "Alleen na het volledige herstelbewijs van I15-15-02"),
    (5, "vorige image én bijpassende database en mediakopie"),
    (5, "bij ontbrekend herstelbewijs, stop dan hier"),
]


def _i15_veiligheid(data: dict[str, Any]) -> None:
    _bestaande_app_basis(data, {4, 5}, {2})
    herstel = data["stappen"][1]
    assert "verwijzing" in herstel["wijze"]
    assert "install/checklists/I02_BACKUP_EN_RESTORE.checklist.yaml#I02-02-03" in herstel["verwijzing"]
    for index, tekst in I15_GATES:
        assert tekst in data["stappen"][index]["uitvoeren_nl"], tekst


def test_i15_letterlijke_taken_en_fotobescherming() -> None:
    _i15_veiligheid(_load("I15_IMMICH.checklist.yaml"))


@pytest.mark.parametrize(("index", "tekst"), I15_GATES)
def test_i15_veiligheidsmutatie_wordt_geweigerd(index: int, tekst: str) -> None:
    data = copy.deepcopy(_load("I15_IMMICH.checklist.yaml"))
    assert tekst in data["stappen"][index]["uitvoeren_nl"]
    data["stappen"][index]["uitvoeren_nl"] = data["stappen"][index]["uitvoeren_nl"].replace(tekst, "ga toch verder")
    with pytest.raises(AssertionError):
        _i15_veiligheid(data)


@pytest.mark.parametrize("index", range(6))
def test_i15_stopzin_wordt_bewaakt(index: int) -> None:
    data = copy.deepcopy(_load("I15_IMMICH.checklist.yaml"))
    data["stappen"][index]["bij_fout_nl"] = "Ga verder; negeer de fout."
    with pytest.raises(AssertionError):
        _i15_veiligheid(data)


I16_GATES = [
    (0, "Zijn die niet aantoonbaar, stop dan hier"),
    (0, "Indexers → Stats"),
    (0, "Settings → Apps"),
    (1, "MediaGuard wijzigt alleen de eigen tags"),
    (1, "geen tweede reconciler"),
    (2, "System → Backup → Backup Now"),
    (2, "uitgaand verkeer naar bestaande diensten geblokkeerd"),
    (2, "ontbreekt een aangetoonde veilige overdracht, stop dan hier"),
    (3, "lusdetectie is eigendom van MSO"),
    (3, "T-12"),
    (4, "Hub-/MSO-meldingsketen is nog niet volledig aangetoond"),
    (5, "Alleen na het volledige herstelbewijs van I16-16-03"),
    (5, "vorige image én passende configuratie/database"),
    (5, "bij ontbrekend herstelbewijs, stop dan hier"),
]


def _i16_veiligheid(data: dict[str, Any]) -> None:
    _bestaande_app_basis(data, {5}, set())
    for index, tekst in I16_GATES:
        assert tekst in data["stappen"][index]["uitvoeren_nl"], tekst
    assert "Emby blijft werken" in data["toelichting_nl"]
    assert "MediaGuard-voortgang" in data["toelichting_nl"]


def test_i16_letterlijke_taken_en_ketenbescherming() -> None:
    _i16_veiligheid(_load("I16_PROWLARR.checklist.yaml"))


@pytest.mark.parametrize(("index", "tekst"), I16_GATES)
def test_i16_veiligheidsmutatie_wordt_geweigerd(index: int, tekst: str) -> None:
    data = copy.deepcopy(_load("I16_PROWLARR.checklist.yaml"))
    assert tekst in data["stappen"][index]["uitvoeren_nl"]
    data["stappen"][index]["uitvoeren_nl"] = data["stappen"][index]["uitvoeren_nl"].replace(tekst, "ga toch verder")
    with pytest.raises(AssertionError):
        _i16_veiligheid(data)


@pytest.mark.parametrize("index", range(6))
def test_i16_stopzin_wordt_bewaakt(index: int) -> None:
    data = copy.deepcopy(_load("I16_PROWLARR.checklist.yaml"))
    data["stappen"][index]["bij_fout_nl"] = "Ga verder; negeer de fout."
    with pytest.raises(AssertionError):
        _i16_veiligheid(data)


I17_GATES = [
    (0, "Zijn die niet aantoonbaar, stop dan hier"),
    (0, "Config → Categories"),
    (0, "tv/tv-anime/movies/movies-anime"),
    (1, "geen blinde retries"),
    (2, "laatste werkende server blijft beschermd"),
    (2, "preview/readback/rollback"),
    (2, "bij ontbrekend herstelbewijs, stop dan hier"),
    (3, "geen bestaande bestanden verplaatsen of verwijderen"),
    (3, "I17-17-05"),
    (4, "Config → Folders → Backup Folder"),
    (4, "Is Backup Folder leeg, stop dan hier"),
    (4, "Completed Download Folder"),
    (4, "Config → General → Backup → Create backup"),
    (4, "Config → General → Backup → Restore backup"),
    (4, "ontbreekt een aangetoonde veilige overdracht, stop dan hier"),
    (4, "nooit op de bestaande SABnzbd"),
    (5, "Alleen na het volledige herstelbewijs van I17-17-05"),
    (5, "vorige image én passende configuratie/database"),
    (5, "Emby blijft werken"),
    (5, "bij ontbrekend herstelbewijs, stop dan hier"),
]


def _i17_veiligheid(data: dict[str, Any]) -> None:
    _bestaande_app_basis(data, set(), set())
    for index, tekst in I17_GATES:
        assert tekst in data["stappen"][index]["uitvoeren_nl"], tekst
    assert "MediaGuard-voortgang" in data["toelichting_nl"]


def test_i17_letterlijke_taken_en_ketenbescherming() -> None:
    _i17_veiligheid(_load("I17_SABNZBD.checklist.yaml"))


@pytest.mark.parametrize(("index", "tekst"), I17_GATES)
def test_i17_veiligheidsmutatie_wordt_geweigerd(index: int, tekst: str) -> None:
    data = copy.deepcopy(_load("I17_SABNZBD.checklist.yaml"))
    assert tekst in data["stappen"][index]["uitvoeren_nl"]
    data["stappen"][index]["uitvoeren_nl"] = data["stappen"][index]["uitvoeren_nl"].replace(tekst, "ga toch verder")
    with pytest.raises(AssertionError):
        _i17_veiligheid(data)


@pytest.mark.parametrize("index", range(6))
def test_i17_stopzin_wordt_bewaakt(index: int) -> None:
    data = copy.deepcopy(_load("I17_SABNZBD.checklist.yaml"))
    data["stappen"][index]["bij_fout_nl"] = "Ga verder; negeer de fout."
    with pytest.raises(AssertionError):
        _i17_veiligheid(data)


CATALOGUSLIJSTEN = [
    "I15_IMMICH.checklist.yaml", "I16_PROWLARR.checklist.yaml", "I17_SABNZBD.checklist.yaml",
    "I18_QBITTORRENT.checklist.yaml", "I19_SEERR.checklist.yaml",
]


def _catalogus_menupaden(data: dict[str, Any], update_index: int | None = 5) -> None:
    """ix-app heeft Image; de app-eigen catalogusschema's hebben dat veld niet (I20 heeft geen updatestap)."""
    inventaris = data["stappen"][0]["uitvoeren_nl"]
    for tekst in ("Stel eerst de installatiesoort vast", "Application Info", "Name", "Train", "ix-app",
                  "ONBEKEND", "stop dan hier", "Zijn die niet aantoonbaar, stop dan hier",
                  "Edit → Image Configuration → Image: Repository en Tag",
                  "staat er geen @sha256-digest in Tag, stop dan hier"):
        assert tekst in inventaris, tekst
    # Elke zin van elke stap, ongeacht positie, hoofdletters of het gebruikte lidwoord.
    # Splits alleen op zinsgrenzen: punten binnen versies/adressen zijn geen zinsgrens.
    for stap in data["stappen"]:
        for zin in re.split(r"[.!?](?:\s+|$)", stap["uitvoeren_nl"]):
            assert not ("catalogusapp" in zin.casefold() and "Edit → Image" in zin), (
                "catalogusinstallatie heeft geen Image-veld", zin
            )
    # Ook de uitvoertekst kan een onjuist pad introduceren terwijl het aparte UI-veld klopt.
    catalogus_inventaris = inventaris.partition("Bij een catalogusapp")[2].partition(";")[0]
    assert catalogus_inventaris and "Image" not in catalogus_inventaris
    assert "Application Info" in catalogus_inventaris
    for fragment in inventaris.replace("catalogusapp:", "Bij een catalogusapp").split("Bij een catalogusapp")[1:]:
        assert "Image" not in fragment.partition(";")[0], "catalogusinstallatie heeft geen Image-veld"
    if update_index is not None:
        catalogus_update = data["stappen"][update_index]["uitvoeren_nl"].partition("Catalogusapp:")[2]
        assert catalogus_update and "Edit → Image" not in catalogus_update
        assert "More Options → Update" in catalogus_update
    for index in ((0,) if update_index is None else (0, update_index)):
        stap = data["stappen"][index]
        standaard, scheiding, catalogus = stap["handmatig_ui_pad_nl"].partition("; catalogusapp: ")
        assert standaard.startswith("Standaard Custom App: ") and scheiding
        assert "Edit → Image" in standaard
        assert "Image" not in catalogus, "catalogusinstallatie heeft geen Image-veld"
        assert "Application Info → App Version en Version" in catalogus
        assert stap["blokkeert_volgende"] is True
        assert "ONBEKEND" in stap["uitvoeren_nl"] and "stop dan hier" in stap["uitvoeren_nl"]
        if index == 5:
            for tekst in ("eigen geïsoleerde testapp", "More Options → Update", "Version to be updated to"):
                assert tekst in catalogus, tekst
            for tekst in ("Close", "Only image(s) will be updated", "geen ombouw", "niet beschikbaar"):
                assert tekst in stap["uitvoeren_nl"], tekst


@pytest.mark.parametrize("naam", CATALOGUSLIJSTEN)
def test_catalogus_menupaden_scheiden_installatiesoorten(naam: str) -> None:
    _catalogus_menupaden(_load(naam))


@pytest.mark.parametrize("naam", CATALOGUSLIJSTEN)
@pytest.mark.parametrize("index", (0, 5))
@pytest.mark.parametrize("mutatie", ("catalogus-image", "onvoorwaardelijk-image"))
def test_catalogus_image_pad_wordt_geweigerd(naam: str, index: int, mutatie: str) -> None:
    data = copy.deepcopy(_load(naam))
    _catalogus_menupaden(data)  # Eerst een groene nulmeting, zodat de weigering de mutatie bewijst.
    stap = data["stappen"][index]
    if mutatie == "catalogus-image":
        stap["handmatig_ui_pad_nl"] += " → Edit → Image"
    else:
        stap["handmatig_ui_pad_nl"] = "Apps → bestaande app → Edit → Image"
    with pytest.raises(AssertionError):
        _catalogus_menupaden(data)


@pytest.mark.parametrize("naam", CATALOGUSLIJSTEN)
def test_installatiesoort_onbekend_mag_niet_door(naam: str) -> None:
    data = copy.deepcopy(_load(naam))
    _catalogus_menupaden(data)
    data["stappen"][0]["uitvoeren_nl"] = data["stappen"][0]["uitvoeren_nl"].replace("stop dan hier", "ga door")
    with pytest.raises(AssertionError):
        _catalogus_menupaden(data)


@pytest.mark.parametrize("naam", ("I03_MEDIAGUARD.checklist.yaml", "I04_MEDIA_SOURCE_OPTIMIZER.checklist.yaml"))
def test_eigen_images_gebruiken_standaard_custom_app(naam: str) -> None:
    data = _load(naam)
    assert "Custom App" in data["stappen"][1]["uitvoeren_nl"]
    assert "Edit → Image" in data["stappen"][0]["handmatig_ui_pad_nl"]
    assert "catalogusinstallatie" not in data["stappen"][0]["uitvoeren_nl"]


@pytest.mark.parametrize("naam", CATALOGUSLIJSTEN)
@pytest.mark.parametrize(("index", "mutatie"), [
    (0, "Bij een catalogusapp lees je de image, tag en digest via Edit → Image"),
    (5, "Catalogusapp: Edit → Image → Update"),
])
def test_catalogus_uitvoertekst_image_pad_wordt_geweigerd(naam: str, index: int, mutatie: str) -> None:
    data = copy.deepcopy(_load(naam))
    _catalogus_menupaden(data)
    tekst = data["stappen"][index]["uitvoeren_nl"]
    begin = "Bij een catalogusapp" if index == 0 else "Catalogusapp:"
    eind = ";" if index == 0 else "; kies"
    voor, scheiding, rest = tekst.partition(begin)
    assert scheiding and eind in rest
    data["stappen"][index]["uitvoeren_nl"] = voor + mutatie + eind + rest.split(eind, 1)[1]
    with pytest.raises(AssertionError):
        _catalogus_menupaden(data)


# Engelse applabels, zelfstandig nagelezen op deze vaste bronstanden (geen live acceptatie).
# I18: src/webui/www/private/{index.html,views/preferencesToolbar.html,views/preferences.html}.
# I19: src/components/{Settings/{SettingsLayout.tsx,SettingsServices.tsx,SettingsAbout/index.tsx,
# SonarrModal/index.tsx,RadarrModal/index.tsx},Layout/Sidebar/index.tsx}; src/i18n/locale/en.json.
# I20: frontend/src/{Router/index.tsx,pages/Settings/Languages/Layout.tsx,
# pages/System/{Status/index.tsx,Backups/index.tsx,Backups/table.tsx}}.
# Een labelwijziging vraagt opnieuw broncontrole en aanpassing van deze tabel.
APPLABELS = {
    "I18": ("9213075dd5f7b352208361c6a84c1c115efc898e", {
        0: ("Help → About", "Tools → Options... → Downloads → Default Save Path:", "BitTorrent → Seeding Limits"),
        1: ("Tools → Options... → BitTorrent → Seeding Limits",),
        2: ("Tools → Options... → Downloads → Excluded file names",),
        3: ("Tools → Options... → Advanced → Network interface:", "Optional IP address to bind to:"),
        4: ("Tools → Options... → BitTorrent → Seeding Limits",),
    }),
    "I19": ("564bfb08b54caa374bddaf820abfd226e5e69151", {
        0: ("Settings → About → Version", "Data Directory", "Settings → Services",
            "Radarr Settings / Sonarr Settings → de server → Edit", "Hostname or IP Address", "API Key",
            "Cancel", "Save Changes"),
        1: ("Settings → Services → Sonarr Settings → de server → Edit (Edit Sonarr Server)",
            "Anime Series Type", "Anime Quality Profile", "Anime Root Folder", "Cancel", "Save Changes"),
        2: ("Requests",),
        3: ("Settings → About → Data Directory",),
        4: ("Requests",),
    }),
    "I20": ("8c68f16a1e12f8e7985baeb73fb4199b5149108a", {
        0: ("System → Status → Bazarr Version", "Database Engine", "Settings → Languages → Selection",
            "Settings → Languages → Profiles", "Settings → Providers"),
        1: ("System → Providers",),
        3: ("System → Status → Database Engine", "System → Backups → Backup Now", "Name",
            "System → Backups → Restore → Restore Backup → Restore", "Cancel"),
    }),
}


def _applabels(data: dict[str, Any]) -> None:
    bronstand, stappen = APPLABELS[data["id"]]
    assert bronstand in data["toelichting_nl"]
    for index, labels in stappen.items():
        for label in labels:
            assert label in data["stappen"][index]["uitvoeren_nl"], (data["id"], index, label, bronstand)


@pytest.mark.parametrize("naam", ["I18_QBITTORRENT", "I19_SEERR", "I20_BAZARR"])
def test_applabels_en_bronstand_worden_per_voorkomen_bewaakt(naam: str) -> None:
    origineel = _load(naam + ".checklist.yaml")
    _applabels(origineel)
    bronstand, stappen = APPLABELS[origineel["id"]]
    for index, labels in stappen.items():
        for label in labels:
            data = copy.deepcopy(origineel)
            stap = data["stappen"][index]
            stap["uitvoeren_nl"] = stap["uitvoeren_nl"].replace(label, "onbewezen label")
            with pytest.raises(AssertionError):
                _applabels(data)
    data = copy.deepcopy(origineel)
    data["toelichting_nl"] = data["toelichting_nl"].replace(bronstand, "onbewezen bronstand")
    with pytest.raises(AssertionError):
        _applabels(data)


I18_GATES = [
    (0, "Zijn die niet aantoonbaar, stop dan hier"),
    (0, "Help → About"),
    (0, "geen torrent verplaatsen of verwijderen"),
    (1, "alleen de controle"),
    (1, "private-trackerverplichtingen"),
    (2, "Downloads → Excluded file names"),
    (2, "geen bestaande torrent"),
    (3, "Advanced → Network interface:"),
    (3, "Optional IP address to bind to:"),
    (3, "stop dan hier"),
    (4, "integrity.never_rewrite_seeding"),
    (4, "payload blijft intact"),
    (5, "ontbreekt een aangetoonde veilige overdracht, stop dan hier"),
    (5, "nooit de bestaande downloadclient stoppen"),
    (5, "vorige image én passende configuratie, torrentstatus en payload"),
]


def _i18_veiligheid(data: dict[str, Any]) -> None:
    _applabels(data)
    _bestaande_app_basis(data, set(), set())
    _catalogus_menupaden(data)
    for index, tekst in I18_GATES:
        assert tekst in data["stappen"][index]["uitvoeren_nl"], tekst
    for tekst in ("Emby blijft werken", "MediaGuard-voortgang", "geen interface- of adreswaarden"):
        assert tekst in data["toelichting_nl"], tekst


def test_i18_letterlijke_taken_en_downloadbescherming() -> None:
    _i18_veiligheid(_load("I18_QBITTORRENT.checklist.yaml"))


@pytest.mark.parametrize(("index", "tekst"), I18_GATES)
def test_i18_veiligheidsmutatie_wordt_geweigerd(index: int, tekst: str) -> None:
    data = copy.deepcopy(_load("I18_QBITTORRENT.checklist.yaml"))
    _i18_veiligheid(data)
    data["stappen"][index]["uitvoeren_nl"] = data["stappen"][index]["uitvoeren_nl"].replace(tekst, "ga toch verder")
    with pytest.raises(AssertionError):
        _i18_veiligheid(data)


@pytest.mark.parametrize("index", range(6))
def test_i18_stopzin_wordt_bewaakt(index: int) -> None:
    data = copy.deepcopy(_load("I18_QBITTORRENT.checklist.yaml"))
    _i18_veiligheid(data)
    data["stappen"][index]["bij_fout_nl"] = "Ga verder; negeer de fout."
    with pytest.raises(AssertionError):
        _i18_veiligheid(data)


def test_i18_eentekenmutatie_acceptatie_wordt_geweigerd(tmp_path: Path) -> None:
    def wijzig(data: dict[str, Any]) -> None:
        stap = data["stappen"][0]
        stap["acceptatie_nl"] = "X" + stap["acceptatie_nl"][1:]

    naam = "I18_QBITTORRENT.checklist.yaml"
    _kopie(tmp_path, naam, wijzig=wijzig)
    assert any("acceptatie ≠ Excel" in f for f in cl.controleer_alle(tmp_path)[naam])
    assert cl.main(tmp_path) == 1


I19_GATES = [
    (0, "Zijn die niet aantoonbaar, stop dan hier"),
    (0, "Settings → Services"),
    (0, "Seerr"),
    (0, "vier forks"),
    (0, "Radarr Settings / Sonarr Settings → de server → Edit"),
    (0, "Controleer bij elke server: Hostname or IP Address moet het LAN-adres van de NAS zijn, "
        "nooit localhost of een loopbackadres (een adres dat met 127 begint: dat is de container zelf, "
        "niet de NAS)"),
    (0, "Is het wel loopback: stop hier, meld het, wijzig niets"),
    (0, "sluit met Cancel, niet Save Changes"),
    (0, "Openen start een verbindingstest naar de fork"),
    (1, "Sonarr Settings → de server → Edit (Edit Sonarr Server)"),
    (1, "Openen start een verbindingstest naar Sonarr"),
    (1, "sluit met Cancel, niet Save Changes"),
    (0, "bestaande Emby-koppeling"),
    (1, "eigenaar MediaGuard"),
    (1, "uitsluitend geïsoleerd"),
    (2, "Requests"),
    (2, "geen bestaande aanvraag of media verwijderen"),
    (3, "settings.json"),
    (3, "SQLite of PostgreSQL"),
    (3, "ontbreekt een aangetoonde veilige overdracht, stop dan hier"),
    (3, "bestaande Seerr niet stoppen"),
    (4, "Hub toont aanvragen alleen"),
    (4, "geen tweede aanvraagmotor"),
    (5, "Alleen na het volledige herstelbewijs van I19-19-04"),
    (5, "vorige image én passende database en configuratie"),
]


def _i19_veiligheid(data: dict[str, Any]) -> None:
    _applabels(data)
    _bestaande_app_basis(data, {5}, set())
    _catalogus_menupaden(data)
    for index, tekst in I19_GATES:
        assert tekst in data["stappen"][index]["uitvoeren_nl"], tekst
    for tekst in ("Emby blijft werken", "MediaGuard-voortgang", "geen Overseerr- of Jellyseerr-migratie"):
        assert tekst in data["toelichting_nl"], tekst


def test_i19_letterlijke_taken_en_aanvraagbescherming() -> None:
    _i19_veiligheid(_load("I19_SEERR.checklist.yaml"))


@pytest.mark.parametrize(("index", "tekst"), I19_GATES)
def test_i19_veiligheidsmutatie_wordt_geweigerd(index: int, tekst: str) -> None:
    data = copy.deepcopy(_load("I19_SEERR.checklist.yaml"))
    _i19_veiligheid(data)
    data["stappen"][index]["uitvoeren_nl"] = data["stappen"][index]["uitvoeren_nl"].replace(tekst, "ga toch verder")
    with pytest.raises(AssertionError):
        _i19_veiligheid(data)


@pytest.mark.parametrize("index", range(6))
def test_i19_stopzin_wordt_bewaakt(index: int) -> None:
    data = copy.deepcopy(_load("I19_SEERR.checklist.yaml"))
    _i19_veiligheid(data)
    data["stappen"][index]["bij_fout_nl"] = "Ga verder; negeer de fout."
    with pytest.raises(AssertionError):
        _i19_veiligheid(data)


def test_i19_eentekenmutatie_acceptatie_wordt_geweigerd(tmp_path: Path) -> None:
    def wijzig(data: dict[str, Any]) -> None:
        stap = data["stappen"][0]
        stap["acceptatie_nl"] = "X" + stap["acceptatie_nl"][1:]

    naam = "I19_SEERR.checklist.yaml"
    _kopie(tmp_path, naam, wijzig=wijzig)
    assert any("acceptatie ≠ Excel" in f for f in cl.controleer_alle(tmp_path)[naam])
    assert cl.main(tmp_path) == 1


I20_GATES = [
    (0, "Zijn die niet aantoonbaar, stop dan hier"),
    (0, "System → Status → Bazarr Version"),
    (0, "Settings → Languages → Profiles"),
    (0, "Settings → Providers"),
    (1, "geen tweede ondertitelzoeker"),
    (1, "geen bestaande ondertitels overschrijven"),
    (2, "SRT/ASS"),
    (2, "anime"),
    (2, "multi-audio"),
    (2, "DV"),
    (2, "echte media en spelers"),
    (2, "kijkgeschiedenis"),
    (3, "System → Backups → Backup Now"),
    (3, "Database Engine"),
    (3, "PostgreSQL"),
    (3, "ontbreekt een aangetoonde veilige overdracht, stop dan hier"),
    (3, "Restore Backup"),
    (3, "nooit op de bestaande Bazarr"),
    (4, "Bazarr blijft tot pariteit is aangetoond"),
    (4, "het platform Bazarr nooit zelf verwijdert"),
    (4, "besluit van de gebruiker"),
    (4, "terugvalweg"),
]


def _i20_veiligheid(data: dict[str, Any]) -> None:
    _applabels(data)
    _bestaande_app_basis(data, set(), set(), aantal=5)
    _catalogus_menupaden(data, update_index=None)
    for index, tekst in I20_GATES:
        assert tekst in data["stappen"][index]["uitvoeren_nl"], tekst
    for index, stap in enumerate(data["stappen"]):
        assert ("NIET GEVALIDEERD (hardware)" in stap["validatie_nu"]) == (index == 2)
    for tekst in ("Emby blijft werken", "MediaGuard-voortgang"):
        assert tekst in data["toelichting_nl"], tekst


def test_i20_letterlijke_taken_en_ondertitelbescherming() -> None:
    _i20_veiligheid(_load("I20_BAZARR.checklist.yaml"))


@pytest.mark.parametrize(("index", "tekst"), I20_GATES)
def test_i20_veiligheidsmutatie_wordt_geweigerd(index: int, tekst: str) -> None:
    data = copy.deepcopy(_load("I20_BAZARR.checklist.yaml"))
    _i20_veiligheid(data)
    data["stappen"][index]["uitvoeren_nl"] = data["stappen"][index]["uitvoeren_nl"].replace(tekst, "ga toch verder")
    with pytest.raises(AssertionError):
        _i20_veiligheid(data)


@pytest.mark.parametrize("index", range(5))
def test_i20_stopzin_wordt_bewaakt(index: int) -> None:
    data = copy.deepcopy(_load("I20_BAZARR.checklist.yaml"))
    _i20_veiligheid(data)
    data["stappen"][index]["bij_fout_nl"] = "Ga verder; negeer de fout."
    with pytest.raises(AssertionError):
        _i20_veiligheid(data)


def test_i20_eentekenmutatie_acceptatie_wordt_geweigerd(tmp_path: Path) -> None:
    def wijzig(data: dict[str, Any]) -> None:
        stap = data["stappen"][0]
        stap["acceptatie_nl"] = "X" + stap["acceptatie_nl"][1:]

    naam = "I20_BAZARR.checklist.yaml"
    _kopie(tmp_path, naam, wijzig=wijzig)
    assert any("acceptatie ≠ Excel" in f for f in cl.controleer_alle(tmp_path)[naam])
    assert cl.main(tmp_path) == 1


def test_i20_zes_stappen_worden_geweigerd(tmp_path: Path) -> None:
    def wijzig(data: dict[str, Any]) -> None:
        zesde = copy.deepcopy(data["stappen"][-1])
        zesde["id"] = "I20-20-06"
        data["stappen"].append(zesde)
        assert len(data["stappen"]) == 6

    naam = "I20_BAZARR.checklist.yaml"
    _kopie(tmp_path, naam, wijzig=wijzig)
    assert any("dekken de Excel-taken niet exact" in f for f in cl.controleer_alle(tmp_path)[naam])
    assert cl.main(tmp_path) == 1


@pytest.mark.parametrize("veld", ("uitvoeren_nl", "handmatig_ui_pad_nl"))
def test_i20_catalogus_image_pad_wordt_geweigerd(veld: str) -> None:
    data = copy.deepcopy(_load("I20_BAZARR.checklist.yaml"))
    _i20_veiligheid(data)
    data["stappen"][0][veld] += "; catalogusapp: Edit → Image"
    with pytest.raises(AssertionError):
        _i20_veiligheid(data)


def test_i18_i19_i20_bewijs_claimt_geen_functionele_acceptatie() -> None:
    rows = {r["id"]: r for r in json.loads((cl.DOCS / "EVIDENCE.json").read_text())}
    ids = [t["ID"] for doc, taken in cl.excel_taken().items()
           if Path(doc).name.startswith(("I18_", "I19_", "I20_")) for t in taken]
    assert len(ids) == 17 and set(ids) <= rows.keys()
    for sid in ids:
        r = rows[sid]
        assert r["status"] == "Bezig"
        assert "Unit-getest" in r["validatie"] and "Sim-getest" not in r["validatie"]
        assert "NIET GEVALIDEERD (echte NAS)" in r["validatie"]
        assert ("NIET GEVALIDEERD (echte Hub)" in r["validatie"]) == (sid == "I19-19-05")
        assert ("NIET GEVALIDEERD (hardware)" in r["validatie"]) == (sid in {"I20-20-03", "I20-20-05"})
        # G-19: elke I18/I19/I20-proef op de NAS vraagt een aanmelding, behalve het archiefherstel I20-20-04.
        assert ("NIET GEVALIDEERD (account)" in r["validatie"]) == (sid != "I20-20-04")
        assert "alleen NAS-test nodig" not in r["opmerking"]
        assert r["blokkade"] and "geen functionele" in r["opmerking"]


# Herstel S-P3-430: deze proeven moeten ook op de oude controlefuncties rood worden.
@pytest.mark.parametrize(("index", "tekst"), [
    (0, "Controleer bij elke server: Hostname or IP Address moet het LAN-adres van de NAS zijn, "
        "nooit localhost of een loopbackadres (een adres dat met 127 begint: dat is de container zelf, "
        "niet de NAS)"),
    (0, "Is het wel loopback: stop hier, meld het, wijzig niets"),
    (0, "Radarr Settings / Sonarr Settings → de server → Edit"),
    (0, "sluit met Cancel, niet Save Changes"),
    (1, "Sonarr Settings → de server → Edit (Edit Sonarr Server)"),
    (1, "Openen start een verbindingstest naar Sonarr"),
    (1, "sluit met Cancel, niet Save Changes"),
])
def test_seerr_herstel_verplichte_leescontrole(index: int, tekst: str) -> None:
    assert tekst in _load("I19_SEERR.checklist.yaml")["stappen"][index]["uitvoeren_nl"]


@pytest.mark.parametrize("naam", [*CATALOGUSLIJSTEN, "I20_BAZARR.checklist.yaml"])
@pytest.mark.parametrize("index", (0, 1))
@pytest.mark.parametrize("woord", ("catalogusapp", "CatalogusApp", "CATALOGUSAPP"))
def test_catalogus_elke_zin_met_image_pad_wordt_geweigerd(naam: str, index: int, woord: str) -> None:
    data = copy.deepcopy(_load(naam))
    update = None if data["id"] == "I20" else 5
    _catalogus_menupaden(data, update)
    data["stappen"][index]["uitvoeren_nl"] += f" Lees voor de {woord} de image via Edit → Image."
    with pytest.raises(AssertionError):
        _catalogus_menupaden(data, update)


@pytest.mark.parametrize(("naam", "controle"), [
    ("I15_IMMICH", _i15_veiligheid),
    ("I16_PROWLARR", _i16_veiligheid),
    ("I17_SABNZBD", _i17_veiligheid),
    ("I18_QBITTORRENT", _i18_veiligheid),
    ("I19_SEERR", _i19_veiligheid),
    ("I20_BAZARR", _i20_veiligheid),
])
def test_inventarisstopzin_wordt_in_alle_zes_lijsten_bewaakt(naam: str, controle: Any) -> None:
    data = copy.deepcopy(_load(naam + ".checklist.yaml"))
    controle(data)
    stap = data["stappen"][0]
    stap["uitvoeren_nl"] = stap["uitvoeren_nl"].replace("Zijn die niet aantoonbaar, stop dan hier", "Ga verder")
    with pytest.raises(AssertionError):
        controle(data)


@pytest.mark.parametrize(("naam", "oud", "nieuw", "controle"), [
    ("I18_QBITTORRENT.checklist.yaml", "Zijn die niet aantoonbaar, stop dan hier.",
     "Lees voor de catalogusapp de image, tag en digest via Edit → Image.", _i18_veiligheid),
    ("I18_QBITTORRENT.checklist.yaml", "BitTorrent → Seeding Limits", "BitTorrent → Share Limits", _i18_veiligheid),
    ("I19_SEERR.checklist.yaml", "Anime Root Folder", "Anime Root Path", _i19_veiligheid),
])
def test_reviewer_mutaties_m1_m2_m3_worden_geweigerd(naam: str, oud: str, nieuw: str, controle: Any) -> None:
    data = copy.deepcopy(_load(naam))
    controle(data)
    assert any(oud in stap["uitvoeren_nl"] for stap in data["stappen"])
    for stap in data["stappen"]:
        stap["uitvoeren_nl"] = stap["uitvoeren_nl"].replace(oud, nieuw)
    with pytest.raises(AssertionError):
        controle(data)


@pytest.mark.parametrize("naam", [*CATALOGUSLIJSTEN, "I20_BAZARR.checklist.yaml"])
def test_image_digest_bron_en_custom_app_stop(naam: str) -> None:
    data = _load(naam)
    app = {"I15": "immich", "I16": "prowlarr", "I17": "sabnzbd", "I18": "qbittorrent",
           "I19": "seerr", "I20": "bazarr"}[data["id"]]
    assert f"ix-dev/community/{app}/ix_values.yaml (images en digests)" in data["toelichting_nl"]
    tekst = data["stappen"][0]["uitvoeren_nl"]
    assert "Edit → Image Configuration → Image: Repository en Tag" in tekst
    assert "staat er geen @sha256-digest in Tag, stop dan hier" in tekst


@pytest.mark.parametrize(("index", "veld", "voorvoegsel"), [
    (0, "uitvoeren_nl", "niet "),
    (0, "uitvoeren_nl", "gebruik "),
    (0, "bij_fout_nl", "niet "),
    (1, "uitvoeren_nl", "niet "),
])
def test_loopbackverbod_is_geen_toestemming_voor_adreswaarden(index: int, veld: str, voorvoegsel: str) -> None:
    data = copy.deepcopy(_load("I19_SEERR.checklist.yaml"))
    assert cl.controleer_lijst(data, cl.excel_taken()) == []
    adres = ".".join(("127", "0", "0", "1"))
    verbod = f"{voorvoegsel}{adres} of localhost (loopback van de container)"
    if index == 0 and veld == "uitvoeren_nl":
        # Vervang de zin zodat de proef niet alleen de schemalimiet voor tekstlengte raakt.
        data["stappen"][index][veld] = verbod
    else:
        data["stappen"][index][veld] += " " + verbod
    assert any("voorbeeldwaarde" in fout for fout in cl.controleer_lijst(data, cl.excel_taken()))


@pytest.mark.parametrize("naam", [*CATALOGUSLIJSTEN, "I20_BAZARR.checklist.yaml"])
def test_zes_lijsten_bevatten_geen_letterlijk_ipv4_adres(naam: str) -> None:
    tekst = (cl.CHECKLIST_DIR / naam).read_text(encoding="utf-8")
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", tekst), naam


# S-P3-440: I05 my_house (Z-18 optie 2: hernoemde titels; installatie via het formulier van de standaard Custom App).
I05 = "I05_MY_HOUSE.checklist.yaml"
I05_GEEN_YAML = ("Het document noemt YAML. De vaste installatieweg is de standaard Custom App met een alles-in-een "
                 "image; YAML wordt niet gebruikt.")
I05_GATES = [
    (0, "stop dan hier"),
    (0, "BLOK-06"),
    (0, "geen script"),
    (1, "deploy/truenas/CUSTOM_APP.md"),
    (1, "laat Devices leeg"),
    (1, "precies die ene opslag en die ene poort"),
    (2, "stop dan hier en neem de app niet in gebruik"),
    (2, "aantoonbaar geslaagde ingebouwde healthcheck"),
    (2, "geen losse of zelfbedachte image"),
    (3, "leestest"),
    (3, "Als getest markeren"),
    (3, "alleen terwijl de gebruiker erbij staat"),
    (4, "geen schakelhandeling"),
    (4, "meld een veiligheidsfout"),
    (4, "nooit nul"),
    (5, "alleen uitlezen"),
    (5, "als getest te boek staat (D12)"),
    (5, "nooit bediend"),
    (6, "Alleen als D13"),
    (6, "apparaat-doorgifte in het formulier"),
    (6, "Geen opdracht in een terminal"),
    (6, "stop dan hier"),
    (7, "stop dan hier"),
    (7, "Systeem › Herstellen en verhuizen"),
    (7, "nooit over de bestaande installatie"),
    (7, "geen opdracht in een terminal"),
]


def _i05_veiligheid(data: dict[str, Any]) -> None:
    assert data["aanpak"] == "inrichten_en_testen" and "inrichten_en_testen" in data["toelichting_nl"]
    assert data["install"] == {"method": "none"}
    assert data["installatie"] == "geen" and data["mutaties_door_platform"] is False
    assert len(data["stappen"]) == 8
    assert cl.controleer_lijst(data, cl.excel_taken()) == []
    for tekst in ("Z-18: optie 2", "BLOK-06", "N-P6-06", "alleen gemeten en nooit geschakeld"):
        assert tekst in data["toelichting_nl"], tekst
    for index, stap in enumerate(data["stappen"]):
        waarden = set(stap["validatie_nu"])
        assert not stap.get("truenas_methoden") and "Sim-getest" not in waarden
        assert "NIET GEVALIDEERD (echte NAS)" in waarden
        assert ("NIET GEVALIDEERD (hardware)" in waarden) == (index >= 3), stap["id"]
        assert not waarden & {"NIET GEVALIDEERD (account)", "NIET GEVALIDEERD (echte Hub)"}
        assert stap["blokkeert_volgende"] is True
        assert stap["bij_fout_nl"].startswith("Stop hier;"), stap["id"]
    for index in (0, 1, 2):
        assert "install/manifests/my_house.manifest.yaml" in data["stappen"][index]["verwijzing"]
    for index in (1, 2):
        assert data["stappen"][index]["afwijking_van_document_nl"].startswith(I05_GEEN_YAML)
    assert "geen compose-profiel" in data["stappen"][6]["afwijking_van_document_nl"]
    for index, tekst in I05_GATES:
        assert tekst in data["stappen"][index]["uitvoeren_nl"], (index, tekst)


def test_i05_letterlijke_taken_en_installatieweg() -> None:
    _i05_veiligheid(_load(I05))


@pytest.mark.parametrize(("index", "tekst"), I05_GATES)
def test_i05_veiligheidsmutatie_wordt_geweigerd(index: int, tekst: str) -> None:
    data = copy.deepcopy(_load(I05))
    _i05_veiligheid(data)
    data["stappen"][index]["uitvoeren_nl"] = data["stappen"][index]["uitvoeren_nl"].replace(tekst, "ga toch verder")
    with pytest.raises(AssertionError):
        _i05_veiligheid(data)


@pytest.mark.parametrize("index", range(8))
def test_i05_stopzin_wordt_bewaakt(index: int) -> None:
    data = copy.deepcopy(_load(I05))
    data["stappen"][index]["bij_fout_nl"] = "Ga verder; negeer de fout."
    with pytest.raises(AssertionError):
        _i05_veiligheid(data)


@pytest.mark.parametrize("index", range(8))
def test_i05_hardwaregrens_wordt_bewaakt(index: int) -> None:
    data = copy.deepcopy(_load(I05))
    waarden = data["stappen"][index]["validatie_nu"]
    if "NIET GEVALIDEERD (hardware)" in waarden:
        waarden.remove("NIET GEVALIDEERD (hardware)")
    else:
        waarden.append("NIET GEVALIDEERD (hardware)")
    with pytest.raises(AssertionError):
        _i05_veiligheid(data)


def test_i05_yaml_alleen_in_afwijking_en_excel_acceptatie() -> None:
    """Negatief (a) van de stap: YAML of compose staat alleen in afwijking_van_document_nl, in de letterlijke
    Excel-acceptatie van I05-05-02 en in de verplichte verwijzing naar de manifestkopie; nooit als aanwijzing."""
    toegestaan = ("afwijking_van_document_nl: ", "acceptatie_nl: YAML valide",
                  "verwijzing: [install/manifests/my_house.manifest.yaml")
    regels = [r.strip() for r in (cl.CHECKLIST_DIR / I05).read_text(encoding="utf-8").splitlines()
              if re.search(r"compose|yaml", r, re.I)]
    assert regels and all(r.startswith(toegestaan) for r in regels), regels
    assert sum(r.startswith("afwijking_van_document_nl: ") for r in regels) == 4
    data = _load(I05)
    for index in (1, 2):
        assert "YAML" not in data["stappen"][index]["titel_nl"]  # Z-18 optie 2
    data["stappen"][1]["uitvoeren_nl"] += " Plak daarna de YAML in Install via YAML."
    assert any("YAML-, compose- of scriptweg in uitvoeren_nl" in f for f in cl.controleer_lijst(data, cl.excel_taken()))


@pytest.mark.parametrize("titel", ["Formulier standaard Custom App zonder Zigbee of tunnel",
                                   "Eerste YAML zonder Zigbee/tunnel"])
def test_i05_verbeterde_titel_wordt_geweigerd(tmp_path: Path, titel: str) -> None:
    """Negatief (b): een 'verbeterde' of de oude titel van stap 2 faalt in tools/checklists.py."""
    _kopie(tmp_path, I05, wijzig=lambda d: d["stappen"][1].update(titel_nl=titel))
    assert any("I05-05-02: titel ≠ Excel-taak" in f for f in cl.controleer_alle(tmp_path)[I05])
    assert cl.main(tmp_path) == 1


def _i05_bewijs(rijen: list[dict[str, Any]]) -> None:
    geselecteerd = [r for r in rijen if r["id"].startswith("I05-05-")]
    assert [r["id"] for r in geselecteerd] == [f"I05-05-0{n}" for n in range(1, 9)]
    for nummer, rij in enumerate(geselecteerd, 1):
        assert rij["status"] == "Geblokkeerd", rij["id"]
        waarden = rij["validatie"].split("; ")
        assert "Unit-getest" in waarden and "Sim-getest" not in waarden and "NIET GEVALIDEERD (echte NAS)" in waarden
        assert ("NIET GEVALIDEERD (hardware)" in waarden) == (nummer >= 4), rij["id"]
        verwacht = ("NIET GEVALIDEERD (echte NAS): controlelijst geschreven; installeren via het formulier door de "
                    "gebruiker (STAPPEN_P6.md onderdeel N-P6-06)" if nummer <= 3 else
                    "NIET GEVALIDEERD (hardware): echte Hue, HomeWizard, Gree, Zigbee en telefoon (N-P6-06)")
        assert rij["blokkade"].startswith(verwacht), rij["id"]
        assert "BLOK-06" in rij["blokkade"] and "N-P6-06" in rij["bewijs"]
        assert "alleen nas-test nodig" not in rij["opmerking"].lower(), rij["id"]


def test_i05_bewijs_blijft_geblokkeerd() -> None:
    _i05_bewijs(json.loads((cl.DOCS / "EVIDENCE.json").read_text(encoding="utf-8")))


@pytest.mark.parametrize(("sid", "veld", "waarde"), [
    ("I05-05-01", "status", "Bezig"),
    ("I05-05-05", "validatie", "Unit-getest; NIET GEVALIDEERD (echte NAS)"),
    ("I05-05-02", "opmerking", "alleen NAS-test nodig"),
    ("I05-05-03", "blokkade", "Wacht op de gebruiker."),
])
def test_i05_te_ruim_bewijs_wordt_geweigerd(sid: str, veld: str, waarde: str) -> None:
    rijen = json.loads((cl.DOCS / "EVIDENCE.json").read_text(encoding="utf-8"))
    next(r for r in rijen if r["id"] == sid)[veld] = waarde
    with pytest.raises(AssertionError):
        _i05_bewijs(rijen)
