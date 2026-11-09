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
    assert m["custom_app"]["gpu"] == "none"  # geen GPU; dus ook geen gpu-parameter (contract 1.2 §9)
    assert m["network"]["host_network"] is False
    # contract 1.2 §9: network.ports is de enige bron; joins_platform_network en custom_app.ports vervallen
    assert "joins_platform_network" not in m["network"] and "ports" not in m["custom_app"]
    assert {p["container_port"] for p in m["network"]["ports"]} == {30440}
    assert m["custom_app"]["portal"]["port"] in {p["name"] for p in m["network"]["ports"]}
    for st in m["custom_app"]["storage"]:
        hp = st.get("host_path")
        assert not isinstance(hp, str) or hp.rstrip("/") not in FORBIDDEN_HOST_PATHS
    assert {s["mount"] for s in m["storage"]} == {"/appdata"}
    params = {p["name"]: p for p in m["parameters"]}
    for ref in re.findall(r"\{param: (\w+)\}", MANIFEST.read_text()):
        assert ref in params, f"parameter {ref} niet gedeclareerd"
    for p in params.values():
        assert "example" not in p or p["source"] != "default", "voorbeeldwaarde als echte waarde"
    assert params["appdata_path"]["source"] == "dataset_picker" and params["appdata_path"]["type"] == "path"
    # HUB_* geeft de Hub zelf mee bij app.install/app.update (§3.2, §8); nooit een token in het manifest
    assert m["custom_app"]["env"] == []
    assert all(p["source"] not in {"secret", "generated"} for p in params.values())
    assert m["validation_status"] == "unvalidated"
    assert m["upgrade"]["tag_only_rollback_safe"] is False


@pytest.mark.skipif(not APPSTORE_SCHEMA.exists(), reason="schema van appstore-en-fork niet naast deze repo")
def test_manifest_valid_against_appstore_schema() -> None:
    import jsonschema

    schema = json.loads(APPSTORE_SCHEMA.read_text(encoding="utf-8"))
    errors = [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(load_manifest())]
    assert errors == []


APPSTORE_PY = ROOT.parent / "appstore-en-fork/.venv/bin/python"


@pytest.mark.skipif(not APPSTORE_PY.exists(), reason="appstore-en-fork (met venv) niet naast deze repo")
def test_manifest_passes_the_appstore_validator() -> None:
    """De validator van de eigenaar van de manifeststandaard (contract 1.2 §9) moet het manifest accepteren; alleen de
    digest ontbreekt nog tot de release-CI hem invult."""
    import subprocess

    r = subprocess.run([str(APPSTORE_PY), "-m", "appstore.manifest.cli", "validate", str(MANIFEST), "--format",
                        "json"], capture_output=True, text=True, check=False, cwd=ROOT.parent / "appstore-en-fork")
    report = json.loads(r.stdout)
    assert report["valid"] is True and report["errors"] == 0, report
    assert {f["code"] for f in report["findings"]} <= {"digest_pending_release"}, report


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


def test_supervisor_is_pid1_without_root_init() -> None:
    """Contract 1.2 §2.2/R11 (A6, JP12): geen init die als root blijft draaien (dumb-init/tini); de procesbewaker is
    zelf PID 1 en wisselt naar 568. De image-E2E controleert dat voor elk proces in een draaiende container."""
    text = (ROOT / "deploy/allinone/Dockerfile").read_text()
    entry = re.findall(r"^ENTRYPOINT (\[.*\])$", text, re.M)
    assert len(entry) == 1 and json.loads(entry[0]) == ["python", "/usr/local/lib/lifecycle-allinone/supervisor.py"]
    lock = (ROOT / "apps/api/requirements.lock").read_text()
    assert "dumb-init" not in lock and not re.search(r"^tini", lock, re.M)
    supervisor = (ROOT / "deploy/allinone/supervisor.py").read_text()
    assert "def reap_orphans" in supervisor and "def verify_privileges" in supervisor


@pytest.mark.parametrize("workflow", ["ci.yml", "release.yml"])
def test_workflows_are_valid_yaml_without_secrets_or_latest(workflow: str) -> None:
    """Review P0: ci.yml was ongeldige YAML (dubbele punt in een stapnaam) en zou op GitHub niet starten."""
    text = (ROOT / ".github/workflows" / workflow).read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert isinstance(doc, dict) and isinstance(doc.get("jobs"), dict) and doc["jobs"]
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            assert isinstance(step, dict) and ("run" in step or "uses" in step), step
            uses = str(step.get("uses", ""))
            assert not uses or re.search(r"@[0-9a-f]{40}$", uses), f"action niet op commit-SHA: {uses}"
    assert ":latest" not in text and "ca-bundle" not in text


def test_release_publishes_only_on_tags_and_never_overwrites() -> None:
    """Review P0-afronding: workflow_dispatch op een branch pushte de versie-tag naar GHCR (en kon een bestaande
    release overschrijven). Publiceren alleen bij een tag; een bestaande versie-tag in GHCR breekt de run af."""
    doc = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    steps = doc["jobs"]["publish"]["steps"]
    publishing = [
        st for st in steps
        if "login-action" in str(st.get("uses", "")) or "sbom-action" in str(st.get("uses", ""))
        or "upload-artifact" in str(st.get("uses", "")) or "docker push" in str(st.get("run", ""))
        or "gh release" in str(st.get("run", ""))
    ]
    assert len(publishing) == 5, [st.get("name") or st.get("uses") for st in publishing]
    for st in publishing:
        assert st.get("if") == "github.ref_type == 'tag'", st.get("name") or st.get("uses")
    push = next(st for st in publishing if "docker push" in str(st.get("run", "")))
    run = push["run"]
    assert run.index("imagetools inspect") < run.index("docker push") and "bestaat al" in run


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
    skip = {".git", ".venv", ".venv-validator", "platform-v2", "__pycache__", "node_modules", ".peil-bron"}
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
# contract 1.2 §11 (A4): vaste lijst; meerdere waarden gescheiden door "; "; toelichting hoort in 'opmerking'
VALIDATIE = {"Sim-getest", "Unit-getest", "NIET GEVALIDEERD (echte NAS)", "NIET GEVALIDEERD (hardware)",
             "NIET GEVALIDEERD (account)", "NIET GEVALIDEERD (Mac/Xcode)", "Sim-geslaagd", "Sim-mislukt"}


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
        values = row["validatie"].split("; ")
        assert values and all(v in VALIDATIE for v in values), f"{row['id']}: vrije tekst in validatie"
        assert len(set(values)) == len(values), f"{row['id']}: dubbele validatiewaarde"
        if row["id"].startswith("EX"):
            assert not set(values) & {"Sim-getest"}, f"{row['id']}: testmatrix gebruikt Sim-geslaagd/Sim-mislukt"
        else:
            assert not set(values) & {"Sim-geslaagd", "Sim-mislukt"}, f"{row['id']}: alleen voor de testmatrix"
        if row["status"] == "Geblokkeerd":
            assert row["blokkade"], f"{row['id']}: blokkade zonder reden"
