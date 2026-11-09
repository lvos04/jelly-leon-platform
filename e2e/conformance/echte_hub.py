"""ALLEEN ONTWIKKELING/TEST — geen installatiemethode. JP10: draai ``tests/conformance`` tegen de **echte** Hub.

Start de Hub (``ai-server-manager``) uit een naburige checkout met zijn eigen venv, met een wegwerp-datamap in /tmp en
een eigen database met het voorvoegsel ``jlp_`` op de lokale PostgreSQL. Maakt het eerste beheerdersaccount met de
setupcode uit de log, vraagt een enrollment-token voor ``jelly-leon-platform`` aan, draait de conformiteitstests met
``JLP_HUB_URL``/``JLP_HUB_ENROLLMENT_TOKEN``/``JLP_HUB_KEY_FINGERPRINT`` en ruimt alles op. Leest alleen in de andere
repo; schrijft daar niets.

    python e2e/conformance/echte_hub.py [--hub-repo ../ai-server-manager] [--pg postgresql+psycopg://postgres@127.0.0.1:5432]
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub-repo", default=str(ROOT.parent / "ai-server-manager"))
    parser.add_argument("--pg", default="postgresql+psycopg://postgres@127.0.0.1:5432")
    args = parser.parse_args()
    hub_repo = Path(args.hub_repo).resolve()
    hub_python = hub_repo / ".venv" / "bin" / "python"
    if not hub_python.exists():
        print(f"geen venv in {hub_repo} (verwacht {hub_python})", file=sys.stderr)
        return 2
    commit = subprocess.run(["git", "-C", str(hub_repo), "rev-parse", "--short", "HEAD"], capture_output=True,
                            text=True, check=False).stdout.strip()
    db_name = f"jlp_conf_hub_{secrets.token_hex(4)}"
    admin = sa.create_engine(f"{args.pg}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))
    data = Path(tempfile.mkdtemp(prefix="jlp-echte-hub-"))
    port = free_port()
    env = {**os.environ, "HUB_DATA_DIR": str(data), "HUB_DATABASE_URL": f"{args.pg}/{db_name}",
           "HUB_PORT": str(port), "HUB_HOST": "127.0.0.1", "HUB_AI_ENABLED": "false", "PYTHONUNBUFFERED": "1"}
    api: subprocess.Popen[str] | None = None
    try:
        for cmd in (["migrate"], ["init"]):
            r = subprocess.run([str(hub_python), "-m", "hub", *cmd], env=env, cwd=hub_repo / "apps" / "api",
                               capture_output=True, text=True, check=False)
            if r.returncode != 0:
                print(r.stdout + r.stderr, file=sys.stderr)
                return 3
            if cmd == ["init"]:
                m = re.search(r"SETUPCODE \(eenmalig[^)]*\): (\S+)", r.stdout)
                if not m:
                    print("geen setupcode in de uitvoer van 'hub init'", file=sys.stderr)
                    return 3
                setup_code = m.group(1)
        api = subprocess.Popen([str(hub_python), "-m", "hub", "api", "--host", "127.0.0.1", "--port", str(port)],
                               env=env, cwd=hub_repo / "apps" / "api", stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True)
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base, trust_env=False, timeout=15) as c:
            for _ in range(60):
                try:
                    if c.get("/healthz").status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.5)
            password = "Conformiteit-" + secrets.token_urlsafe(18)
            r = c.post("/api/v1/setup/admin", json={"setup_code": setup_code, "username": "conformiteit",
                                                     "password": password})
            r.raise_for_status()
            csrf = r.json()["csrf_token"]
            grants = []
            for _ in range(2):  # één voor de conformiteitsstroom, één voor de koppeltest met de eigen worker
                r = c.post("/api/v1/services/enrollment-tokens", json={"app_id": "jelly-leon-platform"},
                           headers={"X-CSRF-Token": csrf})
                r.raise_for_status()
                grants.append(r.json())
            session_cookie = c.cookies.get("hub_session") or ""
        grant = grants[0]
        print(f"echte Hub: ai-server-manager {commit} op {base}; vingerafdruk {grant['keyset_fingerprint']}",
              flush=True)
        test_env = {**os.environ, "JLP_HUB_URL": base, "JLP_HUB_ENROLLMENT_TOKEN": grant["enrollment_token"],
                    "JLP_HUB_KEY_FINGERPRINT": grant["keyset_fingerprint"],
                    "JLP_HUB_ENROLLMENT_TOKEN_2": grants[1]["enrollment_token"],
                    "JLP_HUB_SESSION_COOKIE": session_cookie, "JLP_HUB_CSRF": csrf}
        return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs",
                               str(ROOT / "tests" / "conformance")], env=test_env, check=False).returncode
    finally:
        if api is not None:
            api.terminate()
            try:
                api.wait(10)
            except subprocess.TimeoutExpired:
                api.kill()
        with admin.connect() as conn:
            conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{db_name}" WITH (FORCE)'))
        admin.dispose()
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
