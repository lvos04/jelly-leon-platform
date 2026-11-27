"""EX06 · MediaGuard, Hub en MSO. Tijdens een lopende job verhindert de noodstop nieuwe uitvoeringen."""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import httpx

from e2e.integral.bewijs import schrijf
from e2e.integral.downloadketen import INSTANTIE, Downloadketen
from e2e.integral.media import behoud, inloggen, sql, verbind_mso
from e2e.integral.scenarios.conftest import Kern, wacht

SCENARIO = {
    "eigenaar": "MediaGuard, Hub en MSO", "groepen": ("media", "uitval"),
    "diensten": ("mediaguard", "mso", "arr-sim", "sabnzbd-sim"),
    "verwachting": "Bij noodstop tijdens een lopende bibliotheekjob worden zoeken, import, opruimen en AV1 "
                   "niet uitgevoerd. De lopende toestand en kijkhistorie blijven leesbaar.",
}


def lopende_sync(mg: httpx.Client, keten: Downloadketen) -> int:
    # Vertraag alleen een leesaanroep van de synchronisatie van een andere instantie.
    pad = "/sonarr-anime"
    sleutel = keten.arr.get("/_sim/sleutels").raise_for_status().json()["sonarr-anime"]
    mg.patch("/api/v1/instances/sonarr-anime", json={"url": str(keten.arr.base_url).rstrip("/") + pad,
                                                     "api_key": sleutel}).raise_for_status()
    keten.arr.post("/_sim/fouten", json={"naam": "traag", "aantal": 1, "waarde": 15,
                                        "pad": "^" + pad + "/api/v3/series$"}).raise_for_status()
    taak = mg.post("/api/v1/library/sync", params={"instance_id": "sonarr-anime"}).raise_for_status()
    job = int(taak.json()["queued"]["sonarr-anime"])
    wacht("lopende bibliotheekjob", lambda: lees_job(mg, job)["status"] == "running")
    return job


def lees_job(mg: httpx.Client, job: int) -> dict[str, Any]:
    data: dict[str, Any] = mg.get(f"/api/v1/jobs/{job}").raise_for_status().json()
    return data


def uitvoeringen(kern: Kern, wanted: int) -> dict[str, set[int]]:
    rijen = sql(kern, ("SELECT id FROM backfill_attempts WHERE wanted_id=?", (wanted,)),
                ("SELECT id FROM audit_events WHERE action='download.manual_import'", ()),
                ("SELECT id FROM download_janitor_runs", ()), ("SELECT id FROM encoding_jobs", ()))
    return {naam: {r[0] for r in tabel} for naam, tabel in
            zip(("zoeken", "import", "opruimen", "av1"), rijen, strict=True)}


