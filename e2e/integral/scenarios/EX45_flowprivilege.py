"""Een verboden flowvoorstel moet het actiebeleid bereiken en een herleidbare weigering opleveren."""

from __future__ import annotations

import uuid

from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.hubveiligheid import meet, schrijfacties
from e2e.integral.scenarios.meldingen import FlowBron

SCENARIO = {
    "eigenaar": "Node-RED en Hub", "groepen": ("meldingen",), "diensten": ("node-red",),
    "verwachting": "De Hub weigert de flowactie, registreert de weigering in de audit en voert niets uit.",
}


def test_verboden_flowactie(kern: Kern) -> None:
    bron = FlowBron(kern)
    event = bron.event("verboden")
    assert bron.lever(event) == "accepted"
    oordeel, = bron.acties()
    assert oordeel["action_id"] == "pool.dataset.delete" and oordeel["risk_class"] == "R3"
    assert oordeel["status"] == "geweigerd" and oordeel["gestart_op"] is None
    actiepad = f"/api/v1/actions/{oordeel['request_id']}"
    detail = kern.hub.get(actiepad).raise_for_status().json()
    assert "actie_verboden_destructief" in [b["code"] for b in detail["blokkades"]]
    rij, = bron.audit("action.proposal.refused")
    assert rij["actor_type"] == "flow" and rij["actor_id"] == bron.actor["id"]
    assert rij["doel_id"] == oordeel["request_id"] and rij["trace_id"] == event["trace_id"]
    poging = kern.hub.post(actiepad + "/execute",
                           headers={"Idempotency-Key": str(uuid.uuid4())})
    assert poging.status_code == 409 and poging.json()["code"] == "status_past_niet"
    mutaties = schrijfacties(kern)
    assert mutaties == []
    meet(kern, beleidsweigeringen=1, auditregels_zelfde_flow_en_spoor=1, uitvoering_http=poging.status_code,
         schrijfacties=len(mutaties))
