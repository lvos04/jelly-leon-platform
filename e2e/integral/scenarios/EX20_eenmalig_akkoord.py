"""Een dubbele aflevering van hetzelfde goedgekeurde bericht en daarna een tweede zelfstandig akkoord."""

from __future__ import annotations

from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.hubveiligheid import (
    APP_A,
    APP_B,
    akkoord,
    bezorgingen,
    meet,
    richt_schrijfpad_in,
    schrijfacties,
    stel_voor,
    uitvoeren,
)

SCENARIO = {
    "blokkade": "NIET GEVALIDEERD (echte NAS)",
    "toelichting": "De praktijkproef voor beleid en executor volgt in N-P6-03.",
    "groepen": ("hub-veiligheid",),
    "eigenaar": "jelly-leon-platform",
    "verwachting": "Twee identieke afleveringen geven één herstart. Twee afzonderlijke akkoorden geven twee herstarts.",
}


def test_eenmalig_akkoord_en_twee_geldige_akkoorden(kern: Kern) -> None:
    richt_schrijfpad_in(kern, herlever=True)
    eerste = akkoord(kern, stel_voor(kern, APP_A))
    uitvoeren(kern, eerste)
    # Het contract beantwoordt een exacte transportherhaling uit de idempotentiecache.
    assert bezorgingen(kern) == [[200, 200]]
    assert schrijfacties(kern) == ["app.redeploy"]
    # Een nieuwe uitvoering van dit al uitgevoerde voorstel moet vóór de executor stoppen.
    weigering = uitvoeren(kern, eerste, geweigerd=True)
    assert weigering["code"] == "status_past_niet"
    assert bezorgingen(kern) == [[200, 200]] and schrijfacties(kern) == ["app.redeploy"]
    tweede = akkoord(kern, stel_voor(kern, APP_B))
    assert tweede["goedkeuring"]["approval_id"] != eerste["goedkeuring"]["approval_id"]
    uitvoeren(kern, tweede)
    afleveringen = bezorgingen(kern)
    mutaties = schrijfacties(kern)
    assert afleveringen == [[200, 200], [200]] and mutaties == ["app.redeploy", "app.redeploy"]
    meet(kern, afleveringen_eerste_akkoord=len(afleveringen[0]), mutaties_eerste_akkoord=1,
         nieuw_uitvoerverzoek_geweigerd_http=409, onafhankelijke_akkoorden=2, mutaties_totaal=len(mutaties))
