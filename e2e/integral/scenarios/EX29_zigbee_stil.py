"""Een werkelijke MQTT-meetstroom valt stil, de worker bewaakt de ingestelde termijn."""

import time
from datetime import datetime
from typing import Any
from uuid import UUID

from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.huis import Huis, toestand

SCENARIO = {
    "eigenaar": "my_house",
    "verwachting": "Geen nieuwe Zigbee-meting, OEM-terugval en melding na de ingestelde termijn.",
    "groepen": ("huis",), "diensten": ("my-house",),
}


def test_stilte_na_meetbericht(kern: Kern) -> None:
    huis = Huis(kern)
    huis.gree()
    huis.unit()
    huis.schema(21)
    wacht("unit op de schematemperatuur", lambda: huis.zone()["target_c"] == 21
          and huis.zone()["pending_command"] is None)
    # Tweede broker luistert uitsluitend op loopback in de wegwerpcontainer. Geen seriële stick nodig.
    huis.project.compose("exec", "-d", "-T", "-u", "568:568", "my-house", "mosquitto", "-p", "1884")
    brug = huis.schrijf("POST", "/integrations/zigbee/setup", {"home_id": huis.woning})
    integratie = UUID(brug["integration_id"])
    huis.sql("""UPDATE integrations SET config = config || '{"host":"localhost","port":1884}'::jsonb
                WHERE id = :'rij';""", integratie)
    huis.schrijf("POST", "/integrations/zigbee/setup", {"home_id": huis.woning})
    sim = huis.start_sim("zigbee", 30222, "--mqtt-host", "localhost", "--mqtt-poort", "1884", "--interval", "1")

    sim.post("/_sim/meting", json={"waarden": {"temperature": 21}}).raise_for_status()

    def sensoren() -> list[Any]:
        return [d for d in huis.get(f"/homes/{huis.woning}/devices") if d["kind"] == "zigbee_sensor" and d["online"]]

    wacht("MQTT-meting door de Zigbee-adapter", lambda: bool(sensoren()))
    sensor = sensoren()[0]["id"]
    zonepad = f"/zones/{huis.zones[0]}"
    huis.schrijf("PATCH", zonepad, {"sensor_device_id": sensor})
    huis.schrijf("PUT", zonepad + "/policy", {"stale_after_s": 300})
    wacht("zone regelt met verse meetwaarde", lambda: huis.zone()["control"] == "schedule"
          and huis.zone()["temperature"]["value"] == 21 and not huis.zone()["temperature"]["stale"]
          and huis.zone()["correction_c"] == 0)
    sim.post("/_sim/meetberichten", json={"aan": False}).raise_for_status()
    stil = toestand(sim)
    assert stil["verbonden"] and not stil["meetberichten"] and stil["gepubliceerd"]
    tijdstip = datetime.fromisoformat(huis.get(f"/devices/{sensor}")["last_seen"])

    def meldingen() -> list[Any]:
        return [a for a in huis.get(f"/homes/{huis.woning}/alerts") if a["source"] == "zigbee"
                and a["key"] == f"stale:{sensor}" and "verouderd" in a["title"].lower()]

    assert not meldingen()
    termijn = huis.zone()["policy"]["stale_after_s"]
    assert termijn == 300
    huis.meet("voor_stilte", {"termijn_s": termijn, "waarschuwingen": 0, "verbonden": stil["verbonden"],
                             "schematemperatuur_c": 21, "gemeten_temperatuur_c": 21, "correctie": 0})
    # De echte bewakingslus draait één keer per minuut. Laat ook de regelaar meerdere ronden maken.
    time.sleep(termijn)

    def terugval_en_melding() -> bool:
        zone = huis.zone()
        waarschuwingen = meldingen()
        laatste = toestand(sim)
        verstreken = [(datetime.fromisoformat(a["created_at"]) - tijdstip).total_seconds() for a in waarschuwingen]
        meting = {"termijn_s": termijn, "melding_na_s": round(verstreken[0], 1) if len(verstreken) == 1 else None,
                  "aantal_meldingen": len(waarschuwingen), "nieuwe_publicaties": sum(
                      p["t"] > stil["gepubliceerd"][-1]["t"] for p in laatste["gepubliceerd"]),
                  "verbonden": laatste["verbonden"], "regeling": zone["control"],
                  "temperatuur_verouderd": zone["temperature"]["stale"], "correctie": zone["correction_c"]}
        if meting != huis.metingen.get("sensorstilte"):
            huis.meet("sensorstilte", meting)
        assert laatste["gepubliceerd"] == stil["gepubliceerd"]
        assert all(duur >= termijn for duur in verstreken)
        return zone["control"] == "oem_fallback" and bool(waarschuwingen)

    wacht("veilige terugval en waarschuwing", terugval_en_melding, 90)
    resultaat = huis.metingen["sensorstilte"]
    assert resultaat["aantal_meldingen"] == 1 and resultaat["verbonden"]
    assert resultaat["temperatuur_verouderd"] and resultaat["correctie"] == 0
