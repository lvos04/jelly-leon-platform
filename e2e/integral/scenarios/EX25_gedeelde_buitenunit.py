"""Twee gekoppelde binnenunits delen dezelfde buitenunit."""

from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.huis import Huis, datagrammen, toestand

SCENARIO = {
    "eigenaar": "my_house", "verwachting": "Tegengestelde modus geweigerd, verwarmen op beide units bevestigd.",
    "groepen": ("huis",), "diensten": ("my-house",),
}


def test_tegengesteld_en_gelijk(kern: Kern) -> None:
    huis = Huis(kern)
    huis.gree()
    b = huis.gree(1, aan=False)
    huis.unit()
    huis.unit(1)
    assert huis.zone()["mode"] == "heat" and huis.zone(1)["mode"] == "off"
    aantal = huis.wachtrij()
    antwoord = huis.opdracht(1, mode="cool")
    assert antwoord.status_code == 409 and "buitenunit" in antwoord.json()["detail"]["message"].lower()
    assert huis.wachtrij() == aantal and all(d["soort"] != "cmd" for d in datagrammen(b))
    akkoord = huis.opdracht(1, mode="heat")
    assert akkoord.status_code == 202 and akkoord.json()["status"] == "queued"
    wacht("verwarmen op de tweede simulator", lambda: toestand(b)["toestand"]["Pow"] == 1)
    assert toestand(b)["toestand"]["Mod"] == 4 and toestand(b)["opdrachten"]
    huis.meet("modi", {"koelen": 409, "verwarmen": 202, "tweede_unit_verwarmt": True})
