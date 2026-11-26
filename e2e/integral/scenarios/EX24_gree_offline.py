"""Een eerder bereikbare unit valt weg terwijl de regelaar blijft draaien."""

from datetime import UTC, datetime

from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.huis import Huis, datagrammen, toestand

SCENARIO = {
    "eigenaar": "my_house", "verwachting": "Offlinewaarschuwing en geen nieuwe opdrachten bij UDP-uitval.",
    "groepen": ("huis",), "diensten": ("my-house",),
}


def test_unit_valt_weg(kern: Kern) -> None:
    huis = Huis(kern)
    sim = huis.gree()
    huis.unit()
    wacht("gemeten doeltemperatuur", lambda: huis.zone()["target_at"] is not None)
    online = huis.zone()
    assert online["online"] and online["setpoint_adjustable"]
    voor = len(datagrammen(sim))
    wachtrij = huis.wachtrij()
    kern.simulatorfout(sim, "onbereikbaar")
    storing = datetime.now(UTC)
    wacht("offlinewaarschuwing", lambda: huis.zone()["unit_status"] == "offline", 180)
    zone = huis.zone()
    assert zone["unit_linked"] and zone["notice_level"] == "warn" and "niet bereikbaar" in zone["notice"]
    assert not zone["online"] and not zone["setpoint_adjustable"]
    assert zone["target_c"] == online["target_c"] and datetime.fromisoformat(zone["target_at"]) <= storing
    antwoord = huis.opdracht(0, target_c=23)
    assert antwoord.status_code == 503 and antwoord.json()["detail"]["code"] == "device_offline"
    verkeer = datagrammen(sim)[voor:]
    assert any(d["soort"] == "status" and "onbereikbaar" in d["fouten"] for d in verkeer)
    assert all(d["soort"] != "cmd" for d in verkeer)
    assert huis.wachtrij() == wachtrij and toestand(sim)["gebonden"]
    huis.meet("offline", {"http": antwoord.status_code, "waarschuwing": True, "laatste_stand_behouden": True,
                         "bediening_beschikbaar": False, "nieuwe_opdrachten": 0})
