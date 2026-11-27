"""Testgegevens en behoud van voortgang in het eigen MediaGuard-volume."""

from __future__ import annotations

import json
import os
import secrets
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from e2e.integral.bewijs import schrijf
from e2e.integral.scenarios.conftest import Kern, wacht

# Zonder CREATE, in één transactie. Na een schone stop kan SQLite de WAL hebben verwijderd.
SQL = """
import json, sqlite3, sys
from pathlib import Path
mode, stappen = json.load(sys.stdin)
pad = Path('/config/core/mediaguard.db')
url = pad.as_uri() + '?mode=' + ('rw' if mode == 'rw' else 'ro')
wal = pad.with_suffix('.db-wal')
if mode == 'gestopt' and not (wal.exists() and wal.stat().st_size):
    url += '&immutable=1'
db = sqlite3.connect(url, uri=True, timeout=10)
db.execute('BEGIN IMMEDIATE' if mode == 'rw' else 'BEGIN')
result = [db.execute(query, args).fetchall() for query, args in stappen]
db.commit()
print(json.dumps(result))
"""
Stap = tuple[str, tuple[Any, ...]]


def sql(kern: Kern, *stappen: Stap, schrijven: bool = False, gestopt: bool = False) -> list[list[list[Any]]]:
    project = kern.net.project
    if gestopt:
        assert not schrijven and not draait(kern, "mediaguard")
        args = ["run", "--rm", "-T", "--no-deps", "--volume", f"{project.naam}_mediaguard:/config:ro",
                "--user", "568:568", "--entrypoint", "python", "truenas"]
    else:
        args = ["exec", "-T", "--user", "568:568", "mediaguard", "/opt/venv/bin/python"]
    modus = "gestopt" if gestopt else "rw" if schrijven else "ro"
    raw = project.compose(*args, "-c", SQL, invoer=json.dumps([modus, stappen]))
    resultaat: list[list[list[Any]]] = json.loads(raw)
    return resultaat


def inloggen(kern: Kern, dienst: str) -> httpx.Client:
    mg = dienst == "mediaguard"
    client = kern.client(dienst, 30080 if mg else 38081)
    pad = "/api/v1/auth/setup-status" if mg else "/api/v1/setup/status"
    wacht(dienst, lambda: client.get(pad).raise_for_status().is_success)
    antwoord = client.post("/api/v1/auth/setup" if mg else "/api/v1/setup/admin",
                           json={"username": "proefbeheer", "password": "Proef-" + secrets.token_urlsafe(24)})
    client.headers["X-CSRF-Token"] = antwoord.raise_for_status().json()["csrf_token"]
    return client


def verbind_mso(mso: httpx.Client, mg: httpx.Client) -> None:
    sleutel = mg.post("/api/v1/auth/api-keys", json={"name": "mso-proef", "role": "operator"}).raise_for_status()
    mso.put("/api/v1/connectors/mediaguard", json={
        "url": str(mg.base_url).rstrip("/"), "enabled": True, "options": {"auth_method": "apikey"},
        "secrets": {"api_key": sleutel.json()["key"]},
    }).raise_for_status()


def draait(kern: Kern, dienst: str) -> bool:
    return dienst in kern.net.project.compose("ps", "--status", "running", "--services", dienst).splitlines()


@contextmanager
def uitval(kern: Kern, dienst: str) -> Iterator[None]:
    assert draait(kern, dienst)
    stoppen = os.environ.get("INTEGRAAL_ZONDER_STORING") != "1"
    if stoppen:
        kern.net.project.compose("stop", "--timeout", "30", dienst)
    try:
        yield
    finally:
        if stoppen:
            kern.net.project.compose("start", dienst)
            kern.net.project.controleer_images((dienst,))


def bestaande_rijen(kern: Kern, *, gestopt: bool = False) -> dict[str, dict[Any, tuple[Any, ...]]]:
    # Primaire sleutels onderscheiden verlies van groei, ook bij gelijkblijvende aantallen.
    tabellen = {"jobs": "id", "job_logs": "id", "audit_events": "id",
                "mediaserver_watch": "path,title,user_name,last_seen"}
    stappen: list[Stap] = [(f"SELECT {kolom} FROM {tabel}", ()) for tabel, kolom in tabellen.items()]  # noqa: S608
    data = sql(kern, *stappen, gestopt=gestopt)
    return {naam: {r[0]: tuple(r[1:]) for r in rijen} for naam, rijen in zip(tabellen, data, strict=True)}


# Hetzelfde ingeplande werk aanbieden na herstel gebruikt de eigen idempotentie van de MediaGuard-wachtrij.
PLAN_WERK = """
from datetime import datetime, UTC, timedelta
from pathlib import Path
from mediaguard.db import Database
from mediaguard.jobs import enqueue
db = Database(Path('/config/core/mediaguard.db'))
print(enqueue(db, 'library.sync', {'full': True}, instance_id='sonarr-series', target_id='proefreeks',
              idempotency_key='herhaalbare-scenarioproef', run_after=datetime.now(UTC) + timedelta(days=1)))
"""


def plan_werk(kern: Kern) -> int:
    return int(kern.net.project.compose("exec", "-T", "--user", "568:568", "mediaguard", "/opt/venv/bin/python",
                                        "-c", PLAN_WERK).strip())


@contextmanager
def behoud(kern: Kern, mg: httpx.Client, job: int) -> Iterator[Callable[[str, bool], None]]:
    sql(kern, ("INSERT INTO job_logs(job_id,ts,level,message) VALUES (?,CURRENT_TIMESTAMP,'info','Proefreeks gepland')",
               (job,)), ("INSERT INTO mediaserver_watch(path,title,user_name,last_seen) "
                         "VALUES ('/data/proefreeks.mkv','Proefreeks','Proefkijker',CURRENT_TIMESTAMP)", ()),
        schrijven=True)
    vooraf = bestaande_rijen(kern)
    pad = kern.net.dossier / "voortgang.json"
    meting = {naam: {"voor": len(ids)} for naam, ids in vooraf.items()}
    schrijf(pad, meting)
    assert all(vooraf.values())

    def meet(moment: str, gestopt: bool = False) -> None:
        nu = bestaande_rijen(kern, gestopt=gestopt)
        for naam, oud in vooraf.items():
            meting[naam][moment] = len(nu[naam])
            meting[naam]["verdwenen_" + moment] = len(oud.keys() - nu[naam].keys())
        kijk = "mediaserver_watch"
        gewijzigd = sum(vooraf[kijk][k] != nu[kijk][k] for k in vooraf[kijk].keys() & nu[kijk].keys())
        meting[kijk]["gewijzigd_" + moment] = gewijzigd
        schrijf(pad, meting)
        assert all(oud.keys() <= nu[naam].keys() for naam, oud in vooraf.items()), "MediaGuard-voortgang verdwenen"
        assert gewijzigd == 0, "Bestaande kijkgegevens gewijzigd"

    try:
        yield meet
    finally:
        wacht("MediaGuard na de proef", lambda: mg.get("/api/v1/health").raise_for_status().json()["status"] == "ok")
        meet("na")
