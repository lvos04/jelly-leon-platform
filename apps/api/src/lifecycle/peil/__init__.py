"""Renderer voor het Peil-statuspaginasjabloon (ONTWERPTAAL §13.2; bron ``ai-server-manager/design/ontwerptaal``).

``statuspagina.html`` in deze map is byte-gelijk aan de versie-gepinde kopie in ``design/ontwerptaal/`` (herkomstkop
van 2 regels, O-14; CI controleert dat en de drift met de bron). Plekken in het sjabloon:

- ``{{x}}`` = tekst, altijd HTML-geëscaped (ook ``'`` en ``"``: plekken staan ook in attributen);
- ``<!--ALS:x-->…<!--/ALS:x-->`` tonen als ``x`` waar is, ``<!--NIET:x-->…`` als ``x`` onwaar is;
- ``<!--ELK:lijst-->…<!--/ELK:lijst-->`` herhalen per item (item-velden over de paginavelden heen).

Anders dan de referentie-renderer (``bouw/sjabloon.mjs``) gebeurt alles in **één doorgang**: een ingevulde waarde wordt
nooit opnieuw als plek gelezen (een waarde met ``{{csrf_token}}`` blijft letterlijk tekst). Een ontbrekende plek is een
fout (geen stille lege tekst), en toonwaarden komen alleen uit de vaste lijst.
"""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from functools import cache
from importlib import resources
from typing import Any

PEIL_VERSIE = "1.1.0"
TONEN = frozenset({"gezond", "beperkt", "storing", "onbekend", "gate"})
MELDING_TONEN = frozenset({"fout", "info"})
ICONEN = frozenset({"gezond", "beperkt", "storing", "onbekend", "gate", "gekoppeld", "niet-gekoppeld", "info"})
_VASTE_LIJSTEN: dict[str, frozenset[str]] = {
    "toon": TONEN, "totaal_toon": TONEN, "melding_toon": MELDING_TONEN, "melding_icoon": ICONEN,
}
_TOKEN = re.compile(r"<!--(ELK|ALS|NIET):(\w+)-->(.*?)<!--/\1:\2-->|\{\{(\w+)\}\}", re.S)
_HERKOMST = re.compile(r"\A<!-- HERKOMST — Peil (\d+\.\d+\.\d+) · bron: [^\n]*\n[^\n]*-->\n")
_DOC_COMMENT = re.compile(r"\A(<!doctype html>\n)<!-- Peil statuspagina-sjabloon (\d+\.\d+\.\d+) [\s\S]*?-->\n(<html )")


class PeilFout(ValueError):
    """Het sjabloon of de gegevens kloppen niet (ontbrekende plek, onbekende toon, onverwacht sjabloon)."""


def _escape(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _lookup(data: Mapping[str, Any], key: str) -> Any:
    if key not in data:
        raise PeilFout(f"plek {{{{{key}}}}} heeft geen waarde")
    value = data[key]
    allowed = _VASTE_LIJSTEN.get(key)
    if allowed is not None and value not in allowed:
        raise PeilFout(f"{key}={value!r} staat niet in de vaste lijst {sorted(allowed)}")
    return value


def render(template: str, data: Mapping[str, Any]) -> str:
    """Vul het sjabloon in (één doorgang, recursief per blok)."""

    def fill(text: str, scope: Mapping[str, Any]) -> str:
        def repl(m: re.Match[str]) -> str:
            kind, key, inner, var = m.groups()
            if var is not None:
                return _escape(_lookup(scope, var))
            if kind == "ELK":
                items = scope.get(key) or []
                if not isinstance(items, list | tuple):
                    raise PeilFout(f"ELK:{key} verwacht een lijst")
                return "".join(fill(inner, {**scope, **item}) for item in items)
            if kind == "ALS":
                return fill(inner, scope) if scope.get(key) else ""
            return "" if scope.get(key) else fill(inner, scope)

        return _TOKEN.sub(repl, text)

    return fill(template, data)


def prepare(source: str, expected_version: str = PEIL_VERSIE) -> str:
    """Herkomstkop en de documentatie-opmerking van het sjabloon weg (die zijn voor bouwers, niet voor de browser).
    De pagina begint daarna met ``<!doctype html>`` en ``<html lang="nl">``. Gooit ``PeilFout`` bij een ander
    sjabloon dan verwacht (bijv. een nieuwe Peil-versie zonder aanpassing van de dienst)."""
    m = _HERKOMST.match(source)
    if not m or m.group(1) != expected_version:
        raise PeilFout(f"herkomstkop ontbreekt of is niet Peil {expected_version}")
    body = source[m.end():]
    d = _DOC_COMMENT.match(body)
    if not d or d.group(2) != expected_version:
        raise PeilFout("onverwacht sjabloon: geen <!doctype html> + Peil-documentatie vóór <html>")
    return d.group(1) + d.group(3) + body[d.end():]


@cache
def statuspagina_template() -> str:
    """Het voorbereide sjabloon uit het pakket (één keer gelezen)."""
    source = resources.files(__package__).joinpath("statuspagina.html").read_text(encoding="utf-8")
    return prepare(source)
