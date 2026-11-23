"""ALLEEN ONTWIKKELING/TEST — geen installatiemethode. JP10: draai ``tests/conformance`` tegen de **echte** Hub.

Start de Hub (``ai-server-manager``: API én worker) uit een naburige checkout met zijn eigen venv, met een
wegwerp-datamap in /tmp en een eigen database met het voorvoegsel ``jlp_`` op de lokale PostgreSQL. Maakt het eerste
beheerdersaccount met de setupcode uit de log en verbindt de Hub via de gewone wizardroutes (D03, alleen-lezen-key,
TLS-vingerafdruk) met de **TrueNAS-simulator** van deze repo (de enige nagebootste bron), met een bestaande app op
hostpoort 38080 en een dataset voor de nieuwe app. Daarna inventaris vernieuwen en catalogus bijwerken; de
catalogusbeschikbaarheid meet de worker van de Hub zelf. Vraagt drie enrollment-tokens voor ``jelly-leon-platform``
aan, draait de conformiteitstests met ``JLP_HUB_URL``/``JLP_HUB_ENROLLMENT_TOKEN``/``JLP_HUB_KEY_FINGERPRINT`` (en
``JLP_TRUENAS_SIM_URL`` voor de planproef, S-P2-420) en ruimt alles op. Leest alleen in de andere repo; schrijft daar
niets. Sim-getest; NIET GEVALIDEERD (echte NAS).

    python e2e/conformance/echte_hub.py [--hub-repo ../ai-server-manager] [--pg postgresql+psycopg://postgres@127.0.0.1:5432]
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from rooktest import maak_certificaat  # noqa: E402

BESTAANDE_POORT = 38080  # bezet door de bestaande simulatorapp: de planproef verwacht hier port_in_use


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def koppel_nas(c: httpx.Client, csrf: str, sim_port: int, readonly_key: str, fingerprint: str,
               start_worker: Callable[[], None]) -> None:
    """De wizard van de Hub tegen de simulator (zoals ``tools/rooktest.py``, stappen W4, W5 en W7). De worker start pas
    na de koppeling: een catalogusmeting zonder koppeling telt als poging en de volgende mag pas na 300 s."""
    def idem() -> dict[str, str]:
        return {"X-CSRF-Token": csrf, "Idempotency-Key": str(uuid.uuid4())}

    d03 = c.get("/api/v1/decisions/D03")
    c.put("/api/v1/decisions/D03/waarde", json={"waarde": {"nas_adres": "127.0.0.1", "https_poort": sim_port}},
          headers={**idem(), "If-Match": d03.headers.get("etag", "")}).raise_for_status()
    c.put("/api/v1/truenas/api-key", json={"api_key": readonly_key}, headers=idem()).raise_for_status()
    c.post("/api/v1/truenas/test", headers=idem()).raise_for_status()  # zelfondertekend: eerst tls_untrusted
    c.post("/api/v1/truenas/tls/trust", json={"fingerprint_sha256": fingerprint}, headers=idem()).raise_for_status()
    test = c.post("/api/v1/truenas/test", headers=idem()).json()
    if test.get("verbonden") is not True:
        raise RuntimeError(f"Hub niet verbonden met de simulator: {test.get('reason_code')}")
    start_worker()
    job = c.post("/api/v1/inventory/refresh", headers=idem()).json()
    job_id = job.get("job_id") or job.get("id")
    for _ in range(120):
        status = c.get(f"/api/v1/jobs/{job_id}").json().get("status")
        if status in ("done", "failed"):
            break
        time.sleep(0.5)
    if status != "done":
        raise RuntimeError(f"inventaris vernieuwen: {status}")
    c.post("/api/v1/apps/discover", headers=idem()).raise_for_status()


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
    commit = subprocess.run(["git", "-C", str(hub_repo), "rev-parse", "HEAD"], capture_output=True,
                            text=True, check=False).stdout.strip()
    db_name = f"jlp_conf_hub_{secrets.token_hex(4)}"
    admin = sa.create_engine(f"{args.pg}/postgres", isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(sa.text(f'CREATE DATABASE "{db_name}"'))
    data = Path(tempfile.mkdtemp(prefix="jlp-echte-hub-"))
    sim_dir = Path(tempfile.mkdtemp(prefix="jlp-echte-hub-sim-"))
    port = free_port()
    env = {**os.environ, "HUB_DATA_DIR": str(data), "HUB_DATABASE_URL": f"{args.pg}/{db_name}",
           "HUB_PORT": str(port), "HUB_HOST": "127.0.0.1", "HUB_AI_ENABLED": "false", "PYTHONUNBUFFERED": "1",
           "HUB_WORKER_POLL_SECONDS": "1"}
    api: subprocess.Popen[str] | None = None
    procs: list[subprocess.Popen[str]] = []
    try:
        sim_port = free_port()
        cert, key, fp = maak_certificaat(sim_dir, "127.0.0.1")
        procs.append(subprocess.Popen(
            [sys.executable, "-m", "truenas_sim", "--host", "127.0.0.1", "--port", str(sim_port), "--version",
             "25.10.8", "--ssl-certfile", str(cert), "--ssl-keyfile", str(key)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True))
        sim_url = f"https://127.0.0.1:{sim_port}"
        sim = httpx.Client(base_url=sim_url, verify=ssl.create_default_context(cafile=str(cert)), trust_env=False,
                           timeout=10)
        for _ in range(60):
            try:
                if sim.get("/api/versions").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.3)
        sim.post("/_sim/fixtures", json={"datasets": ["simpool/apps/gotify"], "apps": [{
            "app_name": "bestaande-app", "catalog_app": "ix-app", "train": "stable", "version": "1.4.8",
            "values": {"image": {"repository": "ghcr.io/sim/bestaande-app", "tag": "1.0.0"},
                       "ports": [{"bind_mode": "published", "port_number": BESTAANDE_POORT, "container_port": 8080,
                                  "protocol": "tcp", "host_ips": []}],
                       "storage": [{"type": "host_path", "mount_path": "/config", "read_only": False,
                                    "host_path_config": {"acl_enable": False,
                                                         "path": "/mnt/simpool/apps/bestaande-app"}}]}}]}
                 ).raise_for_status()
        readonly_key = sim.get("/_sim/keys").json()["readonly"]
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
                               env=env, cwd=hub_repo / "apps" / "api", stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, text=True)
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
            def start_worker() -> None:
                procs.append(subprocess.Popen([str(hub_python), "-m", "hub", "worker"], env=env,
                                              cwd=hub_repo / "apps" / "api", stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL, text=True))

            koppel_nas(c, csrf, sim_port, readonly_key, fp, start_worker)
            grants = []
            for _ in range(3):  # conformiteitsstroom, koppeltest met de eigen worker, planproef (S-P2-420)
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
                    "JLP_HUB_ENROLLMENT_TOKEN_3": grants[2]["enrollment_token"],
                    "JLP_HUB_SESSION_COOKIE": session_cookie, "JLP_HUB_CSRF": csrf, "JLP_TRUENAS_SIM_URL": sim_url,
                    "JLP_TRUENAS_SIM_CA": str(cert),
                    "JLP_HUB_COMMIT": commit}
        return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-rs",
                               str(ROOT / "tests" / "conformance")], env=test_env, check=False).returncode
    finally:
        for proc in procs:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
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
        shutil.rmtree(sim_dir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
