"""Testgegevens en behoud van voortgang in het eigen MediaGuard-volume."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from collections.abc import Callable, Iterable, Iterator
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
    tabellen = {"jobs": "id,progress,status", "job_logs": "id,job_id,ts,level,message", "audit_events": "id",
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


PROEFVOORTGANG = 0.625
PROEFMELDING = "Vijf van de acht proefafleveringen verwerkt"


def voeg_proefgegevens_toe(kern: Kern, job: int) -> tuple[int, int]:
    # Een afzonderlijke gepauzeerde job laat de werker en de bestaande scenariojob ongemoeid.
    _, _, proefjobs, proefregels = sql(
        kern,
        ("INSERT INTO job_logs(job_id,ts,level,message) VALUES (?,CURRENT_TIMESTAMP,'info','Proefreeks gepland')",
         (job,)),
        ("INSERT INTO mediaserver_watch(path,title,user_name,last_seen) "
         "VALUES ('/data/proefreeks.mkv','Proefreeks','Proefkijker',CURRENT_TIMESTAMP)", ()),
        ("INSERT INTO jobs(job_type,status,progress,created_at) "
         "VALUES ('library.sync','paused',?,CURRENT_TIMESTAMP) RETURNING id", (PROEFVOORTGANG,)),
        ("INSERT INTO job_logs(job_id,ts,level,message) "
         "VALUES (last_insert_rowid(),CURRENT_TIMESTAMP,'info',?) RETURNING id", (PROEFMELDING,)), schrijven=True)
    return int(proefjobs[0][0]), int(proefregels[0][0])


def logafdrukken(rijen: dict[Any, tuple[Any, ...]], ids: Iterable[int]) -> dict[int, str]:
    return {k: hashlib.sha256(json.dumps(rijen[k]).encode()).hexdigest() for k in ids if k in rijen}


@contextmanager
def behoud(kern: Kern, mg: httpx.Client, job: int) -> Iterator[Callable[[str, bool], None]]:
    proefjob, proefregel = voeg_proefgegevens_toe(kern, job)
    vooraf = bestaande_rijen(kern)
    pad = kern.net.dossier / "voortgang.json"
    meting: dict[str, dict[str, Any]] = {naam: {"voor": len(ids)} for naam, ids in vooraf.items()}
    vorige_stand = {k: rij[0] for k, rij in vooraf["jobs"].items()}
    logjob, tijd, niveau, tekst = vooraf["job_logs"][proefregel]
    proefregel_intact = logjob == proefjob and bool(tijd) and niveau == "info" and tekst == PROEFMELDING
    meting["jobs"].update(proefjob=proefjob, voortgang_voor=vorige_stand)
    historie = vooraf["job_logs"]
    meting["job_logs"].update(proefregel=proefregel, proefregel_intact_voor=proefregel_intact,
                              afdrukken_voor=logafdrukken(historie, historie))
    schrijf(pad, meting)
    assert all(vooraf.values())
    assert vooraf["jobs"][proefjob] == (PROEFVOORTGANG, "paused") and proefregel_intact, "Beginstand proefgegevens"

    def meet(moment: str, gestopt: bool = False) -> None:
        nonlocal vorige_stand
        nu = bestaande_rijen(kern, gestopt=gestopt)
        for naam, oud in vooraf.items():
            meting[naam][moment] = len(nu[naam])
            meting[naam]["verdwenen_" + moment] = len(oud.keys() - nu[naam].keys())
        for naam in ("job_logs", "mediaserver_watch"):
            meting[naam]["gewijzigd_" + moment] = sum(
                vooraf[naam][k] != nu[naam][k] for k in vooraf[naam].keys() & nu[naam].keys())
        stand = {k: nu["jobs"][k][0] for k in vorige_stand if k in nu["jobs"]}
        teruggevallen = sum(waarde < vorige_stand[k] for k, waarde in stand.items())
        meting["jobs"]["voortgang_" + moment] = stand
        meting["jobs"]["teruggevallen_" + moment] = teruggevallen
        meting["job_logs"]["afdrukken_" + moment] = logafdrukken(nu["job_logs"], historie)
        meting["job_logs"]["proefregel_intact_" + moment] = nu["job_logs"].get(proefregel) == historie[proefregel]
        schrijf(pad, meting)
        assert all(oud.keys() <= nu[naam].keys() for naam, oud in vooraf.items()), "MediaGuard-voortgang verdwenen"
        assert teruggevallen == 0, "Bestaande jobvoortgang teruggevallen"
        assert all(0 <= waarde <= 1 for waarde in stand.values()), "Jobvoortgang buiten bereik"
        assert nu["jobs"][proefjob] == vooraf["jobs"][proefjob], "Gepauzeerde proefjob gewijzigd"
        assert meting["job_logs"]["gewijzigd_" + moment] == 0, "Bestaande jobhistorie gewijzigd"
        assert meting["mediaserver_watch"]["gewijzigd_" + moment] == 0, "Bestaande kijkgegevens gewijzigd"
        vorige_stand = stand

    try:
        yield meet
    finally:
        wacht("MediaGuard na de proef", lambda: mg.get("/api/v1/health").raise_for_status().json()["status"] == "ok")
        meet("na")
