"""Versie 1 van EX21 meet de uitgeschakelde AI terwijl de Hub een regel op nieuwe simulatordata toepast."""

from __future__ import annotations

import json

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

# De lezer koppelt de teller aan de nog levende runner en zijn echte supervisor.
# Ook tijdens de regelproef moet dit dezelfde runner blijven.
LEES_METING = r"""
import json
from pathlib import Path

bestand, = Path("/ex21-meting").iterdir()
pid = int(bestand.name)
ouder, uid = map(int, bestand.read_text().splitlines()[0].split())
proces = Path("/proc") / str(pid)
status = dict(r.split(":", 1) for r in (proces / "status").read_text().splitlines())
assert int(status["PPid"]) == ouder and int(status["Uid"].split()[1]) == uid == 569
assert (proces / "cmdline").read_bytes().split(b"\0")[-4:-1] == [b"-m", b"hub", b"ai-runner"]
assert b"/usr/local/lib/hub/supervisor.py" in (Path("/proc") / str(ouder) / "cmdline").read_bytes().split(b"\0")
regels = bestand.read_text().splitlines()[1:]
assert regels[:2] == ["netwerk", "controle_klaar"]
print(json.dumps({"pid": pid, "controle": 1, "netwerk": regels[2:].count("netwerk"),
                  "hulpprocessen": regels[2:].count("hulpproces")}))
"""


def runnerstand(kern: Kern) -> dict[str, int]:
    cijfer: dict[str, int] = json.loads(kern.net.project.compose(
        "exec", "-T", "--user", "569:569", "hub", "python", "-c", LEES_METING))
    return cijfer


def test_ai_uit_regelwerk_blijft_draaien(kern: Kern) -> None:
    vernieuw(kern)
    pad = "/api/v1/inventory/pools"

    def poolredenen() -> list[dict[str, str]]:
        redenen = kern.hub.get("/api/v1/gates/data_mutatie").raise_for_status().json()["redenen"]
        return [r for r in redenen if r["soort"] == "meting" and r["id"] == "pools"]

    gezond = kern.hub.get(pad).raise_for_status().json()
    assert gezond["status"] == "ok" and gezond["items"] and all(p["gezond"] for p in gezond["items"])
    assert poolredenen() == []
    begin = runnerstand(kern)
    start = len(oproepen(kern))
    kern.sim.post("/_sim/faults/pool_degraded", json={}).raise_for_status()
    vernieuw(kern)
    nu = kern.hub.get(pad).raise_for_status().json()
    assert nu["status"] == "ok" and any(p["status"] == "DEGRADED" and p["gezond"] is False for p in nu["items"])
    regels = poolredenen()
    assert len(regels) == 1 and regels[0]["status"] == "geblokkeerd"
    metingen = [r for r in oproepen(kern)[start:] if r["method"] == "pool.query" and r["user"] == "hub-readonly"]
    assert metingen
    einde = runnerstand(kern)
    assert begin == einde and einde["controle"] == 1 and einde["netwerk"] == einde["hulpprocessen"] == 0
    meet(kern, controle=einde["controle"], netwerk=einde["netwerk"], hulpprocessen=einde["hulpprocessen"],
         zelfde_supervisorrunner=True, actuele_poolmetingen=len(metingen), poolblokkades=len(regels))
