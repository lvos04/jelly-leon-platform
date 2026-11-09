"""E2E van de alles-in-één image (zoals TrueNAS hem als standaard Custom App start: één map → /appdata, één poort).

Vereist Docker en een gebouwde image (``LIFECYCLE_IMAGE``, standaard ``jelly-leon-platform:dev``). Getest: eerste start
als root met lege /appdata, eigenaarschap 568, no_new_privs, Docker-HEALTHCHECK wordt ``healthy``, herstart met behoud
van geheimen, harde kill, start als 568, foutmelding bij onschrijfbare map, enrollment bij een Hub-simulator met een
eenmalig token + JWT-toegang, en herstart/kill zonder verlies van runs of koppeling.
Niet getest (kan hier niet): het echte TrueNAS-formulier/ix-app — NIET GEVALIDEERD (echte NAS).
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import docker_ok, free_port

IMAGE = os.environ.get("LIFECYCLE_IMAGE", "jelly-leon-platform:dev")
pytestmark = pytest.mark.skipif(not docker_ok(), reason="Docker niet beschikbaar")


def sh(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, check=check)


def get(url: str, headers: dict[str, str] | None = None) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers=headers or {})  # noqa: S310 - test tegen localhost
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            body = resp.read().decode()
            return resp.status, (json.loads(body) if "json" in resp.headers.get("Content-Type", "") else body)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def post(url: str, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, Any]:
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST",  # noqa: S310 - test tegen localhost
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def wait_healthy(name: str, timeout: float = 150) -> str:
    """Wacht tot de ingebouwde Docker-HEALTHCHECK (die TrueNAS gebruikt) ``healthy`` meldt."""
    deadline = time.monotonic() + timeout
    status = ""
    while time.monotonic() < deadline:
        status = sh("docker", "inspect", "-f", "{{.State.Health.Status}}", name, check=False).stdout.strip()
        if status in {"healthy", "unhealthy"}:
            return status
        time.sleep(2)
    return status


def wait_ready(port: int, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = get(f"http://127.0.0.1:{port}/readyz")
            if last[0] == 200:
                return
        except Exception as exc:  # nog niet luisterend
            last = exc
        time.sleep(1)
    raise AssertionError(f"niet ready binnen {timeout} s: {last}")


class Container:
    def __init__(self, data: Path, port: int, *extra: str, env: dict[str, str] | None = None,
                 network_host: bool = False) -> None:
        self.name = f"jlp-e2e-{uuid.uuid4().hex[:8]}"
        args = ["docker", "run", "-d", "--name", self.name, "-v", f"{data}:/appdata"]
        if network_host:
            args += ["--network", "host", "-e", f"LIFECYCLE_PORT={port}"]
        else:
            args += ["-p", f"127.0.0.1:{port}:30440"]
        for k, v in (env or {}).items():
            args += ["-e", f"{k}={v}"]
        self.args = [*args, *extra, IMAGE]
        self.port = port

    def run(self) -> None:
        sh(*self.args)

    def logs(self) -> str:
        r = sh("docker", "logs", self.name, check=False)
        return r.stdout + r.stderr

    def exec(self, *cmd: str, user: str | None = None) -> subprocess.CompletedProcess[str]:
        return sh("docker", "exec", *(["-u", user] if user else []), self.name, *cmd, check=False)

    def remove(self) -> None:
        sh("docker", "rm", "-f", self.name, check=False)


def as_root_in_helper(mount: Path, entrypoint: str, *args: str) -> subprocess.CompletedProcess[str]:
    """Bestandsbewerking op een host-map als root ín een wegwerpcontainer (geen sudo op de host nodig)."""
    return sh("docker", "run", "--rm", "--network", "none", "--user", "0:0", "-v", f"{mount}:/x",
              "--entrypoint", entrypoint, IMAGE, *args, check=False)


def host_stat(data: Path, rel: str) -> tuple[str, str, str]:
    r = as_root_in_helper(data, "stat", "-c", "%u %g %a", f"/x/{rel}")
    assert r.returncode == 0, r.stderr
    uid, gid, mode = r.stdout.split()
    return uid, gid, mode


@pytest.fixture
def data_dir(tmp_path: Path) -> Iterator[Path]:
    d = tmp_path / "appdata"
    d.mkdir()
    yield d
    # bestanden zijn van 568: opruimen via een container (geen sudo op de host nodig)
    as_root_in_helper(tmp_path, "rm", "-rf", "/x/appdata")


@pytest.fixture
def started(data_dir: Path) -> Iterator[Container]:
    c = Container(data_dir, free_port())
    c.run()
    try:
        wait_ready(c.port)
        yield c
    finally:
        print(c.logs())
        c.remove()


def test_first_start_as_root_then_drops_to_568(started: Container, data_dir: Path) -> None:
    assert started.exec("python", "/usr/local/lib/lifecycle-allinone/healthcheck.py").returncode == 0
    assert wait_healthy(started.name) == "healthy", started.logs()
    # eigenaarschap op de host-map zelf (bind-mount: dezelfde numerieke uid/gid), gelezen via een hulpcontainer zodat
    # de test geen root op de host nodig heeft (secrets/ is 0700 van 568)
    assert host_stat(data_dir, "secrets") == ("568", "568", "700")
    assert host_stat(data_dir, "backups") == ("568", "568", "700")  # dumps bevatten de hele database
    for name in ("vault.key", "identity.ed25519"):
        uid, _gid, mode = host_stat(data_dir, f"secrets/{name}")
        assert (uid, mode) == ("568", "600"), (name, uid, mode)
    # alle processen behalve dumb-init (PID 1) draaien als 568, met NoNewPrivs
    ps = started.exec("sh", "-c", "for p in /proc/[0-9]*; do [ -r $p/status ] && "
                      "awk '/^(Name|Uid|NoNewPrivs):/{printf \"%s \", $2} END{print \"\"}' $p/status; done")
    rows = [line.split() for line in ps.stdout.splitlines() if line.strip()]
    workers = [r for r in rows if r and r[0] in {"python", "postgres", "uvicorn"}]
    assert workers, ps.stdout
    for name, uid, nnp in ((r[0], r[1], r[-1]) for r in workers):
        assert uid == "568", f"{name} draait als {uid}"
        assert nnp == "1", f"{name} zonder no_new_privs"
    code, page = get(f"http://127.0.0.1:{started.port}/")
    assert code == 200 and "niet gekoppeld" in page.lower()
    # de healthcheck (elke 30 s) vult de log niet met migratiemeldingen: extra /readyz-aanroepen voegen niets toe
    before = started.logs().count("Context impl PostgresqlImpl")
    for _ in range(3):
        assert get(f"http://127.0.0.1:{started.port}/readyz")[0] == 200
    assert started.logs().count("Context impl PostgresqlImpl") == before
    code, _body = get(f"http://127.0.0.1:{started.port}/lifecycle/v1/runs")
    assert code == 401  # fail-closed zonder Hub
    # de database luistert niet op het netwerk
    assert "5432" not in started.exec("sh", "-c", "cat /proc/net/tcp /proc/net/tcp6 | awk '{print $2}'").stdout


def test_existing_data_in_appdata_is_not_touched(data_dir: Path) -> None:
    """I01-01-07 (negatief): de image maakt alléén zijn eigen submappen eigendom van 568; bestaande bestanden en mappen
    in de gekozen dataset behouden eigenaar en rechten (geen recursieve chown)."""
    own = data_dir / "bestaand"
    own.mkdir(mode=0o750)
    (own / "foto.jpg").write_bytes(b"niet aanraken")
    os.chmod(own / "foto.jpg", 0o640)
    uid, gid = str(os.getuid()), str(os.getgid())
    assert uid != "568"
    c = Container(data_dir, free_port())
    c.run()
    try:
        wait_ready(c.port)
        assert host_stat(data_dir, "bestaand") == (uid, gid, "750")
        assert host_stat(data_dir, "bestaand/foto.jpg") == (uid, gid, "640")
        assert host_stat(data_dir, "secrets")[:2] == ("568", "568")
    finally:
        c.remove()


def test_restart_keeps_secrets_and_kill_recovers(started: Container, data_dir: Path) -> None:
    # sleutel zoals hij op de host-map staat (via hulpcontainer, geen root op de host nodig)
    before = as_root_in_helper(data_dir, "sha256sum", "/x/secrets/vault.key").stdout.split()[:1]
    assert before, "vault.key ontbreekt op de host-map"
    vault_sum = started.exec("sha256sum", "/appdata/secrets/vault.key", user="568").stdout
    sh("docker", "restart", started.name)
    wait_ready(started.port)
    assert started.exec("sha256sum", "/appdata/secrets/vault.key", user="568").stdout == vault_sum
    sh("docker", "kill", "-s", "KILL", started.name)
    sh("docker", "start", started.name)
    wait_ready(started.port)
    assert "postmaster.pid" in started.logs()
    assert as_root_in_helper(data_dir, "sha256sum", "/x/secrets/vault.key").stdout.split()[:1] == before


def test_start_as_568_and_unwritable_dir(tmp_path: Path) -> None:
    ok_dir = tmp_path / "ok"
    ok_dir.mkdir()
    assert as_root_in_helper(tmp_path, "chown", "568:568", "/x/ok").returncode == 0  # zoals TrueNAS-ACL 568
    c = Container(ok_dir, free_port(), "--user", "568:568")
    c.run()
    try:
        wait_ready(c.port)
    finally:
        c.remove()
    bad = tmp_path / "bad"
    bad.mkdir(mode=0o755)
    b = Container(bad, free_port(), "--user", "568:568", env={"LIFECYCLE_FAIL_PAUSE_SECONDS": "0"})
    b.run()
    try:
        deadline = time.monotonic() + 30
        while "FOUT" not in b.logs() and time.monotonic() < deadline:
            time.sleep(1)
        assert "geen schrijfrechten" in b.logs() or "kan niet schrijven" in b.logs()
    finally:
        b.remove()
    as_root_in_helper(tmp_path, "rm", "-rf", "/x/ok")


def test_enrollment_with_hub_simulator(data_dir: Path, hub_server: tuple[str, Any]) -> None:
    hub_url, sim = hub_server
    secret = sim.add_grant("token", "jelly-leon-platform")
    port = free_port()
    c = Container(data_dir, port, network_host=True, env={"HUB_URL": hub_url, "HUB_ENROLLMENT_TOKEN": secret})
    c.run()
    try:
        wait_ready(port)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not any(
            e["event_type"] == "service.enrollment.completed" for e in sim.events
        ):
            time.sleep(1)
        assert any(e["event_type"] == "service.enrollment.completed" for e in sim.events), c.logs()
        token = sim.mint("e2e-admin", ["admin"], "jelly-leon-platform")
        # de API ziet de koppeling van de worker via de database (korte cache, max. enkele seconden); tot dan
        # weigert hij JWT's (fail-closed)
        deadline = time.monotonic() + 15
        code, link = get(f"http://127.0.0.1:{port}/lifecycle/v1/hub-link", {"Authorization": f"Bearer {token}"})
        while code == 401 and time.monotonic() < deadline:
            time.sleep(0.5)
            code, link = get(f"http://127.0.0.1:{port}/lifecycle/v1/hub-link", {"Authorization": f"Bearer {token}"})
        assert code == 200 and link["state"] == "gekoppeld" and link["linked_via"] == "token", link
        code, page = get(f"http://127.0.0.1:{port}/")
        assert "Gekoppeld met" in page and secret not in page
        assert secret not in c.logs()
        code, health = get(f"http://127.0.0.1:{port}/connector/v1/health", {"Authorization": f"Bearer {token}"})
        assert code == 200 and health["status"] == "healthy", health

        # data vastleggen en daarna herstarten en hard killen: niets mag verloren gaan
        headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": str(uuid.uuid4())}
        code, run = post(f"http://127.0.0.1:{port}/lifecycle/v1/runs",
                         {"app_id": "uptime-kuma", "kind": "install", "target_version": "1.2.25",
                          "reason": "e2e herstarttest"}, headers)
        assert code == 201, run
        for action in (("restart",), ("kill", "-s", "KILL")):
            sh("docker", *action, c.name)
            if action[0] == "kill":
                sh("docker", "start", c.name)
            wait_ready(port)
            fresh = sim.mint("e2e-admin", ["admin"], "jelly-leon-platform")
            auth = {"Authorization": f"Bearer {fresh}"}
            code, again = get(f"http://127.0.0.1:{port}/lifecycle/v1/runs/{run['id']}", auth)
            assert code == 200 and again["etag"] == run["etag"] and again["reason"] == "e2e herstarttest", again
            code, link = get(f"http://127.0.0.1:{port}/lifecycle/v1/hub-link", auth)
            assert code == 200 and link["state"] == "gekoppeld" and link["service_id"], link
        assert len([e for e in sim.events if e["event_type"] == "service.enrollment.completed"]) == 1
    finally:
        print(c.logs())
        c.remove()
