"""Renderer van het Peil-statuspaginasjabloon (``lifecycle.peil``): plekken, blokken, escapen, vaste toonlijst."""

from __future__ import annotations

from typing import Any

import pytest

from lifecycle.peil import PEIL_VERSIE, PeilFout, prepare, render, statuspagina_template


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
    assert out == '<a title="{{geheim}} &quot;&#x27;&lt;script&gt;&amp;">{{geheim}} &quot;&#x27;&lt;script&gt;&amp;</a>'
    assert "S3CRET" not in out


@pytest.mark.parametrize("value", ["{{csrf_token}}", "<script>alert(1)</script>", "\"' onmouseover=x"])
def test_full_template_escapes_text(value: str) -> None:
    html = render(statuspagina_template(), minimal_data(dienst_omschrijving=value, csrf_token="GEHEIM"))
    assert "<script>" not in html and "onmouseover=x" not in html.replace("&#x27; onmouseover=x", "")
    assert html.count("GEHEIM") == 1  # alleen in het verborgen CSRF-veld


def test_missing_placeholder_and_unknown_tone_are_errors() -> None:
    with pytest.raises(PeilFout, match="totaal_toon"):
        render(statuspagina_template(), {k: v for k, v in minimal_data().items() if k != "totaal_toon"})
    with pytest.raises(PeilFout, match="vaste lijst"):
        render(statuspagina_template(), minimal_data(totaal_toon="groen"))
    with pytest.raises(PeilFout, match="vaste lijst"):
        render(statuspagina_template(), minimal_data(checks=[{"naam": "x", "toon": "neutraal", "label": "x",
                                                              "iso": "", "tijd": ""}]))
    with pytest.raises(PeilFout, match="vaste lijst"):
        render(statuspagina_template(), minimal_data(melding=True, melding_toon="succes", melding_icoon="info",
                                                     melding_titel="t", melding_tekst="t", melding_link=False,
                                                     trace_id=""))


def test_prepare_strips_provenance_and_builder_comment() -> None:
    tpl = statuspagina_template()
    assert tpl.startswith('<!doctype html>\n<html lang="nl">\n<head>')
    assert "HERKOMST" not in tpl and "Peil statuspagina-sjabloon" not in tpl
    assert f"/*! Peil statuspagina.css {PEIL_VERSIE}" in tpl  # de CSS-kop blijft (herkenbaar in de image-E2E)


def test_prepare_refuses_unexpected_templates() -> None:
    good = ("<!-- HERKOMST — Peil 1.1.0 · bron: x\n     y -->\n<!doctype html>\n"
            "<!-- Peil statuspagina-sjabloon 1.1.0 · bron …\n -->\n<html lang=\"nl\"></html>")
    assert prepare(good) == '<!doctype html>\n<html lang="nl"></html>'
    with pytest.raises(PeilFout):
        prepare(good.replace("Peil 1.1.0 · bron: x", "Peil 1.2.0 · bron: x"))
    with pytest.raises(PeilFout):
        prepare(good.split("\n", 2)[2])  # zonder herkomstkop
    with pytest.raises(PeilFout):
        prepare(good.replace("sjabloon 1.1.0", "sjabloon 2.0.0"))
