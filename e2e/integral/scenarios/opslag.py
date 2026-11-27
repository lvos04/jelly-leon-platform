"""Testgegevens, back-upvoorwaarden en voorstellen voor de gekoppelde Hub en executor."""

from __future__ import annotations

import json
import secrets
import sqlite3
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from e2e.integral.bewijs import schrijf
from e2e.integral.media_inhoud import BEWAARD, FIXTURES, KIJKERS, lees_kijkstand
from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.hubveiligheid import PROEVEN, appfixture, richt_schrijfpad_in, vernieuw

BRON = "simpool/apps/proef-config"
KLOON = "simpool/asm-sandbox/proef-config"
SNAP = BRON + "@voor-proef"
MEDIA = "simpool/apps/proef-media"
PADEN = ("/media/proefreeks.mkv", BEWAARD)
INSTANTIE = "sonarr-series"
TUSSENSTAND = 0.375
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


class Proefmedia:
    def __init__(self, kern: Kern):
        self.kern = kern
        self.emby = kern.client("emby-opslag", 8096)
        self.mg = kern.client("mg-inhoud", 8100)
        self.kijkstand = {
            pad: {u: {"Played": i == j, "PlayCount": j + 1 if i == j else 0, "LastPlayedDate": "2026-01-02T10:00:00Z",
                      "PlaybackPositionTicks": 0 if i == j else (j + 1) * 1_200_000, "IsFavorite": j == 1,
                      "Rating": 6.5 + i + j, "AudioStreamIndex": j + 1, "SubtitleStreamIndex": -1 if i == j else 3}
                  for j, u in enumerate(KIJKERS)} for i, pad in enumerate(PADEN)}
        self.metingen: dict[str, Any] = {}
        self.taaklog: list[dict[str, Any]] = []
        self.datasetstand = {"kijkpositie": 41, "mediaguard_taak": 7}

    def begin(self) -> None:
        start = self.emby.post("/_sim/bibliotheek", json=self.kijkstand).raise_for_status().json()
        self.emby.headers["Authorization"] = f'MediaBrowser Token="{start["api_key"]}"'
        wacht("MediaGuard", lambda: self.mg.get("/api/v1/health").is_success)
        beheerder = {"username": "herstelproef", "password": secrets.token_urlsafe(32)}
        auth = self.mg.post("/api/v1/auth/setup", json=beheerder)
        self.mg.headers["X-CSRF-Token"] = auth.raise_for_status().json()["csrf_token"]
        arr = self.kern.client("arr-inhoud", 30216)
        sleutels = arr.get("/_sim/sleutels").raise_for_status().json()
        arr.post("/api/v1/request", headers={"X-Api-Key": sleutels["seerr"]},
                 json={"mediaType": "tv", "mediaId": 101, "seasons": [1]}).raise_for_status()
        self.mg.patch(f"/api/v1/instances/{INSTANTIE}", json={"url": f"{str(arr.base_url).rstrip('/')}/{INSTANTIE}",
                      "api_key": sleutels[INSTANTIE]}).raise_for_status()
        taak = self.mg.post("/api/v1/library/sync", params={"instance_id": INSTANTIE}).raise_for_status().json()
        self.job = int(taak["queued"][INSTANTIE])
        self.kern.net.project.compose("exec", "-T", "-d", "mg-inhoud", "python", "-c",
                                      (FIXTURES / "hersteltaak.py").read_text(), str(self.job))
        wacht("bestaande taak met tussenstand", lambda: self.taak()["progress"] == TUSSENSTAND)
        self.meet("voor")

    def taak(self) -> dict[str, Any]:
        detail: dict[str, Any] = self.mg.get(f"/api/v1/jobs/{self.job}").raise_for_status().json()
        return detail

    def speel_verder(self) -> None:
        uid = KIJKERS[1]
        stand = {**self.kijkstand[PADEN[0]][uid], "PlaybackPositionTicks": 8_700_000}
        self.emby.post(f"/Users/{uid}/Items/1/UserData", json=stand).raise_for_status()
        self.kijkstand[PADEN[0]][uid] = stand
        self.datasetstand = {"kijkpositie": 87, "mediaguard_taak": 12}
        schrijf_inhoud(self.kern, MEDIA, **self.datasetstand)

    def meet(self, moment: str, *, afgerond: bool = False) -> None:
        assert inhoud(self.kern)["datasets"][MEDIA] == self.datasetstand, "Mediadataset gewijzigd"
        apps = self.kern.sim.get("/_sim/state").raise_for_status().json()["apps"]
        for naam in ("emby", "mediaguard"):
            assert apps.get(naam, {}).get("state") == "RUNNING", f"{naam} ontbreekt of staat stil"
        info = self.emby.get("/System/Info").raise_for_status().json()
        assert info["ProductName"] == "Emby Server" and info["Id"] == "kijkproef"
        kijkers = self.emby.get("/Users").raise_for_status().json()
        rechten = {u["Id"]: u["Policy"] for u in kijkers}
        assert len(kijkers) == 2 and rechten == {
            u: {"IsAdministrator": False, "EnableMediaPlayback": True, "EnableContentDownloading": n == 0}
            for n, u in enumerate(KIJKERS)}, "Gebruiker of rechten gewijzigd"
        assert lees_kijkstand(self.emby, kijkers) == self.kijkstand, "Kijkgegevens gewijzigd"
        detail = self.taak()
        assert (detail["id"], detail["job_type"], detail["instance_id"]) == (self.job, "library.sync", INSTANTIE)
        assert not detail["logs_truncated"] and detail["logs"][:len(self.taaklog)] == self.taaklog, "Taaklog gewijzigd"
        assert any(r["message"] == "Bibliotheeksynchronisatie wacht op herstelproef" for r in detail["logs"])
        self.taaklog = detail["logs"]
        if afgerond:
            assert detail["status"] == "succeeded" and detail["progress"] == 1, "Bestaande taak bleef stilstaan"
            resultaat = detail["result"]
            assert resultaat["status"] == "succeeded" and resultaat["series"] == 1 and resultaat["episodes"] == 2
            assert resultaat["failed_items"] == []
        else:
            assert (detail["status"], detail["progress"]) == ("running", TUSSENSTAND), "Bestaande voortgang gewijzigd"
        self.metingen[moment] = {
            "gebruikers": len(kijkers), "kijkstanden": sum(len(r) for r in self.kijkstand.values()),
            "gebruikersrechten_behouden": True, "taak": self.job, "voortgang": detail["progress"],
            "taakstatus": detail["status"], "logregels": len(self.taaklog),
        }
        schrijf(self.kern.net.dossier / "opslagmedia.json", self.metingen)

    def vervolg(self) -> None:
        self.meet("na_herstel")
        self.kern.net.project.compose("exec", "-T", "mg-inhoud", "touch", f"/work/vervolg-{self.job}")
        wacht("voortzetting van dezelfde bibliotheektaak", lambda: self.taak()["status"] != "running", seconden=30)
        self.meet("na_voortzetting", afgerond=True)


