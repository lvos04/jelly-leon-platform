"""E2E van de alles-in-één image (zoals TrueNAS hem als standaard Custom App start: één map → /appdata, één poort).

Vereist Docker en een gebouwde image (``LIFECYCLE_IMAGE``, standaard ``jelly-leon-platform:dev``). Getest: eerste start
als root, eigenaarschap 568, no_new_privs, herstart met behoud van geheimen/database, harde kill, start als 568,
foutmelding bij onschrijfbare map, en enrollment bij een Hub-simulator met een eenmalig token + JWT-toegang.
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


@pytest.fixture
def data_dir(tmp_path: Path) -> Iterator[Path]:
    d = tmp_path / "appdata"
    d.mkdir()
    yield d
    # bestanden zijn van 568: opruimen via een container (geen sudo op de host nodig)
    sh("docker", "run", "--rm", "-v", f"{tmp_path}:/x", "--entrypoint", "rm", IMAGE, "-rf", "/x/appdata", check=False)


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
    st = (data_dir / "secrets").stat()
    assert (st.st_uid, st.st_gid) == (568, 568) and oct(st.st_mode & 0o777) == "0o700"
    for name in ("vault.key", "identity.ed25519"):
        s = (data_dir / "secrets" / name).stat()
        assert (s.st_uid, s.st_mode & 0o777) == (568, 0o600)
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
    code, _body = get(f"http://127.0.0.1:{started.port}/lifecycle/v1/runs")
    assert code == 401  # fail-closed zonder Hub
    # de database luistert niet op het netwerk
    assert "5432" not in started.exec("sh", "-c", "cat /proc/net/tcp /proc/net/tcp6 | awk '{print $2}'").stdout


def test_restart_keeps_secrets_and_kill_recovers(started: Container, data_dir: Path) -> None:
    before = (data_dir / "secrets" / "vault.key").read_bytes() if os.access(data_dir / "secrets", os.R_OK) else None
    vault_sum = started.exec("sha256sum", "/appdata/secrets/vault.key", user="568").stdout
    sh("docker", "restart", started.name)
    wait_ready(started.port)
    assert started.exec("sha256sum", "/appdata/secrets/vault.key", user="568").stdout == vault_sum
    sh("docker", "kill", "-s", "KILL", started.name)
    sh("docker", "start", started.name)
    wait_ready(started.port)
    assert "postmaster.pid" in started.logs()
    assert before is None or (data_dir / "secrets" / "vault.key").read_bytes() == before


def test_start_as_568_and_unwritable_dir(tmp_path: Path) -> None:
    ok_dir = tmp_path / "ok"
    ok_dir.mkdir()
    os.chown(ok_dir, 568, 568)
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
    sh("docker", "run", "--rm", "-v", f"{tmp_path}:/x", "--entrypoint", "rm", IMAGE, "-rf", "/x/ok", check=False)


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
        code, link = get(f"http://127.0.0.1:{port}/lifecycle/v1/hub-link", {"Authorization": f"Bearer {token}"})
        assert code == 200 and link["state"] == "gekoppeld" and link["linked_via"] == "token"
        code, page = get(f"http://127.0.0.1:{port}/")
        assert "Gekoppeld met" in page and secret not in page
        assert secret not in c.logs()
        code, health = get(f"http://127.0.0.1:{port}/connector/v1/health", {"Authorization": f"Bearer {token}"})
        assert code == 200 and health["status"] == "healthy", health
    finally:
        print(c.logs())
        c.remove()
