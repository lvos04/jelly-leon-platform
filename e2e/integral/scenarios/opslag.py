"""Testgegevens, back-upvoorwaarden en voorstellen voor de gekoppelde Hub en executor."""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from typing import Any

import httpx

from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.hubveiligheid import appfixture, richt_schrijfpad_in, vernieuw

BRON = "simpool/apps/proef-config"
KLOON = "simpool/asm-sandbox/proef-config"
SNAP = BRON + "@voor-proef"
MEDIA = "simpool/apps/proef-media"
FOTO = "P2\n1 1\n255\n42\n"
EXPORT = "CREATE TABLE voortgang(positie INTEGER); INSERT INTO voortgang VALUES (42);"
BESTANDEN = {"proef.pgm": FOTO, "export.sql": EXPORT}


def besluit(kern: Kern, nummer: str, waarde: dict[str, Any]) -> None:
    pad = "/api/v1/decisions/" + nummer
    etag = kern.hub.get(pad).raise_for_status().headers["etag"]
    kern.wijzig("PUT", pad + "/waarde", headers={"If-Match": etag}, json={"waarde": waarde})


def kopieer_taak(kern: Kern, schijf: httpx.Client) -> None:
    taken = schijf.get("/_sim/cloudsync_taken").raise_for_status().json()
    kern.sim.post("/_sim/fixtures", json={"cloudsync_tasks": taken}).raise_for_status()
    vernieuw(kern)


def inhoud(kern: Kern) -> dict[str, Any]:
    rijen: dict[str, Any] = kern.sim.get("/_sim/inhoud").raise_for_status().json()
    return rijen


def schrijf_inhoud(kern: Kern, dataset: str, **waarden: Any) -> None:
    kern.sim.post("/_sim/inhoud", json={"dataset": dataset, "inhoud": waarden}).raise_for_status()


def plaats_proefmedia(kern: Kern) -> None:
    kern.sim.post("/_sim/fixtures", json={"datasets": [MEDIA], "apps": [appfixture("emby")]}).raise_for_status()
    schrijf_inhoud(kern, MEDIA, kijkpositie=41, mediaguard_taak=7)
    lees_proefmedia(kern, 41, 7)


def lees_proefmedia(kern: Kern, kijkpositie: int, taak: int) -> None:
    assert inhoud(kern)["datasets"][MEDIA] == {"kijkpositie": kijkpositie, "mediaguard_taak": taak}
    apps = kern.sim.get("/_sim/state").raise_for_status().json()["apps"]
    assert apps["emby"]["state"] == "RUNNING"


def gate(kern: Kern, naam: str) -> dict[str, Any]:
    rij: dict[str, Any] = kern.hub.get("/api/v1/gates/" + naam).raise_for_status().json()
    return rij


def bereid_opslag_voor(kern: Kern) -> httpx.Client:
    richt_schrijfpad_in(kern, opslag=True)
    kern.sim.post("/_sim/fixtures", json={"datasets": [BRON, KLOON.rsplit("/", 1)[0]]}).raise_for_status()
    schijf = kern.client("onedrive", 30223)
    taak = schijf.post("/_sim/backup", json={"bestanden": BESTANDEN, "pad": "/mnt/" + BRON}).raise_for_status().json()
    assert taak["job"]["state"] == "SUCCESS"
    besluit(kern, "D04", {"methode": "truenas_cloud_sync", "taken": [str(taak["id"])], "versleuteld": False,
                          "scope": [BRON], "retentie": "één testexport", "herstel_credentials_locatie": "testvolume"})
    kopieer_taak(kern, schijf)
    assert kern.hub.get("/api/v1/backups/extern").raise_for_status().json()["status"] == "ok"
    foto = schijf.get("/herstel/proef.pgm").raise_for_status().json()["inhoud"]
    assert foto.split() == ["P2", "1", "1", "255", "42"]
    sql = schijf.get("/herstel/export.sql").raise_for_status().json()["inhoud"]
    assert sql == EXPORT
    with closing(sqlite3.connect(":memory:")) as db:
        db.executescript(sql)
        assert db.execute("SELECT positie FROM voortgang").fetchone() == (42,)
    nu = datetime.now(UTC)
    for soort in ("foto", "database"):
        kern.wijzig("POST", "/api/v1/backups/restore-tests", json={
            "dataset": BRON, "soort": soort, "resultaat": "geslaagd", "omgeving": "eigen simulator",
            "omschrijving": "Teruggelezen uit de synthetische export", "uitgevoerd_op": nu.isoformat()})
    dag = nu.date().isoformat()
    besluit(kern, "D05", {"foto_hersteld": True, "db_export_hersteld": True, "omgeving": "eigen simulator",
                          "uitgevoerd_op": dag, "referentie": "proef.pgm en export.sql"})
    kern.wijzig("POST", "/api/v1/decisions/D05/bewijs", json={"soort": "hersteltest", "resultaat": "geslaagd",
                "omschrijving": "Pixel en databasepositie teruggelezen", "uitgevoerd_op": dag})
    kern.wijzig("POST", "/api/v1/decisions/D06/meting")
    besluit(kern, "D16", {"per_app": [{"app_id": "proef-config", "rpo_uren": 24, "rto_uren": 2}]})
    kern.wijzig("POST", "/api/v1/backups/self")
    wacht("open gate voor opslag", lambda: gate(kern, "r2_r3_acties")["toegestaan"] is True)
    return schijf


def aanvraag(kern: Kern, actie: str, doel: dict[str, str], params: dict[str, Any]) -> httpx.Response:
    return kern.hub.post("/api/v1/actions", headers={"Idempotency-Key": str(uuid.uuid4())}, json={
        "action_id": actie, "target": {"host_id": "nas-primary", **doel}, "parameters": params,
        "reason": "Opslagproef op synthetische gegevens"})


def snapshot(kern: Kern) -> dict[str, Any]:
    rij: dict[str, Any] = aanvraag(kern, "zfs.snapshot.create", {"dataset": BRON},
                                   {"naam": "voor-proef"}).raise_for_status().json()
    return rij


def voer_uit(kern: Kern, rij: dict[str, Any]) -> httpx.Response:
    return kern.hub.post(f"/api/v1/actions/{rij['request_id']}/execute", headers={"Idempotency-Key": str(uuid.uuid4())})


def beheerheaders(kern: Kern, dienst: str) -> dict[str, str]:
    token = kern.wijzig("POST", "/api/v1/auth/service-jwt", json={"audience": dienst}).json()["token"]
    return {"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4())}
