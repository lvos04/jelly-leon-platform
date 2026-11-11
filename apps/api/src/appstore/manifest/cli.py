"""``jl-manifest`` — manifest v1 valideren, vertalen en ondertekenen vanaf de commandoregel (ontwikkeling/CI).

    jl-manifest validate <bestand> [--format text|json] [--strict]
    jl-manifest checklist <bestand> [--format text|json]     (kind: checklist, contract 1.3 §9)
    jl-manifest translate <bestand> --params <params.json> --truenas-version X --ix-app-version Y [--app-name N]
    jl-manifest sign <bestand> --key <ed25519.pem>          (schrijft <bestand>.sig)
    jl-manifest verify <bestand> --public-key <sleutel>

Exitcodes: 0 = geldig/gereed, 1 = ongeldig/niet gereed, 2 = gebruiks- of leesfout.
De installatie zelf loopt nooit via deze CLI: die gaat via de Hub en de executor (TrueNAS-API).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .loader import load_manifest_text
from .provenance import load_public_key, sign_data, verify_data
from .translate import Target, translate
from .validator import validate_text


def _read(path: str) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        print(f"FOUT: kan {path} niet lezen: {exc.strerror}", file=sys.stderr)
        raise SystemExit(2) from exc


def _cmd_validate(args: argparse.Namespace) -> int:
    report, _, _ = validate_text(_read(args.file))
    ok = report.strict_valid if args.strict else report.valid
    if args.format == "json":
        out: dict[str, Any] = report.model_dump(mode="json")
        out["file"] = args.file
        out["strict"] = args.strict
        out["result"] = "geldig" if ok else "ongeldig"
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"{args.file}")
        print(report.as_text())
        if args.strict and report.valid and not report.strict_valid:
            print(
                "Ongeldig in --strict-modus: elke waarschuwing die niet met reden in accepted_findings staat, telt "
                "als fout (contract 1.3 §9)."
            )
    return 0 if ok else 1


def _cmd_checklist(args: argparse.Namespace) -> int:
    from .checklist import validate_checklist_text

    report = validate_checklist_text(_read(args.file))
    if args.format == "json":
        out: dict[str, Any] = report.model_dump(mode="json")
        out["file"] = args.file
        out["result"] = "geldig" if report.valid else "ongeldig"
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(args.file)
        print(report.as_text().replace("Manifest ", "Controlelijst ", 1))
    return 0 if report.valid else 1


def _cmd_translate(args: argparse.Namespace) -> int:
    report, manifest, _ = validate_text(_read(args.file))
    if manifest is None:
        print(report.as_text(), file=sys.stderr)
        print("Vertalen geweigerd: het manifest is ongeldig.", file=sys.stderr)
        return 1
    params: dict[str, Any] = {}
    if args.params:
        try:
            params = json.loads(_read(args.params))
        except json.JSONDecodeError:
            print("FOUT: parameterbestand is geen geldige JSON.", file=sys.stderr)
            return 2
        if not isinstance(params, dict):
            print("FOUT: parameterbestand moet een JSON-object zijn.", file=sys.stderr)
            return 2
    result = translate(
        manifest,
        params,
        Target(truenas_version=args.truenas_version, ix_app_version=args.ix_app_version),
        app_name=args.app_name,
        manifest_sha256=report.manifest_sha256,
    )
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0 if result.ready_for_install else 1


def _cmd_sign(args: argparse.Namespace) -> int:
    data, findings = load_manifest_text(_read(args.file))
    if findings:
        print(findings[0].as_text(), file=sys.stderr)
        return 1
    key = serialization.load_pem_private_key(_read(args.key), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        print("FOUT: sleutel is geen Ed25519-privésleutel.", file=sys.stderr)
        return 2
    Path(args.file + ".sig").write_text(sign_data(key, data) + "\n", encoding="utf-8")
    print(f"Handtekening geschreven: {args.file}.sig")
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    data, findings = load_manifest_text(_read(args.file))
    if findings:
        print(findings[0].as_text(), file=sys.stderr)
        return 1
    try:
        key = load_public_key(_read(args.public_key).decode())
    except ValueError as exc:
        print(f"FOUT: publieke sleutel ongeldig: {exc}", file=sys.stderr)
        return 2
    sig = _read(args.file + ".sig").decode()
    if verify_data(key, data, sig):
        print("Handtekening klopt.")
        return 0
    print("Handtekening klopt NIET.")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jl-manifest", description="Manifeststandaard v1 (B08-001) — valideren en vertalen."
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("validate", help="valideer een manifest")
    p.add_argument("file")
    p.add_argument("--format", choices=("text", "json"), default="text")
    p.add_argument("--strict", action="store_true", help="waarschuwingen tellen als fout")
    p.set_defaults(func=_cmd_validate)
    c = sub.add_parser("checklist", help="valideer een controlelijst (kind: checklist, I-document zonder installatie)")
    c.add_argument("file")
    c.add_argument("--format", choices=("text", "json"), default="text")
    c.set_defaults(func=_cmd_checklist)
    t = sub.add_parser("translate", help="vertaal naar TrueNAS app.create-waarden (voert niets uit)")
    t.add_argument("file")
    t.add_argument("--params", help="JSON-bestand met parameterwaarden")
    t.add_argument("--truenas-version", default=None)
    t.add_argument("--ix-app-version", default=None)
    t.add_argument("--app-name", default=None)
    t.set_defaults(func=_cmd_translate)
    s = sub.add_parser("sign", help="onderteken een manifest (uitgever)")
    s.add_argument("file")
    s.add_argument("--key", required=True)
    s.set_defaults(func=_cmd_sign)
    v = sub.add_parser("verify", help="controleer de uitgever-handtekening")
    v.add_argument("file")
    v.add_argument("--public-key", required=True)
    v.set_defaults(func=_cmd_verify)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    code: int = args.func(args)
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