def test_noodstop(kern: Kern) -> None:
    mg, mso = inloggen(kern, "mediaguard"), inloggen(kern, "mso")
    keten = Downloadketen(kern, mg)
    download = keten.importkandidaat()
    project = kern.net.project
    project.compose("exec", "-T", "--user", "0", "mediaguard", "install", "-d", "-o", "568", "-g", "568",
                    "/data/uitvalproef")
    fixture = Path(__file__).resolve().parents[1] / "mediafixtures" / "bibliotheek.py"
    project.compose("exec", "-T", "--user", "568:568", "mediaguard", "/opt/venv/bin/python", "-", "noodstopclip",
                    invoer=fixture.read_text())
    verbind_mso(mso, mg)
    status = "/api/v1/mediaguard/status"
    wacht("actieve MSO-koppeling", lambda: mso.get(status, params={"refresh": True}).json()["mode"] == "connected")
    mso.post("/api/v1/mediaguard/sync").raise_for_status()
    mso_item = f"/api/v1/mediaguard/wanted/{keten.wanted}"
    wacht("wanted-item in MSO", lambda: mso.get(mso_item).status_code == 200)
    job = lopende_sync(mg, keten)
    noodstop = "/api/v1/system/emergency-stop"
    zoekpad = f"/api/v1/wanted/{keten.wanted}/search"
    with behoud(kern, mg, job) as meet:
        voor = uitvoeringen(kern, keten.wanted)
        vooraf_sab = keten.sab_ids()
        lopend = lees_job(mg, job)["status"]
        assert lopend == "running"
        stoppen = os.environ.get("INTEGRAAL_ZONDER_STORING") != "1"
        if stoppen:
            mg.post(noodstop, json={"active": True, "reason": "Integrale uitvalproef"}).raise_for_status()
        try:
            actief = mg.get("/api/v1/status").raise_for_status().json()["emergency_stop"]["active"]
            hub_codes = {}
            hub_fouten = {}
            for actie in ("wanted.search", "download.import", "download.cleanup", "av1.encode"):
                resp = kern.hub.post("/api/v1/actions", headers={"Idempotency-Key": str(uuid.uuid4())},
                                     json={"action_id": "mediaguard." + actie, "target": {"app_id": "mediaguard"},
                                           "parameters": {}, "reason": "Noodstopproef"})
                hub_codes[actie] = resp.status_code
                hub_fouten[actie] = resp.json().get("code")
                assert hub_fouten[actie] == "capability_unavailable"
            eerder = mso.post(mso_item + "/search")
            vers = mso.get(status, params={"refresh": True}).raise_for_status().json()
            herzoek = mso.post(mso_item + "/search")
            zoeken = mg.post(zoekpad)
            invoer = mg.post(f"/api/v1/activity/downloads/{INSTANTIE}/import",
                             json={"download_id": download, "confirm": True})
            av1 = mg.post("/api/v1/encoding/jobs", json={"path": "/data/uitvalproef/proefreeks.mkv",
                                                            "trial_only": True, "force_cpu": True})
            opruimjob = mg.post("/api/v1/downloads/cleanup/run").raise_for_status().json()["job_id"]
            # Dezelfde werker krijgt gelegenheid te draaien. Een wachtende opruimjob is nog geen opruimronde.
            wacht("afgeronde bibliotheekjob", lambda: lees_job(mg, job)["status"] == "succeeded")
            if not actief:
                wacht("uitgevoerde opruimjob", lambda: lees_job(mg, opruimjob)["started_at"] is not None)
            opruimen = lees_job(mg, opruimjob)
            nieuwe = {n: len(ids - voor[n]) for n, ids in uitvoeringen(kern, keten.wanted).items()}
            historie = lees_job(mg, job)
            meet("tijdens", False)
            feiten = {"job_voor": lopend, "job_tijdens": historie["status"],
                      "historie_leesbaar": historie["id"] == job and bool(historie["logs"]), "noodstop": actief,
                      "hub_http": hub_codes, "hub_foutcodes": hub_fouten, "mso_http_voor_verversen": eerder.status_code,
                      "mso_route_onbevestigd_voor_verversen": eerder.status_code == 409
                      and "niet bevestigen" in str(eerder.json()).lower(), "mso_ziet_noodstop": vers["emergency_stop"],
                      "mso_http": herzoek.status_code,
                      "mso_route_onbevestigd": herzoek.status_code == 409
                      and "niet bevestigen" in str(herzoek.json()).lower(),
                      "mso_weigert_noodstop": herzoek.status_code == 409 and "noodstop" in str(herzoek.json()).lower(),
                      "zoeken_http": zoeken.status_code, "zoekopdrachten_gemeld": zoeken.json().get("issued"),
                      "zoeken_weigert_noodstop": zoeken.status_code == 429
                      and "noodstop" in str(zoeken.json().get("detail")).lower(),
                      "import_http": invoer.status_code, "import_weigert_noodstop": invoer.status_code == 409
                      and "noodstop" in str(invoer.json()).lower(), "av1_http": av1.status_code,
                      "av1_weigert_noodstop": av1.status_code == 409
                      and av1.json()["detail"].get("code") == "EMERGENCY_STOP",
                      "opruimjob": opruimen["status"], "opruimjob_gestart": opruimen["started_at"] is not None,
                      "nieuwe_uitvoeringen": nieuwe, "nieuwe_sab_taken": len(keten.sab_ids() - vooraf_sab)}
            schrijf(kern.net.dossier / "noodstop.json", feiten)
        finally:
            if stoppen:
                mg.post(noodstop, json={"active": False, "reason": "Einde proef"}).raise_for_status()
        wacht("opruimjob na opheffen", lambda: lees_job(mg, opruimjob)["status"] == "succeeded")
        feiten["opruimjob_na"] = lees_job(mg, opruimjob)["status"]
        # Dezelfde aflevering moet zonder noodstop daadwerkelijk in arr en SAB verschijnen.
        voor_zoekproef = keten.sab_ids() if stoppen else vooraf_sab
        vrij = mg.post(zoekpad) if stoppen else zoeken
        feiten["zoeken_zonder_noodstop_http"] = vrij.status_code
        feiten["zoekopdrachten_zonder_noodstop"] = vrij.json().get("issued")
        schrijf(kern.net.dossier / "noodstop.json", feiten)
        assert vrij.is_success and feiten["zoekopdrachten_zonder_noodstop"] == 1
        wacht("download van de proefaflevering zonder noodstop",
              lambda: bool((keten.aflevering_ids() & keten.sab_ids()) - voor_zoekproef))
        gedeeld = (keten.aflevering_ids() & keten.sab_ids()) - voor_zoekproef
        feiten["gedeelde_nieuwe_downloads_zonder_noodstop"] = len(gedeeld)
        schrijf(kern.net.dossier / "noodstop.json", feiten)
        assert len(gedeeld) == 1
        assert set(hub_codes.values()) == {501}
        assert not any(nieuwe.values()) and feiten["nieuwe_sab_taken"] == 0, "Nieuwe uitvoering tijdens noodstop"
        assert eerder.status_code == 409
        assert actief and feiten["historie_leesbaar"] and feiten["mso_ziet_noodstop"] and feiten["mso_weigert_noodstop"]
        assert feiten["zoeken_weigert_noodstop"]
        assert feiten["import_weigert_noodstop"] and feiten["av1_weigert_noodstop"]
        assert opruimen["status"] == "queued" and opruimen["started_at"] is None
