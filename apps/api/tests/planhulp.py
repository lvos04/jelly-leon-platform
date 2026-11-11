"""Gedeelde testgegevens voor de planner (B08-003/B08-004). Alle namen, paden en versies zijn **testwaarden**
(``pool-x`` bestaat nergens); de NAS-gegevens D01–D18 zijn onbekend."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hub_p2 import app_record

from appstore.manifest.conflicts import Inventory
from lifecycle.hubread import Beschikbaarheid, Inventaris, installed_app
from lifecycle.planner import Doel

ROOT = Path(__file__).resolve().parents[3]
NU = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
DOEL = Doel("25.04.2.4", "1.4.8")
DOEL_JSON = {"truenas_version": "25.04.2.4", "ix_app_version": "1.4.8"}
GOTIFY_REPO = "ghcr.io/gotify/server"
GOTIFY_DIGEST = "sha256:44fc5bbd1c0618878e33073d38c2c13126b48f245c3fe12236b1da48eb203b86"
OUDE_DIGEST = "sha256:" + "a1" * 32
GOTIFY = {"host_port": 30215, "data_path": "/mnt/pool-x/apps/gotify", "timezone": "Europe/Amsterdam",
          "rpo": "PT24H", "rto": "PT4H"}
SCHADUW = {"host_port": 30216}


def manifest(naam: str) -> str:
    return (ROOT / "install" / "manifests" / f"{naam}.manifest.yaml").read_text(encoding="utf-8")


def gotify_record(*, tag: str = "3.0.0", digest: str | None = OUDE_DIGEST, host_port: int = 30215,
                  dataset: str | None = "pool-x/apps/gotify", naam: str = "gotify") -> dict[str, Any]:
    return app_record(naam, repository=GOTIFY_REPO, tag=tag, digest=digest, app_id="gotify",
                      poorten=[(8080, host_port, "tcp")], opslag=[("/mnt/pool-x/apps/gotify", "/app/data", dataset)])


def inventaris(*records: dict[str, Any]) -> Inventaris:
    """Een verse, gemeten inventaris in de vorm die ``HubLezer.inventaris`` oplevert."""
    per_naam = {str(r["naam"]): r for r in records}
    return Inventaris("ok", None, Inventory(observed_at=NU, source="hub_snapshot",
                                            apps=[installed_app(r) for r in records]),
                      per_naam, "2026-11-11T12:00:00Z", "meting-1")


def meting(waarde: str, status: str = "ok", reden: str | None = None) -> Any:
    def lees(train: str, app: str, version: str) -> Beschikbaarheid:
        return Beschikbaarheid(waarde, status, reden, "2026-11-11T11:59:00Z" if waarde != "onbekend" else None,
                               train, app, version)

    return lees


def codes(document: dict[str, Any]) -> set[str]:
    return {b["code"] for b in document["blokkades"]}


def stap(document: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next(s for s in document["stappen"] if s["id"] == step_id)


def status(voortgang: list[dict[str, Any]]) -> dict[str, str]:
    return {s["id"]: s["status"] for s in voortgang}
