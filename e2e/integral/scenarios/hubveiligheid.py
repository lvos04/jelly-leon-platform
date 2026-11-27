"""Metingen via de gekoppelde kern en de beheer-API van uitsluitend de TrueNAS-simulator."""

from __future__ import annotations

import hashlib
import json
import re
import ssl
import uuid
from pathlib import Path
from typing import Any

from e2e.integral.bewijs import schrijf
from e2e.integral.scenarios.conftest import Kern, wacht

PROEVEN = Path(__file__).resolve().parent
EXECUTOR = "ai-server-manager-executor"
APP_A = "proef-app-a"
APP_B = "proef-app-b"


def meet(kern: Kern, **tellingen: int | bool) -> None:
    schrijf(kern.net.dossier / "metingen.json", tellingen)


def oproepen(kern: Kern) -> list[dict[str, Any]]:
    rijen: list[dict[str, Any]] = kern.sim.get("/_sim/calls").raise_for_status().json()
    assert any(r["method"] == "system.info" and r["user"] == "hub-readonly" for r in rijen)
    return rijen


def schrijfacties(kern: Kern) -> list[str]:
    return [r["method"] for r in oproepen(kern) if r["write"]]


def vernieuw(kern: Kern) -> None:
    taak = kern.wijzig("POST", "/api/v1/inventory/refresh").json()["id"]
    pad = f"/api/v1/jobs/{taak}"
    wacht("inventarismeting", lambda: kern.hub.get(pad).raise_for_status().json()["status"] in {"done", "failed"})


def appfixture(naam: str) -> dict[str, Any]:
    return {"app_name": naam, "version": "1.4.8",
            "values": {"image": {"repository": f"example.invalid/proeven/{naam}", "tag": "1.0"}}}


def stel_voor(kern: Kern, naam: str) -> dict[str, Any]:
    resultaat: dict[str, Any] = kern.wijzig("POST", "/api/v1/actions", json={
        "action_id": "app.restart", "target": {"host_id": "nas-primary", "app_id": naam},
        "parameters": {}, "reason": "Herstart van een tijdelijke simulatorapp",
    }).json()
    assert resultaat["status"] == "wacht_op_goedkeuring" and resultaat["goedkeuring_nodig"]
    assert resultaat["risk_class"] == "R1" and resultaat["blokkades"] == []
    return resultaat


def akkoord(kern: Kern, voorstel: dict[str, Any]) -> dict[str, Any]:
    velden = {k: voorstel[k] for k in ("resource_etag", "preview_hash", "policy_hash")}
    rij: dict[str, Any] = kern.wijzig("POST", f"/api/v1/actions/{voorstel['request_id']}/approve", json=velden).json()
    assert rij["status"] == "goedgekeurd" and rij["goedkeuring"]["approval_id"]
    return rij


def uitvoeren(kern: Kern, voorstel: dict[str, Any], *, geweigerd: bool = False) -> dict[str, Any]:
    antwoord = kern.hub.post(f"/api/v1/actions/{voorstel['request_id']}/execute",
                             headers={"Idempotency-Key": str(uuid.uuid4())})
    assert antwoord.status_code == (409 if geweigerd else 200)
    resultaat: dict[str, Any] = antwoord.json()
    if not geweigerd:
        assert resultaat["status"] == "uitgevoerd" and resultaat["resultaat"]["methode"] == "app.redeploy"
    return resultaat


def richt_schrijfpad_in(kern: Kern, *, herlever: bool = False, opslag: bool = False) -> None:
    # Een NAS publiceert de executor op zijn eigen adres. Alleen de nagebootste app-poort is hier een tussenstap.
    project = kern.net.project
    bron = (PROEVEN / "sim_app_poort.py").read_text()
    modus = "opslag" if opslag else "dubbel" if herlever else "enkel"
    project.compose("exec", "-T", "-d", "truenas", "python", "-c", bron, modus)
    dienst = appfixture(EXECUTOR)
    dienst["values"]["image"]["repository"] = f"ghcr.io/lvos04/{EXECUTOR}"
    dienst["values"]["ports"] = [{"port_number": 30410, "container_port": 30410}]
    kern.sim.post("/_sim/fixtures", json={"apps": [dienst, appfixture(APP_A), appfixture(APP_B)]}).raise_for_status()
    vernieuw(kern)
    apps = kern.hub.get("/api/v1/inventory/apps").raise_for_status().json()
    assert apps["status"] == "ok" and {a["naam"] for a in apps["items"]} >= {EXECUTOR, APP_A, APP_B}
    sleutel = kern.sim.get("/_sim/keys").raise_for_status().json()["write"]
    pem = kern.net.ca["truenas"]
    afdruk = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()
    getoond = ":".join(afdruk[i:i + 2] for i in range(0, len(afdruk), 2)).upper()
    for melding in (getoond, "De schrijfsleutel is getest en opgeslagen."):
        pagina = kern.executor.get("/").raise_for_status()
        veld = re.search(r'name="csrf" value="([^"]+)"', pagina.text)
        assert veld is not None
        antwoord = kern.executor.post("/schrijfsleutel", headers={"Origin": str(kern.executor.base_url).rstrip("/")},
                                      data={"csrf": veld[1], "truenas_sleutel": sleutel})
        assert antwoord.status_code == 200
        # Foutuitvoer mag geen formulier of sleutel bevatten.
        if melding not in antwoord.text:
            raise AssertionError("De executor bevestigde de TLS-afdruk of schrijfsleutel niet")
    assert schrijfacties(kern) == []


def bezorgingen(kern: Kern) -> list[list[int]]:
    uitvoer = kern.net.project.compose("exec", "-T", "truenas", "cat", "/certificaat/bezorgingen.json")
    uit: list[list[int]] = json.loads(uitvoer)
    return uit
