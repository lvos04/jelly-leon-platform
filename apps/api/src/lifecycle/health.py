"""Gezondheid: ``/healthz`` (proces leeft), ``/readyz`` (afhankelijkheden ok) en connector-health.

Onbereikbaar of onbekend is ``unknown``/``down``, nooit ``healthy`` (contract §1, 05_OPERATIES).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import FastAPI
from sqlalchemy import select, text

from lifecycle.migrations_util import migration_status
from lifecycle.models import WorkerHeartbeat
from lifecycle.secrets_store import secrets_present
from lifecycle.timeutil import iso_z, utcnow

WORKER_STALE_AFTER = timedelta(seconds=60)


def _check(name: str, status: str, detail: str) -> dict[str, str]:
    return {"name": name, "status": status, "observed_at": iso_z(utcnow()) or "", "detail": detail}


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


def check_worker(app: FastAPI) -> dict[str, str]:
    try:
        with app.state.session_factory() as session:
            beat = session.execute(select(WorkerHeartbeat).where(WorkerHeartbeat.name == "worker")).scalar_one_or_none()
    except Exception as exc:
        return _check("worker", "unknown", f"hartslag niet leesbaar ({type(exc).__name__})")
    if beat is None:
        return _check("worker", "unknown", "nog geen hartslag van de worker")
    age = utcnow() - beat.beat_at
    if age > WORKER_STALE_AFTER:
        return _check("worker", "down", f"laatste hartslag {int(age.total_seconds())} s geleden")
    return _check("worker", "healthy", f"laatste hartslag {int(age.total_seconds())} s geleden")


def check_hub_link(app: FastAPI) -> dict[str, str]:
    try:
        link = app.state.hub_link.read()
    except Exception as exc:
        return _check("hub_link", "unknown", f"koppelstatus niet leesbaar ({type(exc).__name__})")
    if link.corrupt:
        return _check("hub_link", "down", "koppelbestand onleesbaar; opnieuw koppelen via de Hub (nieuw token)")
    if link.state == "gekoppeld":
        if link.doc.get("confirmed_at"):
            return _check("hub_link", "healthy", "gekoppeld met de Hub en bevestigd")
        return _check("hub_link", "degraded", "gekoppeld; bevestiging bij de Hub (services/self) volgt")
    if link.state == "ingetrokken":
        return _check("hub_link", "down", "ingetrokken door de Hub; opnieuw koppelen via de Hub")
    if link.pending is not None:
        return _check("hub_link", "unknown", "koppelen met de Hub is bezig")
    return _check("hub_link", "unknown", "niet gekoppeld met de Hub")


def _hub_reason(app: FastAPI) -> str:
    try:
        link = app.state.hub_link.read()
    except Exception:
        return "hub_unknown"
    if link.corrupt:
        return "hub_link_corrupt"
    if link.state == "ingetrokken":
        return "hub_revoked"
    if link.state == "gekoppeld":
        return "hub_unconfirmed"
    return "hub_not_linked"


def collect_health(app: FastAPI) -> dict[str, Any]:
    checks = [check_database(app), check_migrations(app), check_secrets(app), check_worker(app), check_hub_link(app)]
    by_name = {c["name"]: c["status"] for c in checks}
    critical = ("database", "migrations", "secrets")
    if any(by_name[name] == "down" for name in critical):
        status, reason = "down", next(n for n in critical if by_name[n] == "down") + "_down"
    elif any(by_name[name] == "unknown" for name in critical):
        status, reason = "unknown", "critical_unknown"
    elif by_name["worker"] != "healthy":
        status, reason = "degraded", "worker_" + by_name["worker"]
    elif by_name["hub_link"] != "healthy":
        status, reason = "degraded", _hub_reason(app)
    else:
        status, reason = "healthy", None
    return {"status": status, "observed_at": iso_z(utcnow()), "reason_code": reason, "checks": checks}


def readiness(app: FastAPI) -> tuple[bool, list[dict[str, str]]]:
    checks = [check_database(app), check_migrations(app), check_secrets(app)]
    return all(c["status"] == "healthy" for c in checks), checks