def plaats_proefmedia(kern: Kern) -> Proefmedia:
    media = Proefmedia(kern)
    apps = kern.sim.get("/_sim/state").raise_for_status().json()["apps"]
    ontbrekend = [appfixture(n) for n in ("emby", "mediaguard") if n not in apps]
    kern.sim.post("/_sim/fixtures", json={"datasets": [MEDIA], "apps": ontbrekend}).raise_for_status()
    schrijf_inhoud(kern, MEDIA, **media.datasetstand)
    media.begin()
    return media


def gate(kern: Kern, naam: str) -> dict[str, Any]:
    rij: dict[str, Any] = kern.hub.get("/api/v1/gates/" + naam).raise_for_status().json()
    return rij


def lever_appbackups(kern: Kern) -> None:
    project = kern.net.project
    mg = appfixture("mediaguard")
    mg["values"]["ports"] = [{"port_number": 30080, "container_port": 30080}]
    mg["portals"] = {"Koppeling": "https://truenas:30080/"}
    kern.sim.post("/_sim/fixtures", json={"apps": [mg]}).raise_for_status()
    vernieuw(kern)
    kern.wijzig("POST", "/api/v1/apps/discover")
    items = kern.hub.get("/api/v1/apps", params={"q": "mediaguard"}).raise_for_status().json()["items"]
    [app] = [a for a in items if a["runtime_id"] == "mediaguard"]
    sleutel = "mgk_" + secrets.token_urlsafe(32)
    kern.wijzig("POST", "/api/v1/adapterkoppelingen", headers={"X-Adapter-Credential": sleutel}, json={
        "app_id": "mediaguard", "catalog_app_id": app["id"], "basis_url": mg["portals"]["Koppeling"].rstrip("/"),
        "doel_bevestigd": True})
    # Gebruik de archief- en connectorfixtures van dezelfde Hub als de productiewerker.
    fixture = Path(project.env["INTEGRAAL_BRONMAP"]) / "ai-server-manager/tests/backupfixtures.py"
    inventaris = (PROEVEN / "archiefinventaris.json").read_bytes()
    opbouw = f"""
from datetime import UTC
from pathlib import Path
import json
from hub.config import get_settings
root = get_settings().data_dir
(root / 'proef-ca.pem').write_text({kern.net.ca["truenas"]!r})
opname = archiefopname(datetime.now(UTC))
exportbewijs(root, opname, schrijf_archiefpaar(root, opname), gelezen_sporen={{'proef': ['film']}})
(root / 'overnamebron' / 'inventaris.json').write_bytes({inventaris!r})
print(json.dumps(herstelpagina(datetime.now(UTC))))
"""
    pagina = project.compose("exec", "-T", "--user", "568:568", "hub", "python", "-",
                             invoer=fixture.read_text() + opbouw)
    project.compose("exec", "-T", "truenas", "python", "-c",
                    "import sys; from pathlib import Path; Path('/certificaat/proefrapport.json').write_bytes("
                    "sys.stdin.buffer.read())", invoer=json.dumps([sleutel, json.loads(pagina)]))
    project.compose("exec", "-T", "-d", "truenas", "python", "-c", (PROEVEN / "sim_backuprapport.py").read_text())


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
    besluit(kern, "D16", {"per_app": [{"app_id": app, "rpo_uren": 24, "rto_uren": 2}
                                      for app in ("proef-config", "emby", "mediaguard")]})
    kern.wijzig("POST", "/api/v1/backups/self")
    lever_appbackups(kern)
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
