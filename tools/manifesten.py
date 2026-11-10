"""Controleer alle installeerbare manifesten (``install/manifests/*.manifest.yaml``) met de validator van de eigenaar
van de manifeststandaard (``appstore-en-fork``, ``jl-manifest validate --strict``; contract 1.3 §9).

Regel (contract 1.3 §9, ADR-0019): geaccepteerde waarschuwingen staan in het manifest zelf (``accepted_findings``; de
validator controleert de vaste lijst en meldt een overbodige acceptatie). Een manifest moet ``--strict`` halen, met
twee smalle uitzonderingen uit ``overgang-1.3.yaml``: ``kopieen`` (manifesten van een andere repo, alleen de eigenaar
past ze aan) en ``hiaten`` (wat de standaard nog niet kan uitdrukken; nooit een code uit de vaste lijst). De
vermelde waarschuwingen moeten exact optreden — niet meer, niet minder. Daarnaast eigen controles die los van de
validator altijd gelden: geen compose-velden, geen ``latest``, upstream-images met digest, run_as nooit root.

Gebruik::

    python tools/manifesten.py [--validator <pad naar jl-manifest>] [--json]

Zonder ``--validator``: ``$JL_MANIFEST``, anders ``.venv-validator/bin/jl-manifest`` in deze repo.
Exitcode 0 = alles in orde, 1 = afwijking, 2 = validator niet gevonden.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_DIR = ROOT / "install" / "manifests"
UITZONDERINGEN = MANIFEST_DIR / "overgang-1.3.yaml"
EIGEN_REPO = "lvos04/jelly-leon-platform"
ACCEPTEERBAAR = frozenset({"backup_db_export_none", "secret_in_form", "digest_pending_release"})
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
VERBODEN_SLEUTELS = ("compose", "custom_compose_config", "custom_compose_config_string", "services")


@dataclass
class Uitkomst:
    bestand: str
    app_id: str
    strict_exit: int
    fouten: int
    waarschuwingen: list[tuple[str, str]]
    afwijkingen: list[str] = field(default_factory=list)

    @property
    def in_orde(self) -> bool:
        return not self.afwijkingen


def validator_pad(opgegeven: str | None) -> Path:
    kandidaten = [opgegeven, os.environ.get("JL_MANIFEST"), str(ROOT / ".venv-validator" / "bin" / "jl-manifest")]
    for kandidaat in kandidaten:
        if kandidaat and Path(kandidaat).is_file():
            return Path(kandidaat)
    raise FileNotFoundError(
        "jl-manifest (appstore-en-fork) niet gevonden; zet JL_MANIFEST of maak .venv-validator "
        "(zie Makefile: validator-venv)"
    )


def manifesten() -> list[Path]:
    return sorted(MANIFEST_DIR.glob("*.manifest.yaml"))


def lees_uitzonderingen() -> dict[str, tuple[str, set[tuple[str, str]]]]:
    """app_id → (soort ``kopieen``/``hiaten``, {(code, pad)}). Een code uit de vaste lijst van ``accepted_findings``
    mag nooit onder ``hiaten`` staan (die hoort in het manifest)."""
    data = yaml.safe_load(UITZONDERINGEN.read_text(encoding="utf-8")) or {}
    if set(data) - {"kopieen", "hiaten"}:
        raise ValueError(f"onbekende soort in {UITZONDERINGEN.name}: {sorted(set(data) - {'kopieen', 'hiaten'})}")
    out: dict[str, tuple[str, set[tuple[str, str]]]] = {}
    for soort in ("kopieen", "hiaten"):
        for app_id, lijst in (data.get(soort) or {}).items():
            regels: set[tuple[str, str]] = set()
            for item in lijst:
                if not str(item.get("reden_nl", "")).strip():
                    raise ValueError(f"uitzondering zonder reden_nl bij {app_id}")
                if soort == "hiaten" and item["code"] in ACCEPTEERBAAR:
                    raise ValueError(f"{app_id}: {item['code']} hoort in accepted_findings van het manifest")
                regels.add((str(item["code"]), str(item["pad"])))
            if str(app_id) in out:
                raise ValueError(f"{app_id} staat dubbel in {UITZONDERINGEN.name}")
            out[str(app_id)] = (soort, regels)
    return out


def _sleutels(waarde: Any) -> list[str]:
    if isinstance(waarde, dict):
        return [*map(str, waarde), *[k for v in waarde.values() for k in _sleutels(v)]]
    if isinstance(waarde, list):
        return [k for v in waarde for k in _sleutels(v)]
    return []


def eigen_controles(data: dict[str, Any]) -> list[str]:
    """Controles die altijd gelden, ook als de validator ooit soepeler wordt (contract §1, §9, EX47)."""
    fout: list[str] = []
    sleutels = _sleutels(data)
    for verboden in VERBODEN_SLEUTELS:
        if verboden in sleutels:
            fout.append(f"verboden sleutel '{verboden}' (compose/YAML-installatie)")
    tekst = json.dumps(data)
    if re.search(r'"(tag|version)": "latest"', tekst) or ":latest" in tekst:
        fout.append("'latest' als tag of versie")
    if data.get("install", {}).get("method") == "custom_app":
        image = data.get("image", {})
        eigen = str(image.get("repository", "")).startswith("ghcr.io/lvos04/")
        if not eigen and not DIGEST_RE.match(str(image.get("digest") or "")):
            fout.append("upstream-image zonder sha256-digest")
        run_as = data.get("custom_app", {}).get("run_as", {})
        if run_as.get("mode") == "custom" and (run_as.get("user") == 0 or run_as.get("group") == 0):
            fout.append("run_as root")
        if not eigen and run_as.get("mode") != "custom":
            fout.append("upstream-image zonder eigen gebruiker (run_as custom 568:568)")
    if data.get("network", {}).get("host_network") is not False:
        fout.append("host_network is niet false")
    if data.get("privileges", {}).get("privileged") is not False:
        fout.append("privileged is niet false")
    return fout


def controleer(validator: Path) -> list[Uitkomst]:
    uitzonderingen = lees_uitzonderingen()
    uitkomsten: list[Uitkomst] = []
    gezien: set[str] = set()
    for pad in manifesten():
        data = yaml.safe_load(pad.read_text(encoding="utf-8"))
        app_id = str(data.get("app_id"))
        gezien.add(app_id)
        res = subprocess.run(  # noqa: S603 - vaste validator, eigen bestanden
            [str(validator), "validate", "--strict", "--format", "json", str(pad)],
            capture_output=True,
            text=True,
            check=False,
        )
        rapport = json.loads(res.stdout)
        bevindingen = rapport.get("findings", [])
        fouten = [b for b in bevindingen if b.get("severity") == "error"]
        # geaccepteerd in het manifest (accepted_findings): de validator heeft de vaste lijst al gecontroleerd
        waarsch = sorted((str(b["code"]), str(b["path"])) for b in bevindingen
                         if b.get("severity") == "warning" and not b.get("accepted"))
        u = Uitkomst(pad.name, app_id, res.returncode, len(fouten), waarsch)
        if pad.name != f"{app_id}.manifest.yaml":
            u.afwijkingen.append(f"bestandsnaam hoort {app_id}.manifest.yaml te zijn")
        if fouten or not rapport.get("valid"):
            u.afwijkingen.append("ongeldig: " + "; ".join(f"{b['code']} @ {b['path']}" for b in fouten))
        soort, verwacht = uitzonderingen.get(app_id, ("", set()))
        if soort == "kopieen" and data.get("owner_repo") == EIGEN_REPO:
            u.afwijkingen.append("een manifest van deze repo staat onder kopieen: los het op in het manifest zelf")
        if set(waarsch) != verwacht:
            extra = sorted(set(waarsch) - verwacht)
            weg = sorted(verwacht - set(waarsch))
            if extra:
                u.afwijkingen.append(f"onverwachte waarschuwing(en) in --strict: {extra}")
            if weg:
                u.afwijkingen.append(f"uitzondering(en) niet meer nodig, haal ze weg: {weg}")
        if not verwacht and res.returncode != 0:
            u.afwijkingen.append(f"--strict gaf exitcode {res.returncode}")
        u.afwijkingen.extend(eigen_controles(data))
        uitkomsten.append(u)
    for app_id in sorted(set(uitzonderingen) - gezien):
        uitkomsten.append(Uitkomst("-", app_id, 0, 0, [], [f"uitzondering voor onbekend manifest {app_id}"]))
    return uitkomsten


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Installeerbare manifesten controleren (jl-manifest --strict)")
    parser.add_argument("--validator")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        validator = validator_pad(args.validator)
    except FileNotFoundError as exc:
        print(f"FOUT: {exc}", file=sys.stderr)
        return 2
    uitkomsten = controleer(validator)
    if args.json:
        print(json.dumps([u.__dict__ | {"in_orde": u.in_orde} for u in uitkomsten], ensure_ascii=False, indent=2))
    else:
        for u in uitkomsten:
            status = "OK  " if u.in_orde else "FOUT"
            extra = f" (toegestane waarschuwingen: {[c for c, _ in u.waarschuwingen]})" if u.waarschuwingen else ""
            print(f"{status} {u.bestand}: strict-exit {u.strict_exit}, {u.fouten} fout(en){extra}")
            for a in u.afwijkingen:
                print(f"     - {a}")
    return 0 if all(u.in_orde for u in uitkomsten) else 1


if __name__ == "__main__":
    raise SystemExit(main())
