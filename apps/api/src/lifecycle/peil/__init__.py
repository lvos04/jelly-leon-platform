"""Renderer voor het Peil-statuspaginasjabloon (ONTWERPTAAL §13.2; bron ``ai-server-manager/design/ontwerptaal``).

``statuspagina.html`` in deze map is byte-gelijk aan de versie-gepinde kopie in ``design/ontwerptaal/`` (herkomstkop
van 2 regels, O-14; CI controleert dat en de drift met de bron). Plekken in het sjabloon:

- ``{{x}}`` = tekst, altijd HTML-geëscaped (ook ``'`` en ``"``: plekken staan ook in attributen);
- ``<!--ALS:x-->…<!--/ALS:x-->`` tonen als ``x`` waar is, ``<!--NIET:x-->…`` als ``x`` onwaar is;
- ``<!--ELK:lijst-->…<!--/ELK:lijst-->`` herhalen per item (item-velden over de paginavelden heen);
- blokken mogen genest zijn, ook ``NIET`` binnen ``ALS`` (Peil 1.1.1).

Peil 1.1.1 (E07): **één doorgang** zoals de referentie ``bouw/sjabloon.py``: het sjabloon wordt eerst in een boom
gelezen en daarna ingevuld, dus een waarde wordt nooit opnieuw als sjabloon gelezen (een waarde ``{{csrf_token}}``
blijft letterlijk tekst). Waar = niet leeg (``False``, ``None``, ``""``, 0 en een lege lijst zijn onwaar). Strenger
dan de referentie: een ontbrekende ``{{plek}}`` is een fout (geen stille lege tekst), toonwaarden en meldingsrollen
komen alleen uit de vaste lijsten, en ``True``/``False`` als tekst zijn een fout. Bij volledige gegevens geeft deze
renderer dezelfde bytes als de referentie (``tests/repo/test_peil_kopie.py``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any

PEIL_VERSIE = "1.1.1"
# ONTWERPTAAL §13.2 (1.1.1, E03): tonen van rijen; het totaal is alleen gezond | beperkt | storing | onbekend.
TONEN = frozenset({"gezond", "beperkt", "storing", "onbekend", "verouderd", "niet-beschikbaar", "nog-niet",
                   "niet-gekoppeld", "gate", "beleid", "feit"})
TOTAAL_TONEN = frozenset({"gezond", "beperkt", "storing", "onbekend"})
MELDING_TONEN = frozenset({"fout", "info"})
MELDING_ICONEN = frozenset({"storing", "info"})
MELDING_ROLLEN = frozenset({"alert", "status", ""})
_VASTE_LIJSTEN: dict[str, frozenset[str]] = {
    "toon": TONEN, "totaal_toon": TOTAAL_TONEN, "melding_toon": MELDING_TONEN, "melding_icoon": MELDING_ICONEN,
    "melding_rol": MELDING_ROLLEN,
}
_TOKEN = re.compile(r"<!--(/?)(ELK|ALS|NIET):(\w+)-->|\{\{(\w+)\}\}")
_HERKOMST = re.compile(r"\A<!-- HERKOMST — Peil (\d+\.\d+\.\d+) · bron: [^\n]*\n[^\n]*-->\n")
_DOC_COMMENT = re.compile(r"\A(<!doctype html>\n)<!-- Peil statuspagina-sjabloon (\d+\.\d+\.\d+) [\s\S]*?-->\n(<html )")


class PeilFout(ValueError):
    """Het sjabloon of de gegevens kloppen niet (ontbrekende plek, onbekende toon, onverwacht sjabloon)."""


@dataclass
class _Blok:
    soort: str  # ELK | ALS | NIET | (wortel)
    naam: str
    kids: list[_Blok | _Plek | str] = field(default_factory=list)


@dataclass(frozen=True)
class _Plek:
    naam: str


def _escape(value: Any) -> str:
    """Zoals ``bouw/sjabloon.py``: ``& < > " '`` (``'`` als ``&#39;``)."""
    tekst = "" if value is None else str(value)
    return (tekst.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            .replace("'", "&#39;"))


def lees(sjabloon: str) -> _Blok:
    """Leest het sjabloon in een boom. ``PeilFout`` bij een niet-gesloten of verkeerd genest blok."""
    wortel = _Blok("", "")
    stapel = [wortel]
    pos = 0
    for m in _TOKEN.finditer(sjabloon):
        top = stapel[-1]
        if m.start() > pos:
            top.kids.append(sjabloon[pos:m.start()])
        pos = m.end()
        sluit, soort, naam, plek = m.groups()
        if plek is not None:
            top.kids.append(_Plek(plek))
        elif not sluit:
            blok = _Blok(soort, naam)
            top.kids.append(blok)
            stapel.append(blok)
        else:
            if len(stapel) == 1 or (top.soort, top.naam) != (soort, naam):
                raise PeilFout(f"sjabloon: onverwachte sluiting {m.group(0)}")
            stapel.pop()
    if len(stapel) != 1:
        raise PeilFout(f"sjabloon: blok niet gesloten: {stapel[-1].soort}:{stapel[-1].naam}")
    if pos < len(sjabloon):
        wortel.kids.append(sjabloon[pos:])
    return wortel


def _waarde(data: Mapping[str, Any], key: str) -> Any:
    if key not in data:
        raise PeilFout(f"plek {{{{{key}}}}} heeft geen waarde")
    value = data[key]
    allowed = _VASTE_LIJSTEN.get(key)
    if allowed is not None and value not in allowed:
        raise PeilFout(f"{key}={value!r} staat niet in de vaste lijst {sorted(allowed)}")
    if isinstance(value, bool):
        raise PeilFout(f"plek {{{{{key}}}}} kreeg een waarheidswaarde in plaats van tekst")
    return value


def _schrijf(blok: _Blok, data: Mapping[str, Any]) -> str:
    uit: list[str] = []
    for kid in blok.kids:
        if isinstance(kid, str):
            uit.append(kid)
        elif isinstance(kid, _Plek):
            uit.append(_escape(_waarde(data, kid.naam)))
        elif kid.soort == "ELK":
            items = data.get(kid.naam) or []
            if not isinstance(items, list | tuple):
                raise PeilFout(f"ELK:{kid.naam} verwacht een lijst")
            for item in items:
                uit.append(_schrijf(kid, {**data, **item}))
        elif kid.soort == "ALS":
            if data.get(kid.naam):
                uit.append(_schrijf(kid, data))
        elif not data.get(kid.naam):  # NIET
            uit.append(_schrijf(kid, data))
    return "".join(uit)


def render(template: str, data: Mapping[str, Any]) -> str:
    """Vul het sjabloon in (één doorgang). Toonwaarden en meldingsrollen worden ook in niet-getoonde blokken niet
    gecontroleerd; alleen wat op de pagina komt."""
    return _schrijf(_boom(template), data)


@cache
def _boom(template: str) -> _Blok:
    return lees(template)


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
    """Het voorbereide sjabloon uit het pakket (één keer gelezen en gecontroleerd op een geldige blokstructuur)."""
    source = resources.files(__package__).joinpath("statuspagina.html").read_text(encoding="utf-8")
    tpl = prepare(source)
    lees(tpl)
    return tpl
