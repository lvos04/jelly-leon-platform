"""Versie 1 van EX21 meet de uitgeschakelde AI terwijl de Hub een regel op nieuwe simulatordata toepast."""

from __future__ import annotations

import json
import select
import subprocess

from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.hubveiligheid import meet, oproepen, vernieuw

SCENARIO = {
    "blokkade": "NIET GEVALIDEERD (echte NAS); NIET GEVALIDEERD (account)",
    "toelichting": ("De proef draait met AI uit. Tegoeduitputting en de kostenmonitor volgen in versie 2. "
                    "De praktijkproef voor beleid en executor volgt in N-P6-03."),
    "groepen": ("hub-veiligheid",),
    "eigenaar": "jelly-leon-platform",
    "verwachting": "AI uit: nul netwerkpogingen van de runner terwijl de Hub op een gewijzigde pool reageert.",
}

# De normale runner onder zijn eigen UID, met een waarnemer vanaf de start tot na de poolregel.
# Een mislukte poging telt ook. Een uitbesteed hulpproces maakt de meting ongeldig.
RUNNERMETING = """
import json, os, signal, socket, sys, threading, time
from contextlib import redirect_stdout
from hub.config import get_settings

assert os.geteuid() == 569 and get_settings().ai_enabled is False
verkeer = []
helpers = []
def tellen(event, args):
    if event in {"socket.connect", "socket.sendto", "socket.sendmsg", "socket.getaddrinfo",
                 "socket.gethostbyname", "socket.gethostbyaddr"}:
        verkeer.append(event)
    if event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.fork", "os.forkpty"}:
        helpers.append(event)

sys.addaudithook(tellen)
with socket.socket() as luisteraar, socket.socket() as client:
    luisteraar.bind(("localhost", 0))
    luisteraar.listen(1)
    client.connect(luisteraar.getsockname())
controle = verkeer.count("socket.connect")
assert controle == 1
verkeer.clear()
uitvoer = sys.stdout
def wacht_op_proef():
    while not callable(signal.getsignal(signal.SIGTERM)):
        time.sleep(0.02)
    print("meetvenster open", file=uitvoer, flush=True)
    sys.stdin.readline()
    os.kill(os.getpid(), signal.SIGTERM)

threading.Thread(target=wacht_op_proef, daemon=True).start()
with redirect_stdout(sys.stderr):
    from hub.__main__ import main
    assert main(["ai-runner"]) == 0
print(json.dumps({"controle": controle, "netwerk": len(verkeer), "hulpprocessen": len(helpers)}))
"""


def test_ai_uit_regelwerk_blijft_draaien(kern: Kern) -> None:
    vernieuw(kern)
    pad = "/api/v1/inventory/pools"

    def poolredenen() -> list[dict[str, str]]:
        redenen = kern.hub.get("/api/v1/gates/data_mutatie").raise_for_status().json()["redenen"]
        return [r for r in redenen if r["soort"] == "meting" and r["id"] == "pools"]

    gezond = kern.hub.get(pad).raise_for_status().json()
    assert gezond["status"] == "ok" and gezond["items"] and all(p["gezond"] for p in gezond["items"])
    assert poolredenen() == []
    project = kern.net.project
    cmd = [*project.cmd, "exec", "-T", "--user", "569:569", "hub", "timeout", "180", "python", "-c", RUNNERMETING]
    with subprocess.Popen(cmd, env=project.env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,  # noqa: S603
                          stderr=subprocess.DEVNULL, text=True) as runner:
        assert runner.stdout is not None
        assert select.select([runner.stdout], [], [], 15)[0] and runner.stdout.readline().strip() == "meetvenster open"
        start = len(oproepen(kern))
        kern.sim.post("/_sim/faults/pool_degraded", json={}).raise_for_status()
        vernieuw(kern)
        nu = kern.hub.get(pad).raise_for_status().json()
        assert nu["status"] == "ok" and any(p["status"] == "DEGRADED" and p["gezond"] is False for p in nu["items"])
        regels = poolredenen()
        assert len(regels) == 1 and regels[0]["status"] == "geblokkeerd"
        metingen = [r for r in oproepen(kern)[start:] if r["method"] == "pool.query" and r["user"] == "hub-readonly"]
        assert metingen
        uit, _ = runner.communicate("\n", timeout=15)
        assert runner.returncode == 0
    aantallen = json.loads(uit)
    assert aantallen == {"controle": 1, "netwerk": 0, "hulpprocessen": 0}
    meet(kern, **aantallen, actuele_poolmetingen=len(metingen), poolblokkades=len(regels))
