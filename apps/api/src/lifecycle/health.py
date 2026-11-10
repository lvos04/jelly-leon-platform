"""Gezondheid: ``/healthz`` (proces leeft), ``/readyz`` (afhankelijkheden ok) en connector-health (contract 1.3 §6).

- Onbereikbaar of onbekend is ``unknown``/``down``, nooit ``healthy`` (contract §1, 05_OPERATIES).
- Totaal = de **slechtste** check in de volgorde ``healthy < unknown < degraded < down`` (§6 regel 1, zoals het
  Peil-totaal); ``reason_code`` noemt de slechtste check (eerste in de vaste volgorde).
- Vaste checknamen waar van toepassing (§6 regel 3): ``database``, ``worker`` (``observed_at`` = laatste hartslag) en
  ``hub_link``; eigen namen ``migrations`` en ``secrets``. ``jobs`` is n.v.t. tot de uitvoeringsengine (P2): de worker
  voert in P0 geen jobs uit (ADR-0019).
- Versheid (§6.2): een hartslag ouder dan ``worker_max_age_s`` (3 × het worker-interval, minstens 60 s; zoals de
  heartbeat-regel van §6.1) is een verouderde meting → ``unknown`` met
  ``reason_code: meting_verouderd`` (nooit de oude waarde als actueel). ``discover`` noemt de ``max_age_s``.
- ``/readyz`` hangt niet van de koppeling af (§5), maar meldt in de details de witte lijst van de koppelstatus
  (onleesbaar → ``last_error.code = koppelstatus_onleesbaar``, §3.5).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import FastAPI
from sqlalchemy import select, text

from lifecycle.migrations_util import migration_status
from lifecycle.models import WorkerHeartbeat
from lifecycle.secrets_store import secrets_present
from lifecycle.timeutil import iso_z, utcnow

WORKER_MAX_AGE_S = 60  # ondergrens; de worker slaat standaard elke 5 s
DEFAULT_MAX_AGE_S = 900  # §6.2
RANG = {"healthy": 0, "unknown": 1, "degraded": 2, "down": 3}


def _check(name: str, status: str, detail: str, observed_at: datetime | None = None) -> dict[str, str]:
    return {"name": name, "status": status, "observed_at": iso_z(observed_at or utcnow()) or "", "detail": detail}


def check_database(app: FastAPI) -> dict[str, str]:
    try:
        with app.state.engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return _check("database", "healthy", "ingebouwde PostgreSQL bereikbaar")
    except Exception as exc:
        return _check("database", "down", f"database onbereikbaar ({type(exc).__name__})")


def check_migrations(app: FastAPI) -> dict[str, str]:
    try:
        current, head = migration_status(app.state.engine)
    except Exception as exc:
        return _check("migrations", "unknown", f"schemaversie niet te bepalen ({type(exc).__name__})")
    if current == head:
        return _check("migrations", "healthy", f"schema op {head}")
    return _check("migrations", "down", f"schema op {current or 'leeg'}, verwacht {head}")


def check_secrets(app: FastAPI) -> dict[str, str]:
    if secrets_present(app.state.settings.secrets_dir):
        return _check("secrets", "healthy", "geheimen aanwezig in /appdata/secrets")
    return _check("secrets", "down", "geheimen ontbreken; eerste-start-initialisatie niet uitgevoerd")


def worker_max_age_s(app: FastAPI) -> int:
    return max(WORKER_MAX_AGE_S, 3 * int(app.state.settings.worker_interval_seconds))


def check_worker(app: FastAPI) -> dict[str, str]:
    try:
        with app.state.session_factory() as session:
            beat = session.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.name == "worker")).scalar_one_or_none()
    except Exception as exc:
        return _check("worker", "unknown", f"hartslag niet leesbaar ({type(exc).__name__})")
    if beat is None:
        return _check("worker", "unknown", "nog geen hartslag van de worker")
    age = utcnow() - beat.beat_at
    if age > timedelta(seconds=worker_max_age_s(app)):  # verouderd (§6.2): onbekend, oude waarde nooit als actueel
        return _check("worker", "unknown", f"meting verouderd: laatste hartslag {int(age.total_seconds())} s geleden",
                      beat.beat_at)
    return _check("worker", "healthy", f"laatste hartslag {int(age.total_seconds())} s geleden", beat.beat_at)


def check_hub_link(app: FastAPI) -> dict[str, str]:
    try:
        link = app.state.hub_link.read()
    except Exception as exc:
        return _check("hub_link", "unknown", f"koppelstatus niet leesbaar ({type(exc).__name__})")
    if link.onleesbaar:
        return _check("hub_link", "down", "koppelbestand onleesbaar; opnieuw koppelen via de Hub (nieuw token)")
    if link.state == "gekoppeld":
        if link.doc.get("confirmed_at"):
            return _check("hub_link", "healthy", "gekoppeld met de Hub en bevestigd")
        return _check("hub_link", "degraded", "gekoppeld; bevestiging bij de Hub (services/self) volgt")
    if link.state == "ingetrokken":
        return _check("hub_link", "down", "ingetrokken door de Hub; opnieuw koppelen via de Hub")
    if link.pending is not None:
        return _check("hub_link", "unknown", "koppelen met de Hub is bezig")
    if link.state == "ontkoppeld":
        return _check("hub_link", "unknown", "ontkoppeld door de Hub")
    return _check("hub_link", "unknown", "niet gekoppeld met de Hub")


def _hub_reason(app: FastAPI) -> str:
    try:
        link = app.state.hub_link.read()
    except Exception:
        return "hub_link_unknown"
    if link.onleesbaar:
        return "koppelstatus_onleesbaar"
    if link.state == "ingetrokken":
        return "hub_revoked"
    if link.state == "gekoppeld":
        return "hub_unconfirmed"
    if link.pending is not None:
        return "koppelen_bezig"
    if link.state == "ontkoppeld":
        return "hub_unlinked"
    return "hub_not_linked"


def collect_health(app: FastAPI) -> dict[str, Any]:
    """§6: totaal = slechtste check; ``reason_code`` van de eerste slechtste check (vaste volgorde)."""
    checks = [check_database(app), check_migrations(app), check_secrets(app), check_worker(app), check_hub_link(app)]
    worst = max(checks, key=lambda c: RANG[c["status"]])
    status = worst["status"]
    reason: str | None = None
    if status != "healthy":
        if worst["name"] == "hub_link":
            reason = _hub_reason(app)
        elif worst["name"] == "worker" and worst["detail"].startswith("meting verouderd"):
            reason = "meting_verouderd"
        else:
            reason = f"{worst['name']}_{status}"
    return {"status": status, "observed_at": iso_z(utcnow()), "reason_code": reason, "checks": checks}


def readiness(app: FastAPI) -> tuple[bool, list[dict[str, str]]]:
    checks = [check_database(app), check_migrations(app), check_secrets(app)]
    return all(c["status"] == "healthy" for c in checks), checks


def hub_link_details(app: FastAPI) -> dict[str, Any]:
    """Voor ``/readyz``: een deel van de witte lijst (§3.5; minder tonen mag), zonder invloed op de gereedheid."""
    try:
        view = app.state.hub_link.read().public()
    except Exception as exc:
        return {"state": None, "koppelen_bezig": False,
                "last_error": {"code": "koppelstatus_onleesbaar", "message_nl": f"niet leesbaar ({type(exc).__name__})",
                               "at": iso_z(utcnow())}}
    return {"state": view["state"], "koppelen_bezig": view["koppelen_bezig"], "last_error": view["last_error"]}
