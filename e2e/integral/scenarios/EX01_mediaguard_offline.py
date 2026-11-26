"""EX01 · MediaGuard, MSO en Hub. MSO blijft zelfstandig, de Hub meldt onbekend, jobs blijven uniek."""

from __future__ import annotations

import time
from typing import Any

import httpx

from e2e.integral.bewijs import schrijf
from e2e.integral.media import behoud, draait, inloggen, plan_werk, sql, uitval, verbind_mso
from e2e.integral.scenarios.conftest import Kern, wacht

SCENARIO = {
    "eigenaar": "MediaGuard, MSO en Hub", "groepen": ("media", "uitval"),
    "diensten": ("mediaguard", "mso"),
    "verwachting": "MSO werkt zelfstandig tijdens uitval, de Hub meldt een incident met onbekende gezondheid "
                   "en na herstel blijven de jobs uniek.",
}


def inventariseer(kern: Kern, toestand: str) -> str:
    # De NAS-simulator heeft geen Docker-socket. Zijn inventaris volgt de gemeten containerstaat.
    app = {"app_name": "mediaguard", "version": "1.4.8", "state": toestand,
           "values": {"image": {"repository": "jlp-integraal/mediaguard", "tag": "proef"}}}
    kern.sim.post("/_sim/fixtures", json={"apps": [app]}).raise_for_status()
    job = kern.wijzig("POST", "/api/v1/inventory/refresh").json()["id"]
    wacht("Hub-inventaris", lambda: kern.hub.get(f"/api/v1/jobs/{job}").raise_for_status().json()["status"] == "done")
    kern.wijzig("POST", "/api/v1/apps/discover")
    items = kern.hub.get("/api/v1/apps", params={"q": "mediaguard"}).raise_for_status().json()["items"]
    rij, = (app for app in items if app["runtime_id"] == "mediaguard")
    assert rij["runtime_staat"] == toestand
    return str(rij["id"])


def mso_status(mso: httpx.Client) -> tuple[int, str | None]:
    resp = mso.get("/api/v1/mediaguard/status", params={"refresh": "true"})
    if not resp.is_success:
        return resp.status_code, None
    data = resp.json()
    assert data["configured"] is True
    modus = data["mode"]
    assert modus in {"connected", "degraded", "standalone"}
    if modus == "standalone":
        assert data["reachable"] is False
    return resp.status_code, str(modus)


def actieve_incidenten(kern: Kern, catalogus_id: str) -> set[str]:
    ids: set[str] = set()
    for app in (catalogus_id, "mediaguard"):
        resp = kern.hub.get("/api/v1/incidents", params={"app": app}).raise_for_status().json()
        ids.update(i["id"] for i in resp["items"] if i["actief"])
    return ids


def test_mediaguard_offline(kern: Kern) -> None:
    mg, mso = inloggen(kern, "mediaguard"), inloggen(kern, "mso")
    verbind_mso(mso, mg)
    verbonden = (200, "connected")
    wacht("actieve MSO-koppeling", lambda: mso_status(mso) == verbonden)
    job = plan_werk(kern)
    feiten: dict[str, Any] = {"mso_voor": "connected", "mso_http_tijdens": {}}
    pad = kern.net.dossier / "uitval.json"
    with behoud(kern, mg, job) as meet:
        catalogus_id = inventariseer(kern, "RUNNING")
        eerder = actieve_incidenten(kern, catalogus_id)
        with uitval(kern, "mediaguard"):
            gestopt = not draait(kern, "mediaguard")
            feiten["mediaguard_gestopt"] = gestopt
            meet("tijdens", gestopt)
            assert draait(kern, "mso")
            assert inventariseer(kern, "STOPPED" if gestopt else "RUNNING") == catalogus_id
            mso.put("/api/v1/system/mode", json={"mode": "manage"}).raise_for_status()
            feiten["mso_eigen_wijziging_teruggelezen"] = (
                mso.get("/api/v1/system/mode").raise_for_status().json()["mode"] == "manage"
            )
            start = time.monotonic()
            while True:
                http, modus = mso_status(mso)
                codes = feiten["mso_http_tijdens"]
                codes[str(http)] = codes.get(str(http), 0) + 1
                rij = kern.hub.get(f"/api/v1/apps/{catalogus_id}").raise_for_status().json()
                gezondheid = rij["gezondheid"]["status"]
                assert gezondheid in {"healthy", "degraded", "down", "unknown"}
                feiten.update(mso_modus=modus, hub_gezondheid=gezondheid,
                              nieuwe_incidenten=len(actieve_incidenten(kern, catalogus_id) - eerder),
                              meetduur_sec=round(time.monotonic() - start, 1))
                schrijf(pad, feiten)
                if (modus == "standalone" and gezondheid == "unknown" and feiten["nieuwe_incidenten"]) or (
                    time.monotonic() - start >= 60
                ):
                    break
                time.sleep(2)
        wacht("MediaGuard herstart", lambda: mg.get("/api/v1/health").raise_for_status().json()["status"] == "ok")
        wacht("MSO opnieuw verbonden", lambda: mso_status(mso) == verbonden)
        feiten["mso_na"] = "connected"
        assert plan_werk(kern) == job
        werk = sql(kern, ("SELECT id FROM jobs WHERE job_type='library.sync' AND target_id='proefreeks'", ()))
        feiten["jobs_na_herhaling"] = len(werk[0])
        bewijsjob = mg.get(f"/api/v1/jobs/{job}").raise_for_status().json()
        feiten["job_met_historie_leesbaar"] = bewijsjob["id"] == job and bool(bewijsjob["logs"])
        schrijf(pad, feiten)
        assert werk == [[[job]]] and feiten["job_met_historie_leesbaar"]
        assert feiten["mso_eigen_wijziging_teruggelezen"]
        assert feiten["mso_modus"] == "standalone", "MSO moet tijdens uitval zelfstandig werken"
        assert gestopt, "MediaGuard moet gestopt zijn"
        assert feiten["hub_gezondheid"] == "unknown" and feiten["nieuwe_incidenten"] > 0
