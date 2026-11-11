"""Validator van manifest v1: veilige lader → compose-detectie → JSON Schema → getypt model → semantische regels.

Uitkomst is een :class:`ValidationReport` met machineleesbare codes en Nederlandse uitleg. ``valid`` is alleen
``True`` als er géén fouten zijn; waarschuwingen blokkeren niet. ``strict_valid`` (release-CI, ``--strict``) vraagt
bovendien dat elke waarschuwing met reden in ``accepted_findings`` staat (contract 1.3 §9: alleen
``backup_db_export_none``, ``secret_in_form`` bij een geheim dat de Hub maakt, en ``digest_pending_release``).
Documentsoorten ``kind: adoption`` en ``kind: checklist`` worden hier geweigerd; ze hebben een eigen schema.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from importlib import resources
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaError
from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticError

from .. import __version__
from .canonical import canonical_json, sha256_digest
from .findings import Finding, Severity, error
from .loader import load_manifest_text
from .model import Manifest
from .rules import check_manifest

SCHEMA_FILE = "manifest-v1.schema.json"
_COMPOSE_KEYS = (
    "compose",
    "services",
    "docker_compose",
    "docker-compose",
    "custom_compose_config",
    "custom_compose_config_string",
    "compose_file",
    "x-compose",
)
_COMPOSE_METHODS = ("compose", "docker_compose", "yaml", "install_via_yaml", "custom_compose", "custom_yaml", "helm")


@lru_cache(maxsize=1)
def schema() -> dict[str, Any]:
    text = resources.files("appstore.manifest").joinpath(SCHEMA_FILE).read_text(encoding="utf-8")
    data: dict[str, Any] = json.loads(text)
    Draft202012Validator.check_schema(data)
    return data


@lru_cache(maxsize=1)
def schema_sha256() -> str:
    return sha256_digest(schema())


@lru_cache(maxsize=1)
def _validator() -> Draft202012Validator:
    return Draft202012Validator(schema())


class ValidationReport(BaseModel):
    """Machineleesbaar resultaat; ``as_text()`` geeft de Nederlandse weergave."""

    model_config = ConfigDict(frozen=True)

    valid: bool
    app_id: str | None
    version: str | None
    install_method: str | None
    manifest_sha256: str | None
    schema_sha256: str
    validator_version: str
    errors: int
    warnings: int
    #: contract 1.3 §9: waarschuwingen die het manifest met reden accepteert (vaste lijst)
    accepted: int = 0
    #: geldig in ``--strict`` (release-CI): geen fouten en geen niet-geaccepteerde waarschuwing
    strict_valid: bool = False
    findings: list[Finding]

    def codes(self) -> list[str]:
        return [f.code for f in self.findings]

    def as_text(self) -> str:
        head = (
            f"Manifest {self.app_id or '(onbekend)'} {self.version or ''}".rstrip()
            + (": GELDIG" if self.valid else ": ONGELDIG")
            + f" — {self.errors} fout(en), {self.warnings} waarschuwing(en)"
        )
        lines = [head]
        lines.extend("  " + f.as_text() for f in self.findings)
        if not self.findings:
            lines.append("  Geen bevindingen.")
        return "\n".join(lines)


def _path(err: JsonSchemaError) -> str:
    out = ""
    for part in err.absolute_path:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out


def _schema_findings(data: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    errors = sorted(_validator().iter_errors(data), key=lambda e: (list(map(str, e.absolute_path)), e.validator))
    for err in errors:
        path = _path(err)
        kw = str(err.validator)
        if kw == "required":
            missing = err.message.split("'")[1] if "'" in err.message else "?"
            findings.append(
                error(
                    "required_field_missing",
                    f"{path}.{missing}" if path else missing,
                    f"Verplicht veld {missing!r} ontbreekt.",
                )
            )
        elif kw == "additionalProperties":
            extra = (
                sorted(set(err.instance) - set(err.schema.get("properties", {})))
                if isinstance(err.instance, dict)
                else []
            )
            for name in extra or ["?"]:
                findings.append(
                    error(
                        "unknown_field",
                        f"{path}.{name}" if path else name,
                        f"Onbekend veld {name!r}; controleer de spelling.",
                    )
                )
        elif kw == "type":
            findings.append(error("wrong_type", path, f"Verkeerd type: verwacht {err.validator_value}."))
        elif kw in ("enum", "const"):
            allowed = err.validator_value if kw == "enum" else [err.validator_value]
            findings.append(
                error(
                    "invalid_value",
                    path,
                    f"Ongeldige waarde {err.instance!r}; toegestaan: {', '.join(map(repr, allowed))}.",
                )
            )
        elif kw in ("pattern", "format"):
            findings.append(error("invalid_format", path, f"Ongeldig formaat: {err.instance!r}."))
        elif kw in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "exclusiveMinimum"):
            findings.append(error("out_of_range", path, f"Waarde buiten bereik ({kw} {err.validator_value})."))
        elif kw == "uniqueItems":
            findings.append(error("duplicate_name", path, "Lijst bevat dubbele waarden."))
        elif kw == "not":
            findings.append(
                error(
                    "forbidden_combination",
                    path or "install",
                    "Deze velden mogen niet samen voorkomen met deze installatiemethode.",
                )
            )
        elif kw in ("oneOf", "anyOf"):
            findings.append(error("invalid_value", path, "Waarde past bij geen enkele toegestane vorm."))
        else:  # pragma: no cover - vangnet voor nieuwe schema-keywords
            findings.append(error("invalid_value", path, f"Ongeldige waarde ({kw})."))
    return findings


def _compose_findings(data: dict[str, Any]) -> list[Finding]:
    out: list[Finding] = []
    for key in _COMPOSE_KEYS:
        if key in data:
            out.append(
                error(
                    "compose_forbidden",
                    key,
                    "Installatie via compose/YAML is verboden; gebruik install.method 'custom_app' (één image, "
                    "formulierwaarden) of 'catalog_app'.",
                )
            )
    install = data.get("install")
    if isinstance(install, dict):
        method = str(install.get("method", "")).lower()
        if method in _COMPOSE_METHODS:
            out.append(
                error(
                    "compose_forbidden", "install.method", f"Installatiemethode {method!r} (compose/YAML) is verboden."
                )
            )
        for key in _COMPOSE_KEYS:
            if key in install:
                out.append(error("compose_forbidden", f"install.{key}", "Compose-configuratie is verboden."))
    custom = data.get("custom_app")
    if isinstance(custom, dict):
        for key in _COMPOSE_KEYS:
            if key in custom:
                out.append(error("compose_forbidden", f"custom_app.{key}", "Compose-configuratie is verboden."))
    return out


def build_report(data: dict[str, Any] | None, findings: list[Finding], manifest_sha: str | None) -> ValidationReport:
    findings = sorted(findings, key=lambda f: (f.severity is not Severity.ERROR, f.path, f.code))
    errors = sum(1 for f in findings if f.severity is Severity.ERROR)
    accepted = sum(1 for f in findings if f.severity is Severity.WARNING and f.accepted)
    open_warnings = len(findings) - errors - accepted
    install = data.get("install") if isinstance(data, dict) else None
    return ValidationReport(
        valid=errors == 0,
        app_id=str(data["app_id"]) if isinstance(data, dict) and isinstance(data.get("app_id"), str) else None,
        version=str(data["version"]) if isinstance(data, dict) and isinstance(data.get("version"), str) else None,
        install_method=str(install.get("method")) if isinstance(install, dict) and install.get("method") else None,
        manifest_sha256=manifest_sha,
        schema_sha256=schema_sha256(),
        validator_version=__version__,
        errors=errors,
        warnings=len(findings) - errors,
        accepted=accepted,
        strict_valid=errors == 0 and open_warnings == 0,
        findings=findings,
    )


def validate_data(data: Any) -> tuple[ValidationReport, Manifest | None]:
    """Valideer al ingelezen data. Geeft ``(rapport, model)``; model is ``None`` als het manifest ongeldig is."""
    if not isinstance(data, dict):
        return build_report(
            None, [error("manifest_not_mapping", "", "Het manifest moet een object met velden zijn.")], None
        ), None
    try:
        manifest_sha = "sha256:" + hashlib.sha256(canonical_json(data)).hexdigest()
    except (TypeError, ValueError):
        return build_report(
            data, [error("wrong_type", "", "Het manifest bevat waarden die geen JSON zijn.")], None
        ), None
    if data.get("kind") == "checklist":
        # contract 1.3 §9: documentsoort voor I-documenten zonder installatie, met een eigen schema
        return build_report(
            data,
            [
                error(
                    "checklist_not_installable",
                    "kind",
                    "Dit is een controlelijst (kind: checklist, install.method none) voor een I-document zonder "
                    "installatie. Valideer hem met 'jl-manifest checklist'; als installatie wordt hij geweigerd.",
                )
            ],
            manifest_sha,
        ), None
    if data.get("kind") == "adoption":
        # B08-002: een adoptiemanifest beschrijft een bestaande app zoals hij nu draait; het is nooit installeerbaar
        return build_report(
            data,
            [
                error(
                    "adoption_manifest_not_installable",
                    "kind",
                    "Dit is een adoptiemanifest van een bestaande app (alleen inventaris en beslisregister). Het "
                    "platform installeert of herinstalleert een app hiermee nooit.",
                )
            ],
            manifest_sha,
        ), None
    findings = _compose_findings(data)
    findings.extend(_schema_findings(data))
    if any(f.severity is Severity.ERROR for f in findings):
        return build_report(data, findings, manifest_sha), None
    try:
        manifest = Manifest.model_validate(data)
    except PydanticError as exc:  # schema en model horen gelijk te oordelen; zo niet: fout, nooit stil doorlaten
        findings.append(
            error(
                "model_mismatch", "", f"Intern: schema keurde goed maar het model niet ({exc.error_count()} fout(en))."
            )
        )
        return build_report(data, findings, manifest_sha), None
    findings.extend(check_manifest(manifest))
    report = build_report(data, findings, manifest_sha)
    return report, (manifest if report.valid else None)


def validate_text(text: str | bytes) -> tuple[ValidationReport, Manifest | None, dict[str, Any] | None]:
    """Valideer YAML/JSON-tekst. Geeft ``(rapport, model, data)``."""
    data, load_findings = load_manifest_text(text)
    if load_findings:
        return build_report(None, load_findings, None), None, None
    report, manifest = validate_data(data)
    return report, manifest, data
