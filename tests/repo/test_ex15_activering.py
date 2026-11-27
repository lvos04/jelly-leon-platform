"""De losse activatiefase mag pas na vergelijking met de geladen database namen wisselen."""

import hashlib
import os
import subprocess

import pytest

from e2e.integral.scenarios.EX15_schema_terugzetten import HERSTEL


@pytest.mark.parametrize(("geladen", "verwacht"), [("ander", 42), ("ontbreekt", 42), ("zelfde", 0)])
def test_controle_komt_voor_hernoemen(tmp_path, geladen, verwacht):
    archief = tmp_path / "later.dump"
    archief.write_bytes(b"synthetisch dumpbestand")
    sha = hashlib.sha256(archief.read_bytes()).hexdigest()
    marker = tmp_path / "hernoemd"
    for naam, script in {
        "pg_ctl": "exit 0",
        "psql": """
case "$*" in
    *"current_setting('ex15.archief_sha256', true)"*) echo "$GELADEN" ;;
    *"RENAME TO lifecycle"*) touch "$MARKER" ;;
    *) exit 9 ;;
esac
""",
    }.items():
        uitvoerbaar = tmp_path / naam
        uitvoerbaar.write_text("#!/bin/sh\n" + script)
        uitvoerbaar.chmod(0o700)
    geladen_sha = {"ander": hashlib.sha256(b"ander").hexdigest(), "ontbreekt": "", "zelfde": sha}[geladen]
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}", "GELADEN": geladen_sha, "MARKER": str(marker)}
    code = subprocess.run(["sh", "-ec", HERSTEL.replace("/appdata", str(tmp_path)), "proef", "activeren",
                           str(archief), sha], env=env, capture_output=True).returncode
    assert code == verwacht and marker.exists() == (geladen == "zelfde")
