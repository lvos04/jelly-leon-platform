"""Peil-kopie (ONTWERPTAAL §13.2/§14, O-14): de versie-gepinde kopie van het statuspaginasjabloon in
``design/ontwerptaal/`` is intact, de dienst serveert precies die kopie, en er is geen drift met de bron in
``lvos04/ai-server-manager`` (gecommitte inhoud, gelezen met ``git show``).

Pin- en driftregel (Peil 1.1.1, E08; ONTWERPTAAL §14): de kopie pint versie én commit-SHA. CI faalt bij (1) een
afwijking van de gepinde inhoud en (2) dezelfde versie met andere inhoud in de bron. Een **nieuwere** bronversie geeft
alleen een waarschuwing: bijwerken is een bewuste stap (``tools/peil_kopie.py bijwerken`` + de eigen tests).

Bron: ``PEIL_BRON`` (git-checkout van ai-server-manager; standaard de naburige map ``../ai-server-manager``) en
``PEIL_BRON_REF`` (standaard ``HEAD``). In CI zet de workflow ``PEIL_BRON_VERPLICHT=1``: dan faalt de test als de bron
ontbreekt in plaats van over te slaan.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import types
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import peil_kopie as pk  # noqa: E402

BRON = Path(os.environ.get("PEIL_BRON") or ROOT.parent / "ai-server-manager")
BRON_REF = os.environ.get("PEIL_BRON_REF", "HEAD")
VERPLICHT = os.environ.get("PEIL_BRON_VERPLICHT") == "1"


def bron_beschikbaar() -> bool:
    ok = (BRON / ".git").exists() and subprocess.run(
        ["git", "-C", str(BRON), "rev-parse", "--verify", f"{BRON_REF}^{{commit}}"], capture_output=True, check=False
    ).returncode == 0
    if not ok and VERPLICHT:
        pytest.fail(f"Peil-bron niet beschikbaar in {BRON} ({BRON_REF}); CI moet ai-server-manager ophalen")
    if not ok:
        pytest.skip(f"Peil-bron niet naast deze repo ({BRON}); in CI verplicht")
    return ok


def test_kopie_is_intact_and_is_what_the_service_serves() -> None:
    from lifecycle.peil import PEIL_VERSIE

    herkomst = pk.controleer_kopie()
    assert {h.versie for h in herkomst.values()} == {PEIL_VERSIE}
    assert len({h.commit for h in herkomst.values()}) == 1


@pytest.mark.parametrize("schade", ["inhoud", "kop", "runtime", "css-in-html"])
def test_tampering_with_the_copy_is_detected(tmp_path: Path, schade: str) -> None:
    kopie = tmp_path / "kopie"
    shutil.copytree(pk.KOPIE_DIR, kopie, ignore=shutil.ignore_patterns("*.md"))
    runtime = tmp_path / "statuspagina.html"
    shutil.copy(pk.RUNTIME_HTML, runtime)
    pk.controleer_kopie(kopie, runtime)  # onbeschadigd: in orde
    html = kopie / "statuspagina.html"
    if schade == "inhoud":
        html.write_text(html.read_text(encoding="utf-8").replace("Koppelen met de Hub", "Koppel nu"), encoding="utf-8")
    elif schade == "kop":
        html.write_text(html.read_text(encoding="utf-8").split("\n", 2)[2], encoding="utf-8")
    elif schade == "runtime":
        runtime.write_text(runtime.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    else:  # beide bestanden consistent gepind, maar de CSS in het sjabloon wijkt af van statuspagina.css
        css = kopie / "statuspagina.css"
        h, rest = pk.splits("statuspagina.css", css.read_bytes())
        nieuw = rest.replace(b"--primary:#123DA3", b"--primary:#FF0000", 1)
        css.write_bytes(pk.maak_kop("statuspagina.css", h.versie, h.commit, nieuw) + nieuw)
    with pytest.raises(pk.KopieFout):
        pk.controleer_kopie(kopie, runtime)


def test_pinned_sha256_is_the_source_at_the_pinned_commit() -> None:
    """De herkomstkop klopt: het bronbestand op de gepinde commit heeft precies de gepinde sha256."""
    bron_beschikbaar()
    for naam, h in pk.controleer_kopie().items():
        assert pk.sha256(pk.git_show(BRON, h.commit, h.pad)) == h.sha256, naam


def drift(naam: str, h: pk.Herkomst, actueel: bytes) -> tuple[str, str] | None:
    """E08: ``("fout", …)`` bij dezelfde versie met andere inhoud, ``("waarschuwing", …)`` bij een nieuwere versie."""
    if pk.sha256(actueel) == h.sha256:
        return None
    versie = pk.bron_versie(naam, actueel)
    if versie == h.versie:
        return ("fout", f"Drift: {h.pad} in ai-server-manager ({BRON_REF}) heeft andere inhoud onder dezelfde versie "
                        f"Peil {versie} dan de gepinde kopie (@ {h.commit[:12]}). Dezelfde versie hoort één inhoud "
                        f"te hebben; neem de kopie opnieuw over met 'python tools/peil_kopie.py bijwerken'.")
    return ("waarschuwing", f"{h.pad}: de bron is Peil {versie} (gepind: {h.versie} @ {h.commit[:12]}). Overstappen "
                            f"is een bewuste stap: 'python tools/peil_kopie.py bijwerken', statuspagina en tests "
                            f"aanpassen.")


def test_drift_rule_same_version_fails_newer_version_warns() -> None:
    herkomst = pk.controleer_kopie()
    h = herkomst["statuspagina.html"]
    _kop, rest = pk.splits("statuspagina.html", (pk.KOPIE_DIR / "statuspagina.html").read_bytes())
    assert drift("statuspagina.html", h, rest) is None
    stil = rest.replace(b"Koppelen met de Hub", b"Koppel nu", 1)
    assert drift("statuspagina.html", h, stil) is not None and drift("statuspagina.html", h, stil)[0] == "fout"
    nieuwer = stil.replace(f"statuspagina-sjabloon {h.versie}".encode(), b"statuspagina-sjabloon 9.9.9", 1)
    uitkomst = drift("statuspagina.html", h, nieuwer)
    assert uitkomst is not None and uitkomst[0] == "waarschuwing"


def test_no_drift_with_the_current_source() -> None:
    """ONTWERPTAAL §14 (E08): zelfde versie, andere inhoud in de bron = rood; een nieuwere versie = waarschuwing."""
    bron_beschikbaar()
    for naam, h in pk.controleer_kopie().items():
        uitkomst = drift(naam, h, pk.git_show(BRON, BRON_REF, h.pad))
        if uitkomst is None:
            continue
        soort, melding = uitkomst
        if soort == "fout":
            pytest.fail(melding)
        warnings.warn(melding, UserWarning, stacklevel=1)


def _referentie_renderer(commit: str) -> types.ModuleType:
    """``bouw/sjabloon.py`` van de bron op de gepinde commit (alleen gelezen; draait in een eigen naamruimte)."""
    code = pk.git_show(BRON, commit, f"{pk.BRON_DIR}/bouw/sjabloon.py").decode("utf-8")
    mod = types.ModuleType("peil_referentie_sjabloon")
    exec(compile(code, "bouw/sjabloon.py", "exec"), mod.__dict__)  # noqa: S102 - eigen platformcode, alleen in tests
    return mod


def _gegevens() -> list[dict[str, object]]:
    from lifecycle.peil import TONEN

    vast: dict[str, object] = {
        "voorbeeld": "", "dienst_naam": "Dienst & co", "dienst_omschrijving": "Omschrijving <b>", "titel_toestand": "t",
        "spring_doel": "inhoud", "spring_tekst": "Naar inhoud", "melding": "", "totaal_toon": "onbekend",
        "totaal_label": "Niet gereed", "totaal_toelichting": "Deels onbekend — wacht op koppeling.",
        "checks": [{"naam": f"Rij {t}", "toon": t, "label": t.title(), "iso": "2026-11-10T08:00:00Z",
                    "tijd": "08:00 UTC", "reden": "reden '1'", "reden_code": "code_x"} for t in sorted(TONEN)],
        "gereed": "Nee — wacht op koppeling", "versie": "0.1.0", "bijgewerkt": "10 nov 2026, 08:00 UTC",
        "bijgewerkt_iso": "2026-11-10T08:00:00Z", "koppelcode_geldig": "60 minuten", "csrf_token": "{{csrf_token}}",
        "fout_hub_adres": "", "fout_koppelcode": "", "fout_vingerafdruk": "",
        "dienst_blok_titel": "Wat deze versie doet",
        "dienst_blok": [{"naam": "P2", "toon": "nog-niet", "label": "Nog niet beschikbaar", "reden": "Volgt in P2."}],
    }
    return [
        {**vast, "koppelformulier": "1", "fout_koppelcode": "Fout", "melding": "1", "melding_rol": "alert",
         "melding_toon": "fout", "melding_icoon": "storing", "melding_titel": "Koppelen is mislukt",
         "melding_tekst": "Tekst", "melding_link": "1", "melding_link_doel": "koppelcode",
         "melding_link_tekst": "Naar het veld", "melding_code": "hub_weigerde", "trace_id": "3d8a1f4e"},
        {**vast, "koppelformulier": "1", "koppelen_bezig": "1", "koppelen_bezig_sinds": "08:00 UTC",
         "koppelen_bezig_iso": "2026-11-10T08:00:00Z"},
        {**vast, "koppelstatus_onleesbaar": "1", "melding": "1", "melding_rol": "", "melding_toon": "fout",
         "melding_icoon": "storing", "melding_titel": "Onleesbaar", "melding_tekst": "Het bestand:",
         "melding_pad": "/appdata/secrets/hub-link.json", "melding_code": "koppelstatus_onleesbaar", "trace_id": ""},
        {**vast, "gekoppeld": "1", "gekoppeld_sinds": "9 nov 2026", "hub_adres": "http://nas.invalid:30400",
         "hub_vingerafdruk": "MEGG-8GFG-2E4Y-K2RQ", "bron": "Eigen controles", "gereed_uitleg": "Uitleg",
         "melding": "1", "melding_rol": "status", "melding_toon": "info", "melding_icoon": "info",
         "melding_titel": "Gekoppeld", "melding_tekst": "Bevestigd.", "melding_code": "", "trace_id": ""},
        {**vast, "ontkoppeld": "1"},
        {**vast, "ingetrokken": "1"},
    ]


def test_renderer_gives_the_same_bytes_as_the_reference() -> None:
    """E07: de Python-renderer van de dienst geeft bij volledige gegevens dezelfde bytes als ``bouw/sjabloon.py``."""
    from lifecycle.peil import render, statuspagina_template

    bron_beschikbaar()
    referentie = _referentie_renderer(pk.controleer_kopie()["statuspagina.html"].commit)
    tpl = statuspagina_template()
    for data in _gegevens():
        assert render(tpl, data) == referentie.render(tpl, data)
