"""Documentsoort ``kind: checklist`` (platformcontract 1.3 §9) voor I-documenten zonder installatie (bv. I01, I02).

Eigen JSON Schema (``checklist-v1.schema.json``, kopie in ``schemas/``); de validator van manifest v1 weigert zo'n
document als installatie (``checklist_not_installable``). Semantische regels hier:

* elke stap-ID hoort bij het document (``I01-…`` bij ``id: I01``) en komt één keer voor;
* de besluiten van een stap staan ook in ``besluiten`` van het document;
* een stap met ``meting_truenas_api`` noemt de alleen-lezen TrueNAS-methoden; een methode die iets wijzigt
  (``.create``, ``.update``, ``.delete``, ``.start``, ``.stop``, …) is verboden: het platform leest alleen;
* geen voorbeeldwaarden als echte waarde (IP-adressen, poolnamen zoals ``tank``) in de teksten.

Hergebruikt :class:`~appstore.manifest.validator.ValidationReport` en de foutvertaling van het manifestschema.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator

from .findings import Finding, error
from .loader import load_manifest_text
from .validator import ValidationReport, _path, build_report

SCHEMA_FILE = "checklist-v1.schema.json"
_MUTATING = re.compile(r"\.(create|update|delete|start|stop|restart|upgrade|rollback|set_|do_|run|import)\w*$")
_EXAMPLE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b|/mnt/tank\b|\btank/")


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    text = resources.files("appstore.manifest").joinpath(SCHEMA_FILE).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    Draft202012Validator.check_schema(data)
    return data


def _schema_findings(data: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    for err in sorted(Draft202012Validator(schema()).iter_errors(data), key=lambda e: list(map(str, e.absolute_path))):
        kw = str(err.validator)
        code = {
            "required": "required_field_missing",
            "additionalProperties": "unknown_field",
            "type": "wrong_type",
            "enum": "invalid_value",
            "const": "invalid_value",
            "pattern": "invalid_format",
        }.get(kw, "invalid_value")
        out.append(error(code, _path(err), f"Controlelijst: {err.message}"))
    return out


def _semantic_findings(data: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    doc_id = str(data["id"])
    besluiten = set(data.get("besluiten") or [])
    seen: set[str] = set()
    for idx, stap in enumerate(data["stappen"]):
        path = f"stappen[{idx}]"
        sid = str(stap["id"])
        if not sid.startswith(f"{doc_id}-"):
            out.append(error("invalid_value", f"{path}.id", f"Stap {sid!r} hoort niet bij document {doc_id}."))
        if sid in seen:
            out.append(error("duplicate_name", f"{path}.id", f"Stap {sid!r} komt dubbel voor."))
        seen.add(sid)
        missing = sorted(set(stap.get("besluiten") or []) - besluiten)
        if missing:
            out.append(
                error("decision_not_listed", f"{path}.besluiten", f"Besluiten {missing} staan niet in 'besluiten'.")
            )
        methoden = stap.get("truenas_methoden") or []
        if "meting_truenas_api" in stap["wijze"] and not methoden:
            out.append(
                error(
                    "required_field_missing",
                    f"{path}.truenas_methoden",
                    "Een meting via de TrueNAS-API noemt de alleen-lezen methoden die de Hub gebruikt.",
                )
            )
        for jdx, m in enumerate(methoden):
            if _MUTATING.search(str(m["naam"])):
                out.append(
                    error(
                        "install_method_forbidden",
                        f"{path}.truenas_methoden[{jdx}].naam",
                        f"{m['naam']} wijzigt iets op de NAS; een controlelijst leest alleen.",
                    )
                )
        for key in ("uitvoeren_nl", "acceptatie_nl", "bij_fout_nl", "handmatig_ui_pad_nl"):
            text = str(stap.get(key) or "")
            if _EXAMPLE.search(text):
                out.append(
                    error(
                        "example_value_as_real", f"{path}.{key}", "Voorbeeldwaarde (IP-adres of poolnaam) in de tekst."
                    )
                )
    return out


def validate_checklist_data(data: Any) -> ValidationReport:
    """Valideer een controlelijst (al ingelezen). ``valid`` alleen zonder fouten; geen waarschuwingen."""
    if not isinstance(data, dict):
        return build_report(None, [error("manifest_not_mapping", "", "De controlelijst moet een object zijn.")], None)
    findings = _schema_findings(data)
    if not findings:
        findings = _semantic_findings(data)
    report = build_report(data, findings, None)
    return report.model_copy(update={"app_id": str(data.get("id")) if data.get("id") else None})


def validate_checklist_text(text: str | bytes) -> ValidationReport:
    data, load_findings = load_manifest_text(text)
    if load_findings:
        return build_report(None, load_findings, None)
    return validate_checklist_data(data)
