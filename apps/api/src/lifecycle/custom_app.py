"""Regels voor terugzetten, digest en catalogusversies van apps op TrueNAS (contract 1.2 §2.2, §8; B08-003/004).

Bouwstenen voor de lifecycle-planner (P2). Deze module is **puur** (geen netwerk, geen NAS): ze maakt recepten en
blokkades die de planner later via de executor laat uitvoeren. In fase P0 voert deze dienst niets uit; de plan-routes
geven nog ``capability_unavailable``. De regels zijn Sim-getest tegen de TrueNAS-simulator; op een echte NAS
**NIET GEVALIDEERD**.

- **Digest in het tagveld:** het formulier kent alleen ``repository`` + ``tag``; eigen en upstream-images worden
  ingevuld als ``<versie>@sha256:<64 hex>``. Of het formulier ``@`` accepteert en of TrueNAS daarmee updates
  detecteert, is NIET GEVALIDEERD; daarom leest de executor na elke installatie/update de digest terug
  (``app.image.query`` → ``repo_digests``) en stopt bij een afwijking (``check_digest_readback``).
- **Terugzetten van een Custom App** (``ix-app``) = ``app.update`` met de **volledige vorige waarden** (TrueNAS doet een
  ondiepe merge op het hoogste niveau, dus alle sleutels mee). ``app.rollback`` alleen voor catalogusapps, naar een
  eerder geïnstalleerde lagere catalogusversie.
- **Verdwenen catalogusversie** (ook de ``ix-app``-versie zelf) → blokkade ``version_unavailable`` met een voorstel;
  **nooit** stil een nieuwere versie of ``latest``.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

IX_APP = "ix-app"
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,127}$")


class RecipeError(ValueError):
    """Ongeldige invoer voor een recept (melding in het Nederlands)."""


@dataclass(frozen=True)
class Blocker:
    """Blokkade in een plan: de planner stopt hier en toont ``message_nl`` + ``proposal_nl``."""

    code: str
    message_nl: str
    proposal_nl: str
    details: dict[str, Any] = field(default_factory=dict)


def form_tag(version: str, digest: str | None) -> str:
    """Waarde voor het tagveld: ``<versie>@sha256:<digest>`` (of alleen de versie als er nog geen digest is)."""
    if not VERSION_RE.fullmatch(version) or version.lower() == "latest":
        raise RecipeError("Versie ongeldig of 'latest'; gebruik een exacte versie.")
    if digest is None:
        return version
    if not DIGEST_RE.fullmatch(digest):
        raise RecipeError("Digest moet sha256:<64 hex> zijn.")
    return f"{version}@{digest}"


def split_form_tag(tag: str) -> tuple[str, str | None]:
    version, sep, digest = tag.partition("@")
    if sep and not DIGEST_RE.fullmatch(digest):
        raise RecipeError("Tag met '@' moet eindigen op sha256:<64 hex>.")
    if not VERSION_RE.fullmatch(version) or version.lower() == "latest":
        raise RecipeError("Versie in de tag ongeldig of 'latest'.")
    return version, (digest if sep else None)


def catalog_version_blocker(
    *, catalog_app: str, train: str, pinned_version: str, available_versions: Iterable[str]
) -> Blocker | None:
    """``version_unavailable`` als de vastgezette versie niet (meer) in de catalogus staat (§2.2). Geldt ook voor de
    ``ix-app``-versie waarmee een eigen Custom App wordt geïnstalleerd (niet voor ``app.update`` van een bestaande)."""
    available = sorted(set(available_versions))
    if pinned_version in available:
        return None
    if catalog_app == IX_APP:
        proposal = ("Test de vertaling naar formulierwaarden met de nieuwe ix-app-versie "
                    f"({', '.join(available) or '?'}) en leg die versie daarna vast; "
                    "tot dan wordt niets geïnstalleerd.")
    else:
        proposal = (f"Werk het manifest bij naar een nieuwe vaste versie ({', '.join(available) or 'geen beschikbaar'})"
                    " en test die eerst; nooit stil een nieuwere versie of 'latest' installeren.")
    return Blocker(
        "version_unavailable",
        f"Versie {pinned_version} van '{catalog_app}' staat niet meer in de trein '{train}'.",
        proposal,
        {"catalog_app": catalog_app, "train": train, "pinned_version": pinned_version, "available": available},
    )


def rollback_recipe(
    *,
    app_name: str,
    catalog_app: str,
    previous_values: Mapping[str, Any] | None = None,
    previous_version: str | None = None,
    rollback_versions: Iterable[str] = (),
) -> dict[str, Any] | Blocker:
    """Recept voor terugzetten (vóór elke update bewaart de planner ``previous_values`` inclusief tag + digest).

    - ``ix-app`` (standaard Custom App): ``app.update`` met de volledige vorige waarden — nooit ``app.rollback``
      (een image-tagwijziging is geen catalogusversie);
    - catalogusapp: ``app.rollback`` naar ``previous_version`` als die lokaal eerder geïnstalleerd is.
    Data bij een schemawijziging herstelt de eigenaar of een snapshot (R3 bij DB-restore); dat zit niet in dit recept.
    """
    if catalog_app == IX_APP:
        if not previous_values or not isinstance(previous_values.get("image"), Mapping):
            return Blocker("rollback_recipe_missing", "De vorige waarden van deze Custom App zijn niet bewaard.",
                           "Terugzetten kan alleen met de vóór de update bewaarde waarden (tag + digest); herstel via "
                           "de eigenaar of een snapshot.", {"app_name": app_name})
        image = previous_values["image"]
        split_form_tag(str(image.get("tag", "")))  # nooit terug naar 'latest' of een vrije tag
        return {"method": "app.update", "params": [app_name, {"values": copy.deepcopy(dict(previous_values))}],
                "readback": {"repository": image.get("repository"), "tag": image.get("tag")}}
    versions = set(rollback_versions)
    if previous_version is None or previous_version not in versions:
        return Blocker("rollback_version_unavailable",
                       f"Catalogusversie {previous_version} is niet lokaal beschikbaar om naar terug te gaan.",
                       "Kies een versie uit app.rollback_versions of herstel via de eigenaar of een snapshot.",
                       {"app_name": app_name, "available": sorted(versions)})
    return {"method": "app.rollback", "params": [app_name, {"app_version": previous_version,
                                                            "rollback_snapshot": True}]}


@dataclass(frozen=True)
class Readback:
    ok: bool
    observed: tuple[str, ...]
    message_nl: str


def check_digest_readback(
    *, repository: str, expected_digest: str, image_rows: Iterable[Mapping[str, Any]]
) -> Readback:
    """Na installatie/update: staat ``<repository>@<expected_digest>`` in ``repo_digests`` van ``app.image.query``?
    Zo niet: stoppen + incident (de planner zet de run niet door)."""
    if not DIGEST_RE.fullmatch(expected_digest):
        raise RecipeError("Verwachte digest moet sha256:<64 hex> zijn.")
    wanted = f"{repository}@{expected_digest}"
    observed = tuple(sorted({str(d) for row in image_rows for d in (row.get("repo_digests") or [])
                             if str(d).startswith(repository + "@")}))
    if wanted in observed:
        return Readback(True, observed, "Digest na installatie komt overeen.")
    return Readback(False, observed, "Digest na installatie wijkt af of ontbreekt: gestopt, incident aangemaakt.")
