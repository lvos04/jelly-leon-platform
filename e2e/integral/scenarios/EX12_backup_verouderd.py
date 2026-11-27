"""De taakstatus van het offsite-doel gaat via cloudsync.query naar het Hub-incident en de uitvoeringsgate."""

from typing import Any

from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.hubveiligheid import meet, schrijfacties
from e2e.integral.scenarios.opslag import BESTANDEN, EXPORT, bereid_opslag_voor, gate, kopieer_taak, snapshot, voer_uit

SCENARIO = {
    "groepen": ("opslag", "backupvoorwaarden"), "eigenaar": "jelly-leon-platform", "diensten": ("onedrive",),
    "verwachting": "Mislukte upload: de export blijft leesbaar, een kritiek incident opent en R2/R3 sluit.",
}


def test_mislukte_upload_en_herstel(kern: Kern) -> None:
    schijf = bereid_opslag_voor(kern)
    rij = snapshot(kern)
    assert rij["status"] == "klaar" and rij["risk_class"] == "R1" and rij["blokkades"] == []

    def incidenten() -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = kern.hub.get("/api/v1/incidents", params={
            "soort": "backup.extern", "status": "alle"}).raise_for_status().json()["items"]
        return items

    assert incidenten() == []
    kern.simulatorfout(schijf, "doel_vol")
    nieuw = {**BESTANDEN, "export.sql": "tweede proefexport"}
    mis = schijf.post("/_sim/backup", json={"bestanden": nieuw}).raise_for_status().json()
    assert mis["job"]["state"] == "FAILED"
    assert schijf.get("/herstel/export.sql").raise_for_status().json()["inhoud"] == EXPORT
    kopieer_taak(kern, schijf)
    extern = kern.hub.get("/api/v1/backups/extern").raise_for_status().json()
    assert extern["status"] == "mislukt" and extern["taken"][0]["laatste_job_staat"] == "FAILED"
    wacht("kritiek back-upincident", lambda: any(i["status"] == "open" for i in incidenten()))
    [inc] = incidenten()
    assert inc["ernst"] == "kritiek" and inc["bron"] == "hub:backup"
    assert gate(kern, "r2_r3_acties")["status"] == "geblokkeerd"
    assert {r["id"] for r in gate(kern, "backup_gate")["redenen"]} == {"extern_backup"}
    weigering = voer_uit(kern, rij)
    assert weigering.status_code == 409 and weigering.json()["code"] == "actie_geblokkeerd"
    assert "backup_gate_rood" in {b["code"] for b in weigering.json()["details"]["blokkades"]}
    goed = schijf.post("/_sim/backup", json={"bestanden": nieuw}).raise_for_status().json()
    assert goed["job"]["state"] == "SUCCESS"
    kopieer_taak(kern, schijf)
    wacht("hetzelfde incident opgelost", lambda: [(i["id"], i["status"]) for i in incidenten()]
          == [(inc["id"], "opgelost")])
    assert schijf.get("/herstel/export.sql").raise_for_status().json()["inhoud"] == nieuw["export.sql"]
    assert gate(kern, "r2_r3_acties")["toegestaan"] and schrijfacties(kern) == []
    schijf.post("/_sim/verouder", json={"uren": 30}).raise_for_status()
    kopieer_taak(kern, schijf)
    assert kern.hub.get("/api/v1/backups/extern").raise_for_status().json()["status"] == "verouderd"
    assert gate(kern, "r2_r3_acties")["toegestaan"] is False
    meet(kern, kritiek_incident=1, geweigerd_http=weigering.status_code, vorige_export_behouden=True,
         na_herstel_gate_open=True, verouderd_na_uren=30, schrijfacties=0)
