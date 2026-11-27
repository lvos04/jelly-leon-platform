"""Repo-brede controles: manifest (contract §9), Dockerfiles (gepinde images, geen ingebakken CA), installatiedocs
(geen YAML-methode, geen voorbeeld-NAS-gegevens), versies consistent en EVIDENCE.json (contract §11)."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "deploy/manifest/jelly-leon-platform.manifest.yaml"
# Schema van de eigenaar van de manifeststandaard op de gepinde commit (KOPIE.json, gelijk aan ASF_COMMIT in de CI),
# uit dezelfde bron als test_validator_kopie: APPSTORE_BRON (CI: de checkout van de validatorstap), anders
# ../appstore-en-fork. Met VALIDATOR_BRON_VERPLICHT=1 (CI) faalt de test als de bron ontbreekt (geen skip).
APPSTORE_BRON = Path(os.environ.get("APPSTORE_BRON") or ROOT.parent / "appstore-en-fork")
APPSTORE_SCHEMA = "apps/api/src/appstore/manifest/manifest-v1.schema.json"
APPSTORE_PIN = ROOT / "apps/api/src/appstore/KOPIE.json"
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
    release = json.loads((ROOT / "release/digests.json").read_bytes())
    gepubliceerd = release["images"][m["image"]["repository"]]
    assert release["app_id"] == m["app_id"] and release["version"] == gepubliceerd["tag"]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", gepubliceerd["digest"])
    # Een volgende versie heeft pas een eigen digest zodra de tagworkflow haar bijlage heeft gepubliceerd.
    assert m["image"]["digest"] == (gepubliceerd["digest"] if m["image"]["tag"] == release["version"] else None)
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
    # Contract 1.3 §9 (C1.3-JLP-09): kop 1.3 en monitorplan op /readyz.
    assert "platformcontract 1.3 §9" in MANIFEST.read_text(encoding="utf-8").splitlines()[0]
    assert m["healthcheck"]["monitor"] == {"pad": "/readyz", "verwachte_status": 200, "timeout_s": 10, "interval_s": 60}
    accepted = {f["code"] for f in m.get("accepted_findings", [])}
    assert accepted == ({"digest_pending_release"} if m["image"]["digest"] is None else set())
    assert not [p for p in params if p.upper().startswith("HUB_")]


def _appstore_schema() -> dict[str, Any]:
    commit = json.loads(APPSTORE_PIN.read_text(encoding="utf-8"))["bron_commit"]
    r = subprocess.run(["git", "-C", str(APPSTORE_BRON), "show", f"{commit}:{APPSTORE_SCHEMA}"], capture_output=True,
                       text=True, check=False)
    if r.returncode != 0:
        if os.environ.get("VALIDATOR_BRON_VERPLICHT") == "1":
            pytest.fail(f"schema van appstore-en-fork@{commit} niet beschikbaar in {APPSTORE_BRON}")
        pytest.skip(f"schema van appstore-en-fork@{commit} niet beschikbaar in {APPSTORE_BRON}; in CI verplicht")
    schema = json.loads(r.stdout)
    assert isinstance(schema, dict)
    return schema


def test_manifest_valid_against_appstore_schema() -> None:
    import jsonschema

    validator = jsonschema.Draft202012Validator(_appstore_schema())
    assert [e.message for e in validator.iter_errors(load_manifest())] == []
    # negatief: het schema moet een kapot manifest ook echt weigeren (anders bewijst groen niets)
    kapot = {**load_manifest(), "install": {"method": "compose"}}
    assert list(validator.iter_errors(kapot)), "schema weigert install.method compose niet"


def _jl_manifest() -> str | None:
    import os

    for kandidaat in (os.environ.get("JL_MANIFEST"), str(ROOT / ".venv-validator/bin/jl-manifest"),
                      str(ROOT.parent / "appstore-en-fork/.venv/bin/jl-manifest")):
        if kandidaat and Path(kandidaat).is_file():
            return kandidaat
    return None


def test_manifest_passes_the_appstore_validator() -> None:
    """De validator van de eigenaar accepteert het manifest in --strict, ook vóór publicatie van de volgende versie."""
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
    verwacht = {"digest_pending_release"} if load_manifest()["image"]["digest"] is None else set()
    assert {f["code"] for f in report["findings"]} == verwacht, report
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


@pytest.mark.parametrize(("dienst", "workflow"), [
    (".github", "ci.yml"), (".github", "release.yml"),
    (".forgejo", "ci.yml"), (".forgejo", "release.yml"),
])
def test_workflows_are_valid_yaml_without_secrets_or_latest(dienst: str, workflow: str) -> None:
    """Review P0: ci.yml was ongeldige YAML (dubbele punt in een stapnaam) en zou op GitHub niet starten."""
    text = (ROOT / dienst / "workflows" / workflow).read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    assert isinstance(doc, dict) and isinstance(doc.get("jobs"), dict) and doc["jobs"]
    for job in doc["jobs"].values():
        for step in job.get("steps", []):
            assert isinstance(step, dict) and ("run" in step or "uses" in step), step
            uses = str(step.get("uses", ""))
            assert not uses or re.search(r"@[0-9a-f]{40}$", uses), f"action niet op commit-SHA: {uses}"
    assert ":latest" not in text and "ca-bundle" not in text


def test_validator_of_appstore_is_pinned_to_one_commit_in_ci_and_make() -> None:
    """Review P2 (S-P2-395, R-TECH-1/R-MANIFEST-1; P-INSTALLATIE punt 1): CI en ``make validator-venv`` haalden de
    validator ongepind van de branch-HEAD van appstore-en-fork; een nieuwe commit daar brak dan stil deze CI. Nu één
    vaste commit (``ASF_COMMIT``) voor beide CI-jobs, ``make validator-venv`` en de rooktest."""
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    doc = yaml.safe_load(text)
    pin = str((doc.get("env") or {}).get("ASF_COMMIT", ""))
    assert re.fullmatch(r"[0-9a-f]{40}", pin), "ci.yml: env.ASF_COMMIT ontbreekt of is geen volledige commit-SHA"
    klonen = [str(st.get("run", "")) for job in doc["jobs"].values() for st in job.get("steps", [])
              if "appstore-en-fork.git" in str(st.get("run", ""))]
    assert len(klonen) == 2, "verwacht: de validator in job test en in job rooktest"
    for run in klonen:
        assert "--depth" not in run, "met --depth bestaat de vaste commit misschien niet in de kloon"
        assert re.search(r'git -C "\$RUNNER_TEMP/appstore" checkout --quiet "\$ASF_COMMIT"', run), run
        assert run.index('checkout --quiet "$ASF_COMMIT"') < run.index("pip"), "eerst vastzetten, dan installeren"
    assert '--validator-commit "$ASF_COMMIT"' in text and "rev-parse HEAD)" not in text
    make = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert re.search(rf"^ASF_COMMIT \?= {pin}$", make, re.M), "Makefile: ASF_COMMIT wijkt af van ci.yml"
    kopie = json.loads((ROOT / "apps/api/src/appstore/KOPIE.json").read_text(encoding="utf-8"))
    assert kopie["bron_commit"] == pin, "de dienstkopie en de CLI-validator moeten dezelfde commit gebruiken"
    assert "archive $(ASF_COMMIT) apps/api" in make and "archive HEAD apps/api" not in make
    assert "--validator-commit $(ASF_COMMIT)" in make


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


def test_forgejo_controleert_de_privebronnen_bij_push_en_pull_request() -> None:
    ci = yaml.safe_load((ROOT / ".forgejo/workflows/ci.yml").read_text())
    kopie = json.loads((ROOT / "apps/api/src/appstore/KOPIE.json").read_text())
    assert set(ci.get("on", ci.get(True))) == {"push", "pull_request", "workflow_dispatch"}
    assert ci["env"]["ASF_COMMIT"] == kopie["bron_commit"]
    for naam in ("test", "rooktest"):
        job = ci["jobs"][naam]
        stap = next(st for st in job["steps"] if st.get("run") == f"bash .forgejo/ci/bronnen.sh {naam}")
        assert "if" not in job and "if" not in stap and not stap.get("continue-on-error")
        assert stap["env"]["BRON_TOKEN"] == "${{ secrets.BRON_LEESTOKEN }}"
    controle = next(st for st in ci["jobs"]["test"]["steps"] if st.get("run") == "python -m pytest -q -rs tests/repo")
    for vlag in ("PEIL_BRON_VERPLICHT", "MANIFEST_VALIDATOR_VERPLICHT", "VALIDATOR_BRON_VERPLICHT"):
        assert controle["env"][vlag] == "1"
    bron = (ROOT / ".forgejo/ci/bronnen.sh").read_text()
    assert 'git -C "$RUNNER_TEMP/appstore" checkout --quiet "$ASF_COMMIT"' in bron


def test_forgejo_publiceert_alleen_bij_een_tagpush() -> None:
    release = yaml.safe_load((ROOT / ".forgejo/workflows/release.yml").read_text())
    job = release["jobs"]["publish"]
    assert job["env"]["REGISTRY_IMAGE"] == "git.medialeon.nl/leon/jelly-leon-platform"
    altijd = {stap.get("run") for stap in job["steps"] if "if" not in stap}
    assert {"bash .forgejo/ci/dockerproef.sh release", "bash .forgejo/ci/sbom.sh"} <= altijd
    publicatie = [stap for stap in job["steps"]
                  if {"REGISTRY_TOKEN", "API_TOKEN"} & stap.get("env", {}).keys()
                  or "upload-artifact@" in stap.get("uses", "")]
    assert len(publicatie) == 3  # register, workflowartefact en release met bijlagen
    tagpush = "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')"
    assert all(stap["if"] == tagpush for stap in publicatie)


def test_compose_only_for_development() -> None:
    text = (ROOT / "e2e/integral/dev/compose.yaml").read_text()
    assert "ALLEEN VOOR ONTWIKKELING" in text and "GEEN installatiemethode" in text
    assert ":latest" not in text


@pytest.mark.parametrize("mapnaam", ["install", "deploy"])
def test_geen_compose_in_installatiemappen(mapnaam: str) -> None:
    naam = re.compile(r"(?:docker-)?compose.*\.ya?ml", re.IGNORECASE)
    gevonden = [pad.relative_to(ROOT) for pad in (ROOT / mapnaam).rglob("*")
                if naam.fullmatch(pad.name) and (pad.is_file() or pad.is_symlink())]
    assert not gevonden, gevonden


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
             "NIET GEVALIDEERD (echte Hub)", "Sim-geslaagd", "Sim-mislukt", "Niet getest"}
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
    assert len(punten) == 15, punten
    seen: set[str] = set()
    for row in _evidence():
        assert row["id"] not in seen, row["id"]
        seen.add(row["id"])
        check_evidence_row(row, tasks, punten)
    # elk contractpunt dat NU moet, heeft een regel (ook als het geblokkeerd is of nog niet gestart)
    assert {f"C1.3-JLP-{n:02d}" for n in range(1, 14)} <= seen


@pytest.mark.parametrize(("rid", "soort"), [
    ("B08-003", "account"),
    ("B08-004", "account"),
    ("B08-006", "account"),
    ("EX16", "echte NAS"),
    ("EX17", "account"),
    ("EX17", "echte NAS"),
    ("EX18", "echte NAS"),
    ("EX19", "echte NAS"),
    ("EX20", "echte NAS"),
    ("EX21", "account"),
    ("EX21", "echte NAS"),
    ("EX50", "echte NAS"),
    ("C1.3-JLP-01", "account"),
    ("C1.3-JLP-09", "account"),
    ("C1.3-JLP-10", "account"),
    ("C1.3-JLP-11", "account"),
    ("C1.3-JLP-14", "account"),
    ("EX26", "hardware"),
    ("EX30", "hardware"),
    ("I01-01-01", "hardware"),
    ("I01-01-01", "account"),
    ("I01-01-03", "account"),
    ("I01-01-04", "account"),
    ("I01-01-05", "account"),
    ("I01-01-07", "account"),
    ("I01-01-08", "account"),
    ("I02-02-04", "account"),
    ("I02-02-05", "account"),
    ("I02-02-07", "account"),
    ("I03-03-01", "account"),
    ("I03-03-02", "account"),
    ("I03-03-03", "account"),
    ("I03-03-04", "account"),
    ("I03-03-05", "account"),
    ("I03-03-06", "account"),
    ("I03-03-07", "account"),
    ("I03-03-08", "account"),
    ("I04-04-01", "account"),
    ("I04-04-02", "account"),
    ("I04-04-03", "account"),
    ("I04-04-04", "account"),
    ("I04-04-05", "account"),
    ("I04-04-06", "account"),
    ("I04-04-07", "account"),
    ("I04-04-08", "account"),
    ("I05-05-01", "account"),
    ("I05-05-02", "account"),
    ("I05-05-03", "account"),
    ("I05-05-04", "account"),
    ("I05-05-05", "account"),
    ("I05-05-06", "account"),
    ("I05-05-07", "account"),
    ("I05-05-08", "account"),
    ("I07-07-01", "account"),
    ("I07-07-02", "account"),
    ("I07-07-03", "account"),
    ("I07-07-04", "hardware"),
    ("I07-07-04", "account"),
    ("I07-07-05", "hardware"),
    ("I07-07-05", "account"),
    ("I07-07-06", "account"),
    ("I08-08-01", "account"),
    ("I08-08-03", "account"),
    ("I08-08-04", "account"),
    ("I08-08-05", "hardware"),
    ("I08-08-05", "account"),
    ("I08-08-06", "account"),
    ("I09-09-01", "account"),
    ("I09-09-02", "account"),
    ("I09-09-04", "account"),
    ("I09-09-05", "account"),
    ("I09-09-06", "account"),
    ("I13-13-01", "account"),
    ("I13-13-02", "account"),
    ("I13-13-03", "account"),
    ("I13-13-04", "account"),
    ("I13-13-05", "account"),
    ("I15-15-01", "account"),
    ("I15-15-03", "hardware"),
    ("I15-15-03", "account"),
    ("I15-15-04", "account"),
    ("I15-15-05", "account"),
    ("I15-15-06", "account"),
    ("I16-16-01", "account"),
    ("I16-16-02", "account"),
    ("I16-16-03", "account"),
    ("I16-16-04", "account"),
    ("I16-16-05", "account"),
    ("I16-16-06", "account"),
    ("I17-17-01", "account"),
    ("I17-17-02", "account"),
    ("I17-17-03", "account"),
    ("I17-17-04", "account"),
    ("I17-17-05", "account"),
    ("I17-17-06", "account"),
    ("I18-18-01", "account"),
    ("I18-18-02", "account"),
    ("I18-18-03", "account"),
    ("I18-18-04", "account"),
    ("I18-18-05", "account"),
    ("I18-18-06", "account"),
    ("I19-19-01", "account"),
    ("I19-19-02", "account"),
    ("I19-19-03", "account"),
    ("I19-19-04", "account"),
    ("I19-19-05", "account"),
    ("I19-19-06", "account"),
    ("I20-20-01", "account"),
    ("I20-20-02", "account"),
    ("I20-20-03", "account"),
    ("I20-20-05", "hardware"),
    ("I20-20-05", "account"),
    ("I23-23-02", "account"),
    ("I23-23-03", "account"),
    ("I23-23-04", "account"),
    ("I23-23-05", "account"),
    ("I23-23-06", "account"),
    ("I23-23-07", "account"),
])
def test_evidence_vermeldt_de_afhankelijkheid_van_de_praktijkproef(rid: str, soort: str) -> None:
    per_id = {rij["id"]: rij for rij in _evidence()}
    assert f"NIET GEVALIDEERD ({soort})" in per_id[rid]["validatie"].split("; ")


def test_vaste_lijst_is_de_tabel_van_paragraaf_11() -> None:
    """De vaste lijst volgt de contractkopie: een waarde erbij of eraf zonder contracttekst is rood (C12-6)."""
    tekst = (ROOT / "docs/platform-v2/PLATFORM_CONTRACTEN.md").read_text(encoding="utf-8")
    begin = tekst.index("## 11. Bewijs en Excel")
    p11 = tekst[begin : tekst.index("## 12.", begin)]
    in_tabel: set[str] = set()
    for ruw in p11.splitlines():
        regel = ruw.strip()
        if regel.startswith("| `"):
            in_tabel.update(re.findall(r"`([^`]+)`", regel.split(" | ")[0]))
    assert in_tabel == VALIDATIE, f"verschil met contract §11: {sorted(in_tabel ^ VALIDATIE)}"


def test_evidence_check_accepts_echte_hub() -> None:
    """Positief: NIET GEVALIDEERD (echte Hub) (C-12) is geldig in validatie en, consistent, in blokkade."""
    rij = {"id": "C1.3-JLP-13", "status": "Bezig", "validatie": "Unit-getest; NIET GEVALIDEERD (echte Hub)",
           "bewijs": "x", "blokkade": "Hub-koppeling alleen met de Hub-simulator: NIET GEVALIDEERD (echte Hub).",
           "opmerking": "", "repo": "jelly-leon-platform", "bijgewerkt": "2026-11-17"}
    check_evidence_row(rij, set(), _contractpunten())


@pytest.mark.parametrize("fout", [
    {"validatie": ""},
    {"validatie": "Sim-getest; ok"},
    {"status": "Gereed"},
    {"status": "Bezig", "validatie": "Niet getest"},
    {"status": "Geblokkeerd", "validatie": "Sim-getest", "blokkade": "NIET GEVALIDEERD (echte NAS): pool nodig."},
    {"validatie": "Unit-getest", "blokkade": "NIET GEVALIDEERD (echte Hub): alleen de Hub-simulator."},
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


def _controleer_nas_claim(rijen: list[dict[str, Any]]) -> None:
    geselecteerd = {r["id"]: r for r in rijen if r["id"].startswith(("I03-03-", "I04-04-"))}
    assert len(geselecteerd) == 16
    vervolgwerk = {"I03-03-04", "I03-03-08", "I04-04-05", "I04-04-07", "I04-04-08"}
    aantekening = "alleen nas-test nodig"
    for id_, rij in geselecteerd.items():
        opmerking = rij["opmerking"].lower()
        if aantekening not in opmerking:
            continue
        assert id_ not in vervolgwerk, id_
        assert f"niet {aantekening}" not in opmerking, id_
        tekst = (rij["blokkade"] + " " + rij["opmerking"]).lower()
        extra = ("eerst afronden", "eerst aantonen", "herstelbewijs", "herstelgate", "geïsoleerde")
        assert not any(woord in tekst for woord in extra), id_
        for verwijzing in re.findall(r"I0[34]-0[34]-\d\d", tekst.upper()):
            assert verwijzing in geselecteerd and verwijzing not in vervolgwerk, id_


def test_i03_i04_geen_te_ruime_nas_claim() -> None:
    rijen = json.loads((ROOT / "docs/platform-v2/EVIDENCE.json").read_text(encoding="utf-8"))
    _controleer_nas_claim(rijen)


@pytest.mark.parametrize("afhankelijkheid", ["herstelbewijs ontbreekt", "zie I04-04-07"])
def test_i03_i04_te_ruime_nas_claim_wordt_geweigerd(afhankelijkheid: str) -> None:
    rijen = json.loads((ROOT / "docs/platform-v2/EVIDENCE.json").read_text(encoding="utf-8"))
    rij = next(r for r in rijen if r["id"] == "I04-04-02")
    rij["blokkade"] = afhankelijkheid
    rij["opmerking"] = "alleen NAS-test nodig"
    with pytest.raises(AssertionError, match="I04-04-02"):
        _controleer_nas_claim(rijen)


def test_i03_proefomgeving_is_geen_alleen_nas_claim() -> None:
    rijen = json.loads((ROOT / "docs/platform-v2/EVIDENCE.json").read_text(encoding="utf-8"))
    for rij in rijen:
        if rij["id"] in ("I03-03-05", "I03-03-06"):
            assert "alleen nas-test nodig" not in rij["opmerking"].lower(), rij["id"]


def test_klein105_bewijs_en_accountoordeel() -> None:
    rijen = json.loads((ROOT / "docs/platform-v2/EVIDENCE.json").read_text(encoding="utf-8"))
    geselecteerd = [r for r in rijen if r["id"].startswith(("I03-03-", "I04-04-"))]
    assert len(geselecteerd) == 16
    accountstappen = {
        "I03-03-01", "I03-03-02", "I03-03-03", "I03-03-04", "I03-03-05", "I03-03-06", "I03-03-07", "I03-03-08",
        "I04-04-01", "I04-04-02", "I04-04-03", "I04-04-04", "I04-04-05", "I04-04-06", "I04-04-07", "I04-04-08",
    }
    for rij in geselecteerd:
        for tekst in ("040f4ea", "25fec3a", "cbf74db", "44186697403", "N-P6-05",
                      "test_i03_i04_geen_te_ruime_nas_claim", "test_i03_proefomgeving_is_geen_alleen_nas_claim",
                      "test_i04_hersteloverdracht_mutatie_wordt_geweigerd",
                      "test_menupaden_gebruiken_letterlijke_bronnamen"):
            assert tekst in rij["bewijs"], (rij["id"], tekst)
        account_gemarkeerd = "NIET GEVALIDEERD (account)" in rij["validatie"].split("; ")
        assert account_gemarkeerd == (rij["id"] in accountstappen), rij["id"]
    hub = next(r for r in geselecteerd if r["id"] == "I03-03-08")
    assert "zolang B-6/ASF-15 ontbreekt" not in hub["opmerking"]


def _controleer_bestaande_app_bewijs(
    rijen: list[dict[str, Any]], app: str, alleen_nas: set[int], hub: set[int], account: set[int],
) -> None:
    geselecteerd = [r for r in rijen if r["id"].startswith(app + "-")]
    assert len(geselecteerd) == 6
    for nummer, rij in enumerate(geselecteerd, 1):
        assert rij["status"] == "Bezig", rij["id"]
        waarden = set(rij["validatie"].split("; "))
        assert "Unit-getest" in waarden and "Sim-getest" not in waarden
        assert "NIET GEVALIDEERD (echte NAS)" in waarden
        assert ("NIET GEVALIDEERD (echte Hub)" in waarden) == (nummer in hub)
        assert ("NIET GEVALIDEERD (account)" in waarden) == (nummer in account)
        if "alleen nas-test nodig" in rij["opmerking"].lower():
            assert nummer in alleen_nas, rij["id"]
        assert "N-P6-05" in rij["bewijs"]
        assert rij["blokkade"]


def test_i15_bewijsgrenzen() -> None:
    rijen = json.loads((ROOT / "docs/platform-v2/EVIDENCE.json").read_text(encoding="utf-8"))
    _controleer_bestaande_app_bewijs(rijen, "I15", {1, 3}, {4, 5}, {1, 2, 3, 4, 5, 6})


def test_i15_te_ruime_nas_claim_wordt_geweigerd() -> None:
    rijen = json.loads((ROOT / "docs/platform-v2/EVIDENCE.json").read_text(encoding="utf-8"))
    next(r for r in rijen if r["id"] == "I15-15-02")["opmerking"] = "alleen NAS-test nodig"
    with pytest.raises(AssertionError, match="I15-15-02"):
        _controleer_bestaande_app_bewijs(rijen, "I15", {1, 3}, {4, 5}, {1, 2, 3, 4, 5, 6})


def test_i16_bewijsgrenzen() -> None:
    rijen = json.loads((ROOT / "docs/platform-v2/EVIDENCE.json").read_text(encoding="utf-8"))
    _controleer_bestaande_app_bewijs(rijen, "I16", {1, 2}, {5}, {1, 2, 3, 4, 5, 6})


def test_i16_te_ruime_nas_claim_wordt_geweigerd() -> None:
    rijen = json.loads((ROOT / "docs/platform-v2/EVIDENCE.json").read_text(encoding="utf-8"))
    next(r for r in rijen if r["id"] == "I16-16-04")["opmerking"] = "alleen NAS-test nodig"
    with pytest.raises(AssertionError, match="I16-16-04"):
        _controleer_bestaande_app_bewijs(rijen, "I16", {1, 2}, {5}, {1, 2, 3, 4, 5, 6})


def test_i17_bewijsgrenzen() -> None:
    rijen = json.loads((ROOT / "docs/platform-v2/EVIDENCE.json").read_text(encoding="utf-8"))
    _controleer_bestaande_app_bewijs(rijen, "I17", {1}, set(), {1, 2, 3, 4, 5, 6})


def test_i17_te_ruime_nas_claim_wordt_geweigerd() -> None:
    rijen = json.loads((ROOT / "docs/platform-v2/EVIDENCE.json").read_text(encoding="utf-8"))
    next(r for r in rijen if r["id"] == "I17-17-05")["opmerking"] = "alleen NAS-test nodig"
    with pytest.raises(AssertionError, match="I17-17-05"):
        _controleer_bestaande_app_bewijs(rijen, "I17", {1}, set(), {1, 2, 3, 4, 5, 6})
