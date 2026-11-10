"""Renderer van het Peil-statuspaginasjabloon (``lifecycle.peil``, Peil 1.1.1): plekken, geneste blokken in één
doorgang, escapen zoals de referentie, vaste lijsten voor tonen en meldingsrollen."""

from __future__ import annotations

from typing import Any

import pytest

from lifecycle.peil import PEIL_VERSIE, TONEN, PeilFout, lees, prepare, render, statuspagina_template


def minimal_data(**over: Any) -> dict[str, Any]:
    """Alle plekken van het sjabloon voor een niet-gekoppelde dienst zonder melding."""
    data: dict[str, Any] = {
        "voorbeeld": False, "dienst_naam": "Dienst", "dienst_omschrijving": "Omschrijving", "titel_toestand": "t",
        "spring_doel": "inhoud", "spring_tekst": "Naar inhoud", "melding": False,
        "totaal_toon": "onbekend", "totaal_label": "Niet gereed", "totaal_toelichting": "Deels onbekend",
        "checks": [{"naam": "Proces", "toon": "gezond", "label": "Gezond", "iso": "2026-11-09T11:56:00.000Z",
                    "tijd": "11:56 UTC"}],
        "gereed": "Nee", "versie": "0.1.0", "bijgewerkt": "9 nov 2026, 11:56 UTC",
        "bijgewerkt_iso": "2026-11-09T11:56:00.000Z", "gekoppeld": False, "ontkoppeld": False, "ingetrokken": False,
        "koppelformulier": True, "csrf_token": "tok", "koppelcode_geldig": "60 minuten", "fout_hub_adres": "",
        "fout_koppelcode": "", "fout_vingerafdruk": "", "schrijfsleutel_formulier": False, "executor_beheer": False,
        "dienst_blok_titel": "Blok", "dienst_blok": [],
    }
    return {**data, **over}


def test_blocks_and_placeholders() -> None:
    tpl = ("<!--ALS:a-->A{{x}}<!--/ALS:a--><!--NIET:a-->N<!--/NIET:a-->|"
           "<!--ELK:l-->[{{x}}<!--ALS:r-->:{{r}}<!--/ALS:r-->]<!--/ELK:l-->")
    assert render(tpl, {"a": True, "x": "1", "l": [{"x": "2"}, {"x": "3", "r": "z"}]}) == "A1|[2][3:z]"
    assert render(tpl, {"a": False, "x": "1", "l": []}) == "N|"


def test_values_are_escaped_and_never_read_as_placeholders() -> None:
    """Anders dan bouw/sjabloon.mjs: een waarde wordt nooit opnieuw als plek ingevuld (één doorgang)."""
    out = render('<a title="{{t}}">{{t}}</a><!--ALS:b-->{{geheim}}<!--/ALS:b-->',
                 {"t": "{{geheim}} \"'<script>&", "b": False, "geheim": "S3CRET"})
    assert out == '<a title="{{geheim}} &quot;&#39;&lt;script&gt;&amp;">{{geheim}} &quot;&#39;&lt;script&gt;&amp;</a>'
    assert "S3CRET" not in out


def test_nested_blocks_niet_inside_als_and_empty_list_is_false() -> None:
    """1.1.1: het sjabloon zet ``NIET:koppelen_bezig`` binnen ``ALS:koppelformulier``; een lege lijst is onwaar."""
    tpl = ("<!--ALS:f-->F<!--NIET:b-->[form]<!--/NIET:b--><!--/ALS:f-->"
           "<!--ALS:l-->L<!--/ALS:l--><!--NIET:l-->-<!--/NIET:l-->")
    assert render(tpl, {"f": True, "b": False, "l": []}) == "F[form]-"
    assert render(tpl, {"f": True, "b": True, "l": [{}]}) == "FL"
    assert render(tpl, {"f": False, "b": False, "l": 0}) == "-"
    # dezelfde bloknaam twee keer na elkaar (zoals koppelformulier in het sjabloon)
    assert render("<!--ALS:a-->1<!--/ALS:a-->x<!--ALS:a-->2<!--/ALS:a-->", {"a": "ja"}) == "1x2"


@pytest.mark.parametrize("tpl", ["<!--ALS:a-->x", "<!--ALS:a-->x<!--/NIET:a-->", "x<!--/ALS:a-->",
                                 "<!--ALS:a--><!--NIET:b--><!--/ALS:a--><!--/NIET:b-->"])
def test_unclosed_or_crossed_blocks_are_errors(tpl: str) -> None:
    with pytest.raises(PeilFout):
        lees(tpl)


def test_booleans_as_text_are_errors() -> None:
    with pytest.raises(PeilFout, match="waarheidswaarde"):
        render("{{x}}", {"x": True})


