"""Controlelijsten (eigen formaat checklist v1): I01/I02 (ADR-0015) en de bestaande apps I03…I20 (ADR-0027).

Alle tests gelden voor elke lijst in ``install/checklists/`` (``cl.lijsten()``), niet voor vaste bestandsnamen.

- Vorm (JSON Schema), dekking van precies de Excel-taken met titel/acceptatie/risico uit de Excel, alleen-lezen
  TrueNAS-methoden, geen terminal, geen voorbeeldwaarden, eerlijke validatie per stap (``tools/checklists.py``).
- Verzameling: elke lijst hoort bij precies één document onder ``03_INSTALLATIE/`` (bestandsnaam = documentnaam),
  geen document heeft twee lijsten, een document met een installeerbaar manifest in ``install/manifests/`` heeft geen
  lijst, en elk ander bestand in ``install/checklists/`` is een fout (ook ``python tools/checklists.py`` faalt dan).
  De test leest de map zelf, los van ``cl.lijsten()``.
- Tekstregels: de letterlijke Excel-titels en -acceptaties van alle documenten gaan door de terminal- en
  voorbeeldregel; een YAML-, compose- of scriptweg wordt geweigerd in ``uitvoeren_nl``, ``bij_fout_nl`` en
  ``handmatig_ui_pad_nl`` (titel, acceptatie en afwijking mogen de Excel-woorden dragen; Z-18 optie 1).
- Negatieve tests: elke overtreding wordt herkend, in elke lijst; de account-regel voor elke account-stap van elke
  lijst.
- Simulator: elke genoemde methode bestaat in het TrueNAS-profiel waarvoor ze geldt en is aan te roepen met de
  alleen-lezen-key van de Hub (25.04.2.6 en 25.10.8) — en een methode die in een profiel niet bestaat, wordt daar ook
  echt geweigerd (``-32601``), zodat de versie-indeling klopt.
"""

from __future__ import annotations

import copy
import json
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
    """R-MANIFEST-1/-2 en Z-18 optie 1: een lijst per document van ADR-0027 met de letterlijke Excel-titels en
    -acceptaties is groen, ook I03 (chmod/chown in de acceptatie) en I05 ("TrueNAS Install via YAML" als titel, YAML in
    de afwijking); de weg zelf staat in uitvoeren_nl zonder YAML."""

    def i05(d: dict[str, Any]) -> None:
        stap = next(s for s in d["stappen"] if s["id"] == "I05-05-03")
        assert "YAML" in stap["titel_nl"]
        stap["afwijking_van_document_nl"] = ("Het document noemt Install via YAML; de weg is het formulier van de "
                                             "standaard Custom App (Z-18: optie 1, zonder besluit).")

    for doc in BESTAANDE_APPS:
        _neplijst(tmp_path, f"03_INSTALLATIE/{doc}.md", wijzig=i05 if doc.startswith("I05") else None)
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
    ("voorbeeld-IP", lambda d: _stap(d, 3).update(uitvoeren_nl="Verbind met wss://192.168.1.10/api/current."),
     "voorbeeldwaarde"),
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
