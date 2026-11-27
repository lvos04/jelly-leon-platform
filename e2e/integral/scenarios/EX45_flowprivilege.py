"""Een verboden flowvoorstel moet het actiebeleid bereiken en een herleidbare weigering opleveren."""

from __future__ import annotations

import json
import uuid
from typing import Any

from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.hubveiligheid import meet, schrijfacties
from e2e.integral.scenarios.meldingen import BRON, FIXTURES, FlowBron

SCENARIO = {
    "eigenaar": "Node-RED en Hub", "groepen": ("meldingen",), "diensten": ("node-red", "home-control-hub"),
    "later_starten": ("home-control-hub",),
    "verwachting": "De Hub weigert de flowactie, registreert de weigering in de audit en voert niets uit.",
}


def productieweigering(kern: Kern, payload: dict[str, Any]) -> dict[str, Any]:
    project = kern.net.project
    grant = kern.wijzig("POST", "/api/v1/services/enrollment-tokens", json={"app_id": BRON}).json()
    project.start((BRON,), INTEGRAAL_HCH_HUB_URL="http://hub:30400", INTEGRAAL_HCH_ENROLLMENT=grant["enrollment_token"],
                  INTEGRAAL_HCH_AFDRUK=grant["keyset_fingerprint"])
    dienst = kern.client(BRON, 30420)
    wacht("gekoppelde flowdienst",
          lambda: dienst.get("/readyz").raise_for_status().json()["hub_link"]["state"] == "gekoppeld")
    voorstel = {k: payload[k] for k in ("action_id", "target", "parameters")}
    meting = project.compose("exec", "-T", "-u", "568:568", BRON, "python", "-c",
                             (FIXTURES / "flow_weigeren.py").read_text(), invoer=json.dumps(voorstel))
    uitkomst: dict[str, Any] = json.loads(meting)
    return uitkomst


def toets_productieweigering(uitkomst: dict[str, Any]) -> None:
    assert uitkomst == {"http": 403, "reden": "destructief_verboden", "audit": 1, "geweigerd": 1, "aangevraagd": 0}


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
    productie = productieweigering(kern, event["payload"])
    toets_productieweigering(productie)
    mutaties = schrijfacties(kern)
    assert mutaties == []
    meet(kern, beleidsweigeringen=1, auditregels_zelfde_flow_en_spoor=1, uitvoering_http=poging.status_code,
         schrijfacties=len(mutaties), bron_http=productie["http"], bron_audit=productie["audit"],
         bron_weigeringsmeldingen=productie["geweigerd"], bron_actieverzoeken=productie["aangevraagd"])