@pytest.mark.parametrize("value", ["{{csrf_token}}", "<script>alert(1)</script>", "\"' onmouseover=x"])
def test_full_template_escapes_text(value: str) -> None:
    html = render(statuspagina_template(), minimal_data(dienst_omschrijving=value, csrf_token="GEHEIM"))
    assert "<script>" not in html and "onmouseover=x" not in html.replace("&#39; onmouseover=x", "")
    assert html.count("GEHEIM") == 1  # alleen in het verborgen CSRF-veld


def test_missing_placeholder_and_unknown_tone_are_errors() -> None:
    with pytest.raises(PeilFout, match="totaal_toon"):
        render(statuspagina_template(), {k: v for k, v in minimal_data().items() if k != "totaal_toon"})
    with pytest.raises(PeilFout, match="vaste lijst"):
        render(statuspagina_template(), minimal_data(totaal_toon="groen"))
    with pytest.raises(PeilFout, match="vaste lijst"):
        render(statuspagina_template(), minimal_data(checks=[{"naam": "x", "toon": "neutraal", "label": "x",
                                                              "iso": "", "tijd": ""}]))
    with pytest.raises(PeilFout, match="vaste lijst"):  # het totaal kent alleen de vier gezondheidswoorden
        render(statuspagina_template(), minimal_data(totaal_toon="niet-gekoppeld"))
    with pytest.raises(PeilFout, match="vaste lijst"):
        render(statuspagina_template(), minimal_data(melding=True, melding_toon="fout", melding_icoon="storing",
                                                     melding_rol="log", melding_titel="t", melding_tekst="t"))
    with pytest.raises(PeilFout, match="vaste lijst"):
        render(statuspagina_template(), minimal_data(melding=True, melding_toon="succes", melding_icoon="info",
                                                     melding_titel="t", melding_tekst="t", melding_link=False,
                                                     trace_id=""))


@pytest.mark.parametrize("toon", sorted(TONEN))
def test_every_tone_has_its_symbol_and_pill_style(toon: str) -> None:
    """1.1.1 (E03): het symbool heet altijd ``#i-<toon>`` en elke toon heeft een pilstijl in het sjabloon."""
    tpl = statuspagina_template()
    assert f'<symbol id="i-{toon}"' in tpl and f".pil-{toon}" in tpl
    html = render(tpl, minimal_data(checks=[{"naam": "x", "toon": toon, "label": "Woord", "iso": "", "tijd": ""}]))
    assert f'<span class="pil pil-{toon}"><svg aria-hidden="true"><use href="#i-{toon}"/></svg>Woord</span>' in html


def test_melding_role_only_when_given() -> None:
    """1.1.1 (E05): ``role`` alleen voor dít verzoek (alert/status); een bestaande toestand heeft geen live-rol."""
    base: dict[str, Any] = {"melding": True, "melding_toon": "fout", "melding_icoon": "storing", "melding_titel": "T",
                            "melding_tekst": "X", "melding_link": False, "melding_code": "koppelstatus_onleesbaar",
                            "trace_id": ""}
    zonder = render(statuspagina_template(), minimal_data(**base, melding_rol=""))
    assert '<div class="melding melding-fout" style=' in zonder and "role=" not in zonder.split('id="inhoud"')[1][:300]
    assert "Code <code>koppelstatus_onleesbaar</code>" in zonder and 'class="selecteer"' not in zonder
    met = render(statuspagina_template(), minimal_data(**base, melding_rol="alert"))
    assert '<div class="melding melding-fout" role="alert" aria-labelledby="melding-titel"' in met


def test_prepare_strips_provenance_and_builder_comment() -> None:
    tpl = statuspagina_template()
    assert tpl.startswith('<!doctype html>\n<html lang="nl">\n<head>')
    assert "HERKOMST" not in tpl and "Peil statuspagina-sjabloon" not in tpl
    assert f"/*! Peil statuspagina.css {PEIL_VERSIE}" in tpl  # de CSS-kop blijft (herkenbaar in de image-E2E)


def test_prepare_refuses_unexpected_templates() -> None:
    good = ("<!-- HERKOMST — Peil 1.1.1 · bron: x\n     y -->\n<!doctype html>\n"
            "<!-- Peil statuspagina-sjabloon 1.1.1 · bron …\n -->\n<html lang=\"nl\"></html>")
    assert prepare(good) == '<!doctype html>\n<html lang="nl"></html>'
    with pytest.raises(PeilFout):
        prepare(good.replace("Peil 1.1.1 · bron: x", "Peil 1.1.0 · bron: x"))
    with pytest.raises(PeilFout):
        prepare(good.split("\n", 2)[2])  # zonder herkomstkop
    with pytest.raises(PeilFout):
        prepare(good.replace("sjabloon 1.1.1", "sjabloon 2.0.0"))
