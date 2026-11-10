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
    # contract 1.3 §9 (C1.3-JLP-09): kop 1.3, monitorplan op /readyz, digest_pending_release alleen in het manifest
    assert "platformcontract 1.3 §9" in MANIFEST.read_text(encoding="utf-8").splitlines()[0]
    assert m["healthcheck"]["monitor"] == {"pad": "/readyz", "verwachte_status": 200, "timeout_s": 10, "interval_s": 60}
    accepted = {f["code"] for f in m.get("accepted_findings", [])}
    assert accepted == ({"digest_pending_release"} if m["image"]["digest"] is None else set())
    assert not [p for p in params if p.upper().startswith("HUB_")]


@pytest.mark.skipif(not APPSTORE_SCHEMA.exists(), reason="schema van appstore-en-fork niet naast deze repo")
def test_manifest_valid_against_appstore_schema() -> None:
    import jsonschema

    schema = json.loads(APPSTORE_SCHEMA.read_text(encoding="utf-8"))
    errors = [e.message for e in jsonschema.Draft202012Validator(schema).iter_errors(load_manifest())]
    assert errors == []


def _jl_manifest() -> str | None:
    import os

    for kandidaat in (os.environ.get("JL_MANIFEST"), str(ROOT / ".venv-validator/bin/jl-manifest"),
                      str(ROOT.parent / "appstore-en-fork/.venv/bin/jl-manifest")):
        if kandidaat and Path(kandidaat).is_file():
            return kandidaat
    return None


def test_manifest_passes_the_appstore_validator() -> None:
    """De validator van de eigenaar van de manifeststandaard (contract 1.2 §9) moet het manifest accepteren, ook in
    --strict; alleen de digest ontbreekt nog tot de release-CI hem invult (digest_pending_release)."""
    import os
    import subprocess

    validator = _jl_manifest()
    if validator is None:
        if os.environ.get("MANIFEST_VALIDATOR_VERPLICHT") == "1":
            pytest.fail("jl-manifest niet gevonden (JL_MANIFEST)")
        pytest.skip("jl-manifest (appstore-en-fork) niet beschikbaar; in CI verplicht")
    r = subprocess.run([validator, "validate", "--strict", str(MANIFEST), "--format", "json"], capture_output=True,
                       text=True, check=False)
    report = json.loads(r.stdout)
    assert report["valid"] is True and report["errors"] == 0, report
    # 1.3: de enige melding is geaccepteerd in het manifest zelf (accepted_findings) en --strict slaagt
    assert {f["code"] for f in report["findings"]} <= {"digest_pending_release"}, report
    assert all(f.get("accepted") for f in report["findings"]) and r.returncode == 0, report


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


def test_bounding_set_and_healthcheck_switch_first_section_2_2() -> None:
    """Contract 1.3 §2.2 (C1.3-JLP-06): bewaker en healthcheck legen bij root eerst de bounding set, wisselen naar 568
    met no_new_privs en controleren pas daarna; de healthcheck staat in exec-vorm en heeft een controlevlag. De
    image-E2E (tests/e2e/test_allinone_image.py) meet het in een draaiende container."""
    supervisor = (ROOT / "deploy/allinone/supervisor.py").read_text()
    demote = supervisor[supervisor.index("def demote()"):supervisor.index("def own_status()")]
    assert demote.index("drop_bounding_set()") < demote.index("os.setuid(UID)")
    assert "PR_CAPBSET_DROP = 24" in supervisor and "cap_last_cap" in supervisor and 'st.get("CapBnd")' in supervisor
    health = (ROOT / "deploy/allinone/healthcheck.py").read_text()
    wissel = health.index("started_as_root = wissel()")
    assert wissel < health.index("\"--controle\" in sys.argv") and wissel < health.index("127.0.0.1:{port}/readyz")
    assert "prctl(24, cap" in health and "prctl(38, 1" in health
    docker = (ROOT / "deploy/allinone/Dockerfile").read_text()
    cmd = re.search(r"^  CMD (\[.*\])$", docker, re.M)
    assert cmd and json.loads(cmd.group(1)) == ["python", "/usr/local/lib/lifecycle-allinone/healthcheck.py"]


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
# contract 1.3 §11 (A33): vaste lijst; meerdere waarden gescheiden door "; "; toelichting hoort in 'opmerking'
VALIDATIE = {"Sim-getest", "Unit-getest", "Intel-iGPU-getest (ontwikkelmachine)", "NIET GEVALIDEERD (echte NAS)",
             "NIET GEVALIDEERD (hardware)", "NIET GEVALIDEERD (account)", "NIET GEVALIDEERD (Mac/Xcode)",
             "Sim-geslaagd", "Sim-mislukt", "Niet getest"}
NIET_GEVALIDEERD = re.compile(r"NIET GEVALIDEERD \([^)]*\)")
CONTRACTPUNT = re.compile(r"C1\.3-JLP-\d\d")
REVIEW = re.compile(r"REVIEW-jelly-leon-platform-[a-z0-9][a-z0-9.-]*")


