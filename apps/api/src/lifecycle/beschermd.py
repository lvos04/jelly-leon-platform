"""Beschermde apps volgens het beleid van de Hub (HARDE EIS; contract 1.3 §2.3 regels 3 en 7, §8).

Het platform stopt, updatet of verwijdert Emby nooit; een update, terugzetting of verwijdering van de mediaserver of van
MediaGuard vraagt eerst een vóór/na-rapport en akkoord (P4). Welke apps dat zijn en hoe de Hub ze herkent, staat alleen
in het beleid van de Hub. Deze module leest daarom een **byte-gelijke, gepinde kopie** van ai-server-manager
``policy/apps.yaml`` (``lifecycle/beleid/apps.yaml``, herkomst in ``beleid/KOPIE.json``; ``tools/beleid_kopie.py``;
ADR-0025) en gebruikt alleen de koppen ``herkenning`` en ``beschermd``.

Herkenning zoals de Hub (``asm_policy.engine.recognize_all``): op de exacte TrueNAS-appnaam en op de basisnaam van
elke image (zonder registry, tag of digest); zonder exacte treffer het langste voorvoegsel gevolgd door ``-`` of ``_``.
Naam en image kunnen naar verschillende apps wijzen; dan gelden de regels van allebei. Een herkenning maakt alleen
strenger, nooit soepeler. Onleesbaar beleid = ``BeleidFout``: de planner geeft dan niets vrij (nooit "open bij fout").
De Hub blijft de eigenaar en weigert zelf ook, ook met goedkeuring.
"""

from __future__ import annotations

import functools
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

BELEID = Path(__file__).with_name("beleid") / "apps.yaml"


class BeleidFout(Exception):
    """De kopie van het Hub-beleid ontbreekt of heeft niet de verwachte vorm."""


@dataclass(frozen=True)
class Regel:
    id: str
    app: str
    acties: frozenset[str] | None  # None = alle acties ('*')
    code: str
    reden_nl: str


@dataclass(frozen=True)
class Beleid:
    herkenning: dict[str, tuple[str, ...]]
    beschermd: tuple[Regel, ...]


def _lees(pad: Path) -> Beleid:
    try:
        doc = yaml.safe_load(pad.read_text(encoding="utf-8"))
        herkenning = {str(app): tuple(str(n).lower() for n in v["namen"]) for app, v in doc["herkenning"].items()}
        regels = []
        for r in doc["beschermd"]:
            acties = None if r["acties"] == "*" else frozenset(str(a) for a in r["acties"])
            regels.append(Regel(str(r["id"]), str(r["app"]), acties, str(r["code"]), str(r["reden_nl"])))
    except (OSError, yaml.YAMLError, KeyError, TypeError, AttributeError) as exc:
        reden = type(exc).__name__
        raise BeleidFout(f"{pad.name} ontbreekt of heeft niet de vorm van het Hub-beleid ({reden})") from exc
    if not herkenning or not regels or any(not namen for namen in herkenning.values()):
        raise BeleidFout(f"{pad.name}: lege herkenning of geen regels 'beschermd'")
    return Beleid(herkenning, tuple(regels))


@functools.cache
def beleid() -> Beleid:
    """Het Hub-beleid uit de gepinde kopie (één keer gelezen)."""
    return _lees(BELEID)


def _basisnamen(images: Iterable[str]) -> list[str]:
    out = []
    for ref in images:
        base = str(ref).lower().split("@", 1)[0].rsplit("/", 1)[-1]
        out.append(base.split(":", 1)[0])
    return out


def herken(b: Beleid, naam: str | None, images: Iterable[str] = ()) -> tuple[str, ...]:
    """Alle herkenningen van een app (eerst de appnaam, dan per image-basisnaam), zoals de Hub."""
    gevonden: list[str] = []
    for h in ([naam.lower()] if naam else []) + _basisnamen(images):
        treffer = next((app for app, namen in b.herkenning.items() if h in namen), None)
        if treffer is None:
            best: tuple[int, str] | None = None
            for app, namen in b.herkenning.items():
                for n in namen:
                    if (h.startswith(n + "-") or h.startswith(n + "_")) and (best is None or len(n) > best[0]):
                        best = (len(n), app)
            treffer = best[1] if best else None
        if treffer is not None and treffer not in gevonden:
            gevonden.append(treffer)
    return tuple(gevonden)


def regels(b: Beleid, actie: str, naam: str | None, images: Iterable[str] = ()) -> list[Regel]:
    """De regels 'beschermd' die deze actie op deze app weigeren (leeg = het beleid weigert haar hierom niet)."""
    herkend = herken(b, naam, images)
    return [r for r in b.beschermd if r.app in herkend and (r.acties is None or actie in r.acties)]


def details(regel: Regel, actie: str, naam: str | None) -> dict[str, Any]:
    return {"bron": "hub_beleid", "regel": regel.id, "actie": actie, "app_name": naam}


__all__ = ["BELEID", "Beleid", "BeleidFout", "Regel", "beleid", "details", "herken", "regels"]
