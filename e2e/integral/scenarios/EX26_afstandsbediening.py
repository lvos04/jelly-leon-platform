"""De externe uitstand overleeft nieuwe metingen en een gewijzigde schemavraag."""

from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.huis import Huis, datagrammen, toestand

SCENARIO = {
    "eigenaar": "my_house", "verwachting": "Extern uitgezet tijdens schema, pas na handmatig hervatten weer aan.",
    "groepen": ("huis",), "diensten": ("my-house",), "soort": "Sim+", "blokkade": "NIET GEVALIDEERD (hardware)",
    "toelichting": "Echte Gree-unit met de afstandsbediening uitschakelen en handmatig hervatten in N-P6-06.",
}


def test_uit_tot_hervatten(kern: Kern) -> None:
    huis = Huis(kern)
    sim = huis.gree()
    huis.unit()
    huis.schema(22)
    wacht("actief schema stuurt doeltemperatuur", lambda: toestand(sim)["toestand"]["SetTem"] == 22)
    wacht("schema door worker bevestigd", lambda: huis.zone()["target_c"] == 22)
    wachtrij = huis.wachtrij()
    sim.post("/_sim/toestand", json={"waarden": {"Pow": 0}}).raise_for_status()
    wacht("afstandsbediening als oorzaak", lambda: huis.zone()["control"] == "off" and huis.zone()["can_resume"])
    assert "afstandsbediening" in huis.zone()["notice"].lower()
    huis.schema(23)
    log = datagrammen(sim)
    wacht("twee nieuwe uitlezingen tijdens het schema", lambda: sum(d["soort"] == "status"
          for d in datagrammen(sim)[len(log):]) >= 2)
    extern = next(i for i, d in enumerate(datagrammen(sim)) if d["soort"] == "extern")
    assert all(d["soort"] != "cmd" for d in datagrammen(sim)[extern + 1:])
    assert toestand(sim)["toestand"]["Pow"] == 0 and huis.wachtrij() == wachtrij
    huis.schrijf("POST", f"/zones/{huis.zones[0]}/resume", {})
    wacht("schema hervat", lambda: toestand(sim)["toestand"]["Pow"] == 1 and toestand(sim)["toestand"]["SetTem"] == 23)
    huis.meet("afstandsbediening", {"uit_tijdens_schema": True, "nieuwe_opdrachten_voor_hervatten": 0,
                                   "na_handmatig_hervatten": "heat"})
