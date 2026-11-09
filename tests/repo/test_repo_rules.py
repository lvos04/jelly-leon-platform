"""Repo-brede controles: manifest (contract §9), Dockerfiles (gepinde images, geen ingebakken CA), installatiedocs
(geen YAML-methode, geen voorbeeld-NAS-gegevens), versies consistent en EVIDENCE.json (contract §11)."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "deploy/manifest/jelly-leon-platform.manifest.yaml"
APPSTORE_SCHEMA = ROOT.parent / "appstore-en-fork/apps/api/src/appstore/manifest/manifest-v1.schema.json"
FORBIDDEN_HOST_PATHS = ("/", "/mnt", "/etc", "/var/run", "/root", "/var/run/docker.sock")


def _walk_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [*value.keys(), *[k for v in value.values() for k in _walk_keys(v)]]
    if isinstance(value, list):
        return [k for v in value for k in _walk_keys(v)]
    return []


def load_manifest() -> dict[str, Any]:
    data = yaml.safe_load(MANIFEST.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def test_manifest_rules_section_9() -> None:
    m = load_manifest()
    assert m["manifest_version"] == "1" and m["app_id"] == "jelly-leon-platform"
    assert m["install"] == {"method": "custom_app"}
    assert "compose" not in _walk_keys(m) and "custom_compose_config" not in _walk_keys(m)
    assert m["image"]["repository"] == "ghcr.io/lvos04/jelly-leon-platform"
    assert m["image"]["tag"] != "latest" and re.fullmatch(r"\d+\.\d+\.\d+", m["image"]["tag"])
    assert m["image"]["digest"] is None or re.fullmatch(r"sha256:[0-9a-f]{64}", m["image"]["digest"])
    assert m["privileges"] == {"privileged": False, "cap_add": [], "devices": [], "gpu": "none"}
    assert m["network"]["host_network"] is False
    ports = {p["container_port"] for p in m["custom_app"]["ports"]}
    assert ports == {30440}
    for st in m["custom_app"]["storage"]:
        hp = st.get("host_path")
        assert not isinstance(hp, str) or hp.rstrip("/") not in FORBIDDEN_HOST_PATHS
    assert {s["mount"] for s in m["storage"]} == {"/appdata"}
    params = {p["name"]: p for p in m["parameters"]}
    for ref in re.findall(r"\{param: (\w+)\}", MANIFEST.read_text()):
        assert ref in params, f"parameter {ref} niet gedeclareerd"
    for p in params.values():
        assert "example" not in p or p["source"] != "default", "voorbeeldwaarde als echte waarde"
    assert params["appdata_path"]["source"] == "dataset_picker"
    assert params["hub_url"]["source"] == "wizard"
    assert m["upgrade"]["tag_only_rollback_safe"] is False


@pytest.mark.skipif(not APPSTORE_SCHEMA.exists(), reason="schema van appstore-en-fork niet naast deze repo")
def test_manifest_valid_against_appstore_schema() -> None:
    import jsonschema

    schema = json.loads(APPSTORE_SCHEMA.read_text(encoding="utf-8"))
    errors = [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(load_manifest())]
    assert errors == []


def test_versions_consistent() -> None:
    version = (ROOT / "VERSION").read_text().strip()
    pyproject = tomllib.loads((ROOT / "apps/api/pyproject.toml").read_text())
    init = (ROOT / "apps/api/src/lifecycle/__init__.py").read_text()
    assert pyproject["project"]["version"] == version
    assert f'__version__ = "{version}"' in init
    assert load_manifest()["version"] == version and load_manifest()["image"]["tag"] == version
    assert f"ARG VERSION={version}" in (ROOT / "deploy/allinone/Dockerfile").read_text()


@pytest.mark.parametrize("dockerfile", ["deploy/allinone/Dockerfile", "e2e/simulators/truenas/Dockerfile"])
def test_dockerfiles_pinned_and_no_baked_ca(dockerfile: str) -> None:
    text = (ROOT / dockerfile).read_text()
    images = re.findall(r"^ARG \w+_IMAGE=(\S+)", text, re.M)
    assert images, "base-images horen als ARG met digest te staan"
    for image in images:
        assert re.search(r":\d[\w.-]*@sha256:[0-9a-f]{64}$", image), image
        assert ":latest" not in image
    for line in re.findall(r"^FROM (\S+)", text, re.M):
        assert line.startswith("${"), f"FROM zonder gepinde ARG: {line}"
    assert "ca-bundle" not in text and "/root/.ccr" not in text
    assert "--mount=type=secret,id=ca_bundle,required=false" in text
    assert "--require-hashes" in text


def test_compose_only_for_development() -> None:
    text = (ROOT / "deploy/dev/compose.yaml").read_text()
    assert "ALLEEN VOOR ONTWIKKELING" in text and "GEEN installatiemethode" in text
    assert ":latest" not in text


IP = re.compile(r"\b(?!127\.0\.0\.1\b)(?!0\.0\.0\.0\b)\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")


@pytest.mark.parametrize("doc", ["install/truenas/INSTALLATIE.md", "deploy/truenas/CUSTOM_APP.md",
                                 "install/manifests/README.md", ".env.example"])
def test_install_docs_no_example_nas_values(doc: str) -> None:
    text = (ROOT / doc).read_text(encoding="utf-8")
    assert not IP.search(text), f"concreet IP-adres in {doc}"
    assert not re.search(r"\btank\b", text), f"voorbeeldpoolnaam in {doc}"
    assert "docker compose up" not in text and "docker run" not in text


def test_install_docs_never_yaml_method() -> None:
    for doc in ("install/truenas/INSTALLATIE.md", "deploy/truenas/CUSTOM_APP.md"):
        for line in (ROOT / doc).read_text(encoding="utf-8").splitlines():
            if "Install via YAML" in line or "YAML" in line:
                assert re.search(r"(niet|geen|nooit|verboden|zonder|Geen|Niet|Nooit|Zonder)", line), f"{doc}: {line}"


def test_no_model_identifiers_in_own_files() -> None:
    pattern = re.compile(r"(claude-(opus|sonnet|haiku)|opus-\d|sonnet-\d|haiku-\d|gpt-\d)", re.I)
    skip = {".git", ".venv", "platform-v2", "__pycache__", "node_modules"}
    for path in ROOT.rglob("*"):
        if path.is_dir() or any(part in skip for part in path.parts) or path.suffix in {".pyc", ".lock"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        assert not pattern.search(text), f"model-ID in {path.relative_to(ROOT)}"


STATUS_WORK = {"Niet gestart", "Bezig", "Gereed", "Geblokkeerd", "N.v.t."}
STATUS_TEST = {"Niet gestart", "Bezig", "Geslaagd", "Mislukt", "Geblokkeerd", "N.v.t."}
STATUS_DECISION = {"Open", "Ingevuld", "Getest", "Geblokkeerd", "N.v.t."}


def test_evidence_json_follows_section_11() -> None:
    evidence = json.loads((ROOT / "docs/platform-v2/EVIDENCE.json").read_text(encoding="utf-8"))
    tasks = {t["ID"] for t in json.loads((ROOT / "docs/platform-v2/EXCEL_TAKEN.json").read_text(encoding="utf-8"))}
    assert isinstance(evidence, list) and evidence
    seen: set[str] = set()
    for row in evidence:
        assert set(row) == {"id", "status", "validatie", "bewijs", "blokkade", "opmerking", "repo", "bijgewerkt"}
        assert row["id"] not in seen
        seen.add(row["id"])
        assert row["repo"] == "jelly-leon-platform"
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["bijgewerkt"])
        if row["id"].startswith("EX"):
            assert row["status"] in STATUS_TEST - {"Geslaagd"}
        elif re.fullmatch(r"D\d{2}", row["id"]):
            assert row["status"] in STATUS_DECISION
        else:
            assert row["id"] in tasks, f"onbekende Excel-ID {row['id']}"
            assert row["status"] in STATUS_WORK - {"Gereed"}
        assert row["status"] not in {"Gereed", "Geslaagd"}
        if row["status"] == "Geblokkeerd":
            assert row["blokkade"], f"{row['id']}: blokkade zonder reden"
