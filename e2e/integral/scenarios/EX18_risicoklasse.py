"""Het beleid weigert het voorstel vóór uitvoering, ook bij een R0-claim van een aangemelde beheerder."""

from __future__ import annotations

import uuid

from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.hubveiligheid import meet, schrijfacties

SCENARIO = {
    "blokkade": "NIET GEVALIDEERD (echte NAS)",
    "toelichting": "De praktijkproef voor beleid en executor volgt in N-P6-03.",
    "groepen": ("hub-veiligheid",),
    "eigenaar": "jelly-leon-platform",
    "verwachting": "Een destructief voorstel met R0 krijgt serverklasse R3, een harde weigering en nul mutaties.",
}


def test_server_bepaalt_klasse(kern: Kern) -> None:
    rid = str(uuid.uuid4())
    antwoord = kern.hub.post("/api/v1/actions", headers={"Idempotency-Key": rid}, json={
        "action_id": "pool.dataset.delete", "target": {"host_id": "nas-primary"},
        "parameters": {}, "reason": "Proef classificatie zonder gegevensdoel", "risk_claim": "R0",
    })
    assert antwoord.status_code == 403 and antwoord.json()["code"] == "actie_geweigerd"
    pad = f"/api/v1/actions/{rid}"
    oordeel = kern.hub.get(pad).raise_for_status().json()
    assert oordeel["risk_claim"] == "R0" and oordeel["risk_claim_genegeerd"] is True
    assert oordeel["risk_class"] == "R3" and oordeel["goedkeuring_nodig"] is True
    assert oordeel["status"] == "geweigerd" and oordeel["goedkeuring"] is None and oordeel["gestart_op"] is None
    antwoord = kern.hub.post(pad + "/execute", headers={"Idempotency-Key": str(uuid.uuid4())})
    assert antwoord.status_code == 409 and antwoord.json()["code"] == "status_past_niet"
    assert schrijfacties(kern) == []
    meet(kern, oordeel_http=403, r3=oordeel["risk_class"] == "R3", goedkeuring_nodig=oordeel["goedkeuring_nodig"],
         uitvoering_http=antwoord.status_code, schrijfacties=0)
