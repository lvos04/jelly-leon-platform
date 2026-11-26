"""Bediening van de echte huisapp en de geselecteerde simulators in één wegwerpproject."""

from __future__ import annotations

import json
import secrets
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx

from e2e.integral.bewijs import schrijf
from e2e.integral.project import MAP, Proeffout, gecontroleerd, opdracht
from e2e.integral.scenarios.conftest import Kern, wacht


def controleer_testnet(kern: Kern) -> None:
    project = kern.net.project
    containers = project.compose("ps", "-q").split()
    if not containers:
        raise Proeffout("Het eigen proefproject draait niet")
    uitvoer = gecontroleerd(opdracht([
        "docker", "inspect", "--format", "{{json .NetworkSettings.Networks}}", *containers,
    ]), "Netwerk van de proefcontainers onbekend")
    netten = [json.loads(regel) for regel in uitvoer.splitlines()]
    if len(netten) != len(containers) or any(len(n) != 1 for n in netten):
        raise Proeffout("De huisproef vereist één intern projectnetwerk")
    netwerk_ids = {next(iter(n.values()))["NetworkID"] for n in netten}
    if len(netwerk_ids) != 1:
        raise Proeffout("De diensten van de huisproef moeten hetzelfde interne netwerk gebruiken")
    netwerk = netwerk_ids.pop()
    info = json.loads(gecontroleerd(opdracht(["docker", "network", "inspect", netwerk]), "Netwerkinspectie mislukt"))[0]
    if info["Internal"] is not True or info["Labels"].get("com.docker.compose.project") != project.naam:
        raise Proeffout("De huisproef vereist een intern netwerk van het eigen project")


class Huis:
    def __init__(self, kern: Kern):
        controleer_testnet(kern)
        self.kern = kern
        self.project = kern.net.project
        self.api = kern.client("my-house", 8080)
        self.metingen: dict[str, Any] = {}
        wacht("my_house klaar voor de eerste beheerder", lambda: self.get("/auth/status").get("setup_required") is True)
        login = self.schrijf("POST", "/auth/setup", {
            "username": "huisproef", "password": secrets.token_urlsafe(30),
        })
        self.api.headers["X-CSRF-Token"] = login["csrf_token"]
        # De web-app opent standaard de eerste woning.
        woningen = self.get("/homes")
        self.woning = (woningen[0] if woningen else self.schrijf("POST", "/homes", {"name": "Proefwoning"}))["id"]
        self.zones = [z["id"] for z in self.get(f"/homes/{self.woning}/zones")]
        self.meet("intern_projectnetwerk", True)

    def meet(self, naam: str, waarde: Any) -> None:
        self.metingen[naam] = waarde
        schrijf(self.kern.net.dossier / "huis.json", self.metingen)

    def get(self, pad: str) -> Any:
        return self.api.get("/api/v1" + pad).raise_for_status().json()

    def schrijf(self, methode: str, pad: str, body: dict[str, Any]) -> Any:
        return self.api.request(methode, "/api/v1" + pad, json=body).raise_for_status().json()

    def start_sim(self, module: str, poort: int, *args: str) -> httpx.Client:
        self.project.compose("exec", "-d", "-T", "-u", "568:568", "-e", "PYTHONPATH=/huis-sim",
                             "my-house", "python", "-m", module, "--host", "intern", "--port", str(poort), *args)
        sim = self.kern.client("my-house", poort)
        wacht(f"{module} bereikbaar", lambda: sim.get("/_sim/toestand").is_success, 30)
        return sim

    def gree(self, nummer: int = 0, *, aan: bool = True) -> httpx.Client:
        # Alleen de door S-P6-11 gepinde fixture, ongeacht de werkboom van de buurrepo.
        pin = (MAP.parent / "simulators/my-house.commit").read_text().strip()
        bron = Path(self.project.env["INTEGRAAL_BRONMAP"]) / "my_house"
        fake = gecontroleerd(opdracht(["git", "-C", str(bron), "show", f"{pin}:integrations/tests/fake_gree.py"]),
                             "Gepinde Gree-fixture ontbreekt")
        self.project.compose("exec", "-T", "-u", "568:568", "my-house", "sh", "-ec",
                             "umask 077; mkdir -p /appdata/app/gree-proef; cat > /appdata/app/gree-proef/fake_gree.py",
                             invoer=fake)
        return self.start_sim("gree", 30220 + nummer, "--fake-bron", "/appdata/app/gree-proef",
                              "--udp-poort", str(7000 + nummer), "--mac", f"f4911e0000{10 + nummer:02x}",
                              "--pow", str(int(aan)))

    def koppel(self, provider: str, host: str, **opties: Any) -> httpx.Response:
        return self.api.post(f"/api/v1/integrations/{provider}/pair", json={
            "home_id": self.woning, "host": host, "options": opties, "name": "Meetproef",
        })

    def bevestig(self, apparaat: str) -> None:
        self.schrijf("POST", f"/devices/{apparaat}/test", {})
        self.schrijf("POST", f"/devices/{apparaat}/tested", {"note": "Alleen de lokale simulator uitgelezen."})

    def unit(self, nummer: int = 0) -> str:
        apparaat = self.koppel("gree", "my-house", port=7000 + nummer, timeout_s=0.25).raise_for_status().json()["id"]
        self.bevestig(apparaat)
        zone = self.zones[nummer]
        self.schrijf("PATCH", f"/zones/{zone}", {"unit_device_id": apparaat, "outdoor_group": "testgroep"})
        wacht("teruglezing van de unit", lambda: self.zone(nummer)["unit_status"] == "online"
              and self.zone(nummer)["target_c"] is not None)
        return str(apparaat)

    def zone(self, nummer: int = 0) -> Any:
        return self.get(f"/zones/{self.zones[nummer]}")

    def schema(self, doel: int) -> None:
        self.schrijf("PUT", f"/zones/{self.zones[0]}/schedule", {
            "blocks": [{"days": list(range(7)), "start": "00:00", "mode": "heat", "target_c": doel}],
        })

    def opdracht(self, nummer: int, **waarden: Any) -> httpx.Response:
        return self.api.post(f"/api/v1/zones/{self.zones[nummer]}/command", json=waarden)

    def sql(self, query: str, rij: UUID | None = None) -> str:
        variabelen = ["-v", f"rij={rij}"] if rij else []
        return self.project.compose("exec", "-T", "-u", "568:568", "my-house", "psql", "-XAtq",
                                    "-h", "/appdata/postgres", "-U", "apps", "-d", "shc",
                                    "-v", "ON_ERROR_STOP=1", *variabelen, invoer=query).strip()

    def wachtrij(self) -> int:
        return int(self.sql("SELECT count(*) FROM commands;"))

    def proefscript(self, dienst: str, naam: str, gegevens: dict[str, Any]) -> Any:
        script = (MAP.parent / "fixtures/huis" / naam).read_text()
        resultaat = self.project.compose("exec", "-T", "-u", "568:568", dienst, "python", "-c", script,
                                          invoer=json.dumps(gegevens), tijd=150)
        return json.loads(resultaat)


def toestand(sim: httpx.Client) -> Any:
    return sim.get("/_sim/toestand").raise_for_status().json()


def datagrammen(sim: httpx.Client) -> Any:
    return sim.get("/_sim/udp").raise_for_status().json()