def _evidence() -> list[dict[str, Any]]:
    rows = json.loads((ROOT / "docs/platform-v2/EVIDENCE.json").read_text(encoding="utf-8"))
    assert isinstance(rows, list) and rows
    return rows


def _contractpunten() -> set[str]:
    lijst = (ROOT / "docs/platform-v2/CONTRACT_1.3_CONFORMITEIT.md").read_text(encoding="utf-8")
    return set(re.findall(r"^\| (C1\.3-JLP-\d\d) \|", lijst, re.M))


def check_evidence_row(row: dict[str, Any], tasks: set[str], punten: set[str]) -> None:
    """Contract 1.3 §11 voor één regel (ook gebruikt door de negatieve test hieronder)."""
    rid = row["id"]
    assert set(row) == {"id", "status", "validatie", "bewijs", "blokkade", "opmerking", "repo", "bijgewerkt"}, rid
    assert row["repo"] == "jelly-leon-platform", rid
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["bijgewerkt"]), rid
    testmatrix = bool(re.fullmatch(r"(EX\d{2}|JLT-\d+)", rid))
    if testmatrix:
        assert row["status"] in STATUS_TEST - {"Geslaagd"}, rid
    elif re.fullmatch(r"D\d{2}", rid):
        assert row["status"] in STATUS_DECISION, rid
    elif rid.startswith("C1."):
        assert CONTRACTPUNT.fullmatch(rid) and rid in punten, f"onbekend contractpunt {rid}"
        assert row["status"] in STATUS_WORK - {"Gereed"}, rid
    elif rid.startswith("REVIEW-"):
        assert REVIEW.fullmatch(rid), f"reviewregel {rid}: REVIEW-jelly-leon-platform-<onderwerp>"
        assert row["status"] in STATUS_WORK - {"Gereed"}, rid
    else:
        assert rid in tasks, f"onbekende Excel-ID {rid}"
        assert row["status"] in STATUS_WORK - {"Gereed"}, rid
    assert row["status"] not in {"Gereed", "Geslaagd"}, rid
    assert row["validatie"].strip(), f"{rid}: een lege validatie is nooit toegestaan"
    values = row["validatie"].split("; ")
    assert all(v in VALIDATIE for v in values), f"{rid}: vrije tekst in validatie"
    assert len(set(values)) == len(values), f"{rid}: dubbele validatiewaarde"
    if testmatrix:
        assert not set(values) & {"Sim-getest", "Unit-getest"}, f"{rid}: testmatrix gebruikt Sim-geslaagd/Sim-mislukt"
    else:
        assert not set(values) & {"Sim-geslaagd", "Sim-mislukt"}, f"{rid}: alleen voor de testmatrix"
    if "Niet getest" in values:
        assert row["status"] == "Niet gestart", f"{rid}: 'Niet getest' alleen bij 'Niet gestart'"
    for gevonden in NIET_GEVALIDEERD.findall(row["blokkade"]):
        assert gevonden in values, f"{rid}: {gevonden!r} staat in blokkade maar niet in validatie"
    if row["status"] == "Geblokkeerd":
        assert row["blokkade"], f"{rid}: blokkade zonder reden"


def test_evidence_json_follows_section_11() -> None:
    tasks = {t["ID"] for t in json.loads((ROOT / "docs/platform-v2/EXCEL_TAKEN.json").read_text(encoding="utf-8"))}
    punten = _contractpunten()
    assert len(punten) == 14, punten
    seen: set[str] = set()
    for row in _evidence():
        assert row["id"] not in seen, row["id"]
        seen.add(row["id"])
        check_evidence_row(row, tasks, punten)
    # elk contractpunt dat NU moet, heeft een regel (ook als het geblokkeerd is of nog niet gestart)
    assert {f"C1.3-JLP-{n:02d}" for n in range(1, 14)} <= seen


@pytest.mark.parametrize("fout", [
    {"validatie": ""},
    {"validatie": "Sim-getest; ok"},
    {"status": "Gereed"},
    {"status": "Bezig", "validatie": "Niet getest"},
    {"status": "Geblokkeerd", "validatie": "Sim-getest", "blokkade": "NIET GEVALIDEERD (echte NAS): pool nodig."},
    {"id": "C1.3-JLP-99"},
    {"id": "REVIEW-andere-repo-contract"},
    {"validatie": "Sim-geslaagd"},
])
def test_evidence_check_rejects_section_11_violations(fout: dict[str, str]) -> None:
    """Negatief: de controle hierboven vangt elke regel van §11 (1.3) af."""
    goed = {"id": "C1.3-JLP-13", "status": "Bezig", "validatie": "Unit-getest", "bewijs": "x", "blokkade": "",
            "opmerking": "", "repo": "jelly-leon-platform", "bijgewerkt": "2026-11-10"}
    check_evidence_row(goed, set(), _contractpunten())
    with pytest.raises(AssertionError):
        check_evidence_row({**goed, **fout}, set(), _contractpunten())
