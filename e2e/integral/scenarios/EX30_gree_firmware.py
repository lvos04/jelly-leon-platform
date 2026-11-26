"""Een antwoord op scan zonder bruikbare bind geeft een onbedienbare unit."""

from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.huis import Huis, datagrammen, toestand

SCENARIO = {
    "eigenaar": "my_house", "verwachting": "Bind mislukt, apparaat niet ondersteund en geen bedienopdracht.",
    "groepen": ("huis",), "diensten": ("my-house",), "soort": "Sim+", "blokkade": "NIET GEVALIDEERD (hardware)",
    "toelichting": "Bind en terugmelding op een echte Gree-unit met afwijkende firmware toetsen in N-P6-06.",
}


def test_bind_mislukt(kern: Kern) -> None:
    huis = Huis(kern)
    sim = huis.gree()
    kern.simulatorfout(sim, "bind_mislukt")
    antwoord = huis.koppel("gree", "my-house", timeout_s=0.2)
    fout = antwoord.json()["detail"]
    assert antwoord.status_code == 502 and fout["code"] == "unsupported"
    assert fout["diagnostics"]["stage"] == "bind_failed"
    apparaat = huis.get(f"/devices/{fout['device_id']}")
    assert apparaat["compatibility"] == "unsupported"
    assert huis.get(f"/integrations/{fout['integration_id']}")["enabled"] is False
    huis.schrijf("PATCH", f"/zones/{huis.zones[0]}", {"unit_device_id": apparaat["id"]})
    geweigerd = huis.opdracht(0, mode="heat")
    assert geweigerd.status_code == 409
    reden = geweigerd.json()["detail"]
    assert reden["code"] == "policy_denied" and "niet ondersteund" in reden["message"]
    log = datagrammen(sim)
    assert any(d["soort"] == "scan" for d in log) and any("bind_mislukt" in d["fouten"] for d in log)
    assert set(toestand(sim)["bindpogingen"]) == {"v1", "v2"}
    assert not toestand(sim)["gebonden"] and not any(d["soort"] == "cmd" for d in log) and huis.wachtrij() == 0
    huis.meet("firmware", {"bind": "unsupported", "integratie_actief": False, "bedienopdrachten": 0})
