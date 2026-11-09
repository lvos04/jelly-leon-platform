"""Upgrade-E2E van de alles-in-één image (B08-004 / I02-02-05 / EX15 voor de eigen app):

1. v1 (huidig schema, kop ``HEAD``) draait met data → v2 (extra testmigratie ``NEXT``) op dezelfde /appdata: eerst
   ``pg_dump`` in /appdata/backups, dan migratie; de dump is terug te zetten in een aparte database en bevat de data.
2. v3 (testmigratie ``NEXT`` faalt halverwege) → container start de API niet, meldt het in het Nederlands, database
   blijft op ``HEAD`` met data intact; terug naar de v1-tag start gewoon weer (tag-rollback zonder schemawijziging).

De varianten worden lokaal gebouwd uit dezelfde Dockerfile (alleen een extra migratiebestand). Vereist Docker.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import ROOT, docker_ok, free_port

IMAGE = os.environ.get("LIFECYCLE_IMAGE", "jelly-leon-platform:dev")
pytestmark = pytest.mark.skipif(not docker_ok(), reason="Docker niet beschikbaar")
HEAD = "0002"  # kop van de migraties in de image (contract 1.2: hub-link.json + jwt_replay)
NEXT = "0003"

MIGRATION_OK = '''"""test: kolom erbij"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("lifecycle_runs", sa.Column("e2e_extra", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("lifecycle_runs", "e2e_extra")
'''

MIGRATION_FAIL = '''"""test: faalt halverwege"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DELETE FROM lifecycle_runs")
    op.execute("SELECT 1/0")


def downgrade():
    pass
'''


def sh(*args: str, check: bool = True, timeout: int = 600) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(args), capture_output=True, text=True, check=check, timeout=timeout)


def build_variant(tmp: Path, tag: str, migration: str) -> str:
    ctx = tmp / f"ctx-{tag}"
    (ctx / "apps/api").mkdir(parents=True)
    shutil.copytree(ROOT / "apps/api/src", ctx / "apps/api/src", ignore=shutil.ignore_patterns("__pycache__",
                                                                                               "*.egg-info"))
    shutil.copy(ROOT / "apps/api/requirements.lock", ctx / "apps/api/requirements.lock")
    shutil.copytree(ROOT / "deploy/allinone", ctx / "deploy/allinone")
    (ctx / "apps/api/src/lifecycle/migrations/versions/0003_e2e.py").write_text(migration)
    image = f"jelly-leon-platform:e2e-{tag}"
    extra: list[str] = []
    # alleen achter een TLS-proxy (bouwomgeving): CA als BuildKit-secret, nooit in de image
    if ca := os.environ.get("LIFECYCLE_BUILD_CA"):
        extra += ["--secret", f"id=ca_bundle,src={ca}"]
    if network := os.environ.get("LIFECYCLE_BUILD_NETWORK"):
        extra += ["--network", network]
    cmd = ["docker", "build", "-q", *extra, "-f", "deploy/allinone/Dockerfile", "-t", image, str(ctx)]
    # gedeelde ontwikkelmachine: image-builds machinebreed serialiseren als LIFECYCLE_BUILD_LOCK een lockbestand noemt
    if (lock := os.environ.get("LIFECYCLE_BUILD_LOCK")) and shutil.which("flock"):
        cmd = ["flock", lock, *cmd]
    sh(*cmd, timeout=1800)
    return image


def wait_log(name: str, needle: str, timeout: float = 120) -> str:
    deadline = time.monotonic() + timeout
    logs = ""
    while time.monotonic() < deadline:
        r = sh("docker", "logs", name, check=False)
        logs = r.stdout + r.stderr
        if needle in logs:
            return logs
        time.sleep(1)
    raise AssertionError(f"'{needle}' niet in log binnen {timeout} s:\n{logs}")


def ready(port: int, timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = sh("curl", "-fsS", f"http://127.0.0.1:{port}/readyz", check=False)
        if r.returncode == 0:
            return
        time.sleep(1)
    raise AssertionError("niet ready")


def psql(name: str, sql: str, db: str = "lifecycle") -> str:
    r = sh("docker", "exec", "-u", "568", name, "psql", "-h", "/tmp/lifecycle-pg", "-U", "lifecycle", "-d", db,
           "-tAc", sql, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


@pytest.fixture
def appdata(tmp_path: Path) -> Iterator[Path]:
    d = tmp_path / "appdata"
    d.mkdir()
    yield d
    sh("docker", "run", "--rm", "-v", f"{tmp_path}:/x", "--entrypoint", "rm", IMAGE, "-rf", "/x/appdata",
       check=False)


def run(image: str, appdata: Path, port: int) -> str:
    name = f"jlp-up-{uuid.uuid4().hex[:8]}"
    sh("docker", "run", "-d", "--name", name, "-v", f"{appdata}:/appdata", "-p", f"127.0.0.1:{port}:30440",
       "-e", "LIFECYCLE_FAIL_PAUSE_SECONDS=1", image)
    return name


def test_upgrade_backup_restore_and_failed_migration(tmp_path: Path, appdata: Path) -> None:
    from lifecycle.migrations_util import head_revision

    assert head_revision() == HEAD, "HEAD in deze test bijwerken na een nieuwe migratie"
    v2 = build_variant(tmp_path, "v2", MIGRATION_OK)
    v3 = build_variant(tmp_path, "v3", MIGRATION_FAIL)
    port = free_port()
    names: list[str] = []
    try:
        # v1 met data
        c1 = run(IMAGE, appdata, port)
        names.append(c1)
        ready(port)
        run_id = str(uuid.uuid4())
        psql(c1, "INSERT INTO lifecycle_runs (id, app_id, kind, state, requested_by_type, requested_by_id, trace_id, "
                 f"evidence, version, created_at, updated_at) VALUES ('{run_id}', 'immich', 'update', 'discovered', "
                 f"'user', 'e2e', '{uuid.uuid4()}', '{{}}', 1, now(), now())")
        sh("docker", "stop", c1)

        # v3: migratie faalt → API start niet, data intact, schema HEAD
        c3 = run(v3, appdata, port)
        names.append(c3)
        logs = wait_log(c3, "databasemigratie mislukt")
        assert f"back-up vóór upgrade: voor-upgrade-{HEAD}-" in logs
        assert sh("curl", "-fsS", f"http://127.0.0.1:{port}/readyz", check=False).returncode != 0
        sh("docker", "rm", "-f", c3)
        names.remove(c3)

        # terug naar v1 (alleen tag): start gewoon, data en schema onveranderd
        sh("docker", "start", c1)
        ready(port)
        assert psql(c1, "SELECT version_num FROM alembic_version") == HEAD
        assert psql(c1, f"SELECT count(*) FROM lifecycle_runs WHERE id='{run_id}'") == "1"
        sh("docker", "stop", c1)

        # v2: dump vóór migratie, migratie slaagt
        c2 = run(v2, appdata, port)
        names.append(c2)
        ready(port)
        assert psql(c2, "SELECT version_num FROM alembic_version") == NEXT
        # de host-map is van 568 (0700): lijst via een hulpcontainer, zodat de test geen root op de host nodig heeft
        ls = sh("docker", "run", "--rm", "--network", "none", "--user", "0:0", "-v", f"{appdata}:/x",
                "--entrypoint", "ls", IMAGE, "-1", "/x/backups")
        dumps = sorted(line for line in ls.stdout.splitlines() if line.strip())
        assert dumps and all(d.startswith(f"voor-upgrade-{HEAD}-") for d in dumps) and len(dumps) <= 3
        # de dumps bevatten de hele database: map 0700, bestanden 0600, eigenaar 568
        modes = sh("docker", "run", "--rm", "--network", "none", "--user", "0:0", "-v", f"{appdata}:/x",
                   "--entrypoint", "stat", IMAGE, "-c", "%u %a %n", "/x/backups", *[f"/x/backups/{d}" for d in dumps])
        rows = [line.split() for line in modes.stdout.splitlines()]
        assert rows[0][:2] == ["568", "700"], rows
        assert all(r[:2] == ["568", "600"] for r in rows[1:]), rows
        # hersteltest: dump in een aparte database terugzetten en de data teruglezen
        psql(c2, "CREATE DATABASE herstel", db="postgres")
        r = sh("docker", "exec", "-u", "568", c2, "pg_restore", "-h", "/tmp/lifecycle-pg", "-U", "lifecycle",
               "-d", "herstel", f"/appdata/backups/{dumps[-1]}", check=False)
        assert r.returncode == 0, r.stderr
        assert psql(c2, f"SELECT count(*) FROM lifecycle_runs WHERE id='{run_id}'", db="herstel") == "1"
        assert psql(c2, "SELECT version_num FROM alembic_version", db="herstel") == HEAD
    finally:
        for name in names:
            sh("docker", "rm", "-f", name, check=False)
        for image in (v2, v3):
            sh("docker", "rmi", "-f", image, check=False)
