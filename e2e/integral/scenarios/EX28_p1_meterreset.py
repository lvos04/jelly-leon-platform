"""Gekoppelde P1-meter, uur-export van my_house en B14 op beide tijdwissels."""

import secrets
from uuid import UUID

from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.huis import Huis

SCENARIO = {
    "eigenaar": "home-control-hub",
    "verwachting": "Na een tellerreset kloppen beide wisseldagen en is het resetuur gemarkeerd.",
    "groepen": ("huis", "energie"), "diensten": ("my-house", "home-control-hub"),
}


def test_uurcijfers_op_korte_en_lange_dag(kern: Kern) -> None:
    huis = Huis(kern)
    dienst = kern.client("home-control-hub", 30420)
    wacht("B14 beschikbaar", lambda: dienst.get("/healthz").is_success)
    huis.start_sim("homewizard", 30221, "--apparaat", "p1")
    meter = UUID(huis.koppel("homewizard", "my-house:30221").raise_for_status().json()["id"])
    huis.schrijf("POST", f"/devices/{meter}/bind", {"role": "grid"})
    # De rolbinding van deze pas aangemaakte woning moet de twee historische testdagen omvatten.
    huis.sql("UPDATE device_bindings SET effective_from = DATE '2026-03-01' WHERE device_id = :'rij';", meter)
    login = {"username": "energiemeting", "password": secrets.token_urlsafe(30)}
    huis.schrijf("POST", "/users", {**login, "role": "viewer"})
    lezer = kern.client("my-house", 8080)
    assert lezer.post("/api/v1/auth/login", json=login).raise_for_status().json()["user"]["role"] == "viewer"
    for richting, datum, uren, gastijden in (
        ("naar_zomertijd", "2026-03-29", 23, ["0130", "0330"]),
        ("naar_wintertijd", "2026-10-25", 25, ["0230", "0230"]),
    ):
        bron = huis.proefscript("my-house", "p1_dag.py", {"meter": str(meter), "richting": richting})
        assert bron == {"uren": uren, "gas_lokale_tijden": gastijden}
        b14 = huis.proefscript("home-control-hub", "b14_dag.py", {
            "woning": huis.woning, "datum": datum, "cookies": dict(lezer.cookies),
        })
        assert b14.pop("uurwaarden") == [1.0] * uren
        assert b14 == {"uren": uren, "tijdvakken_utc": uren, "totaal_kwh": uren,
                       "kwaliteit": "gemeten", "reset_uren_utc": [1]}
        huis.meet(richting, {**b14, **bron})
