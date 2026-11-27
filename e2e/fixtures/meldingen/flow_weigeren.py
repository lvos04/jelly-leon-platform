"""Bied de Node-RED-proefactie aan op de gewone flow-API binnen de gekoppelde testdienst."""

import json
import os
import sys
import uuid

import httpx
from integrations.db import Database
from integrations.domains.automation.flowapi import PREFIX, new_token
from integrations.domains.automation.models import AutomationFlow, AutomationFlowToken
from integrations.models import AuditEvent, OutboxEvent
from sqlalchemy import select

voorstel = json.load(sys.stdin)
assert voorstel == {"action_id": "pool.dataset.delete", "target": {"host_id": "nas-primary"}, "parameters": {}}
database = Database(os.environ["HCH_DATABASE_URL"])
flow_id, spoor = uuid.uuid4(), uuid.uuid4()
token, afdruk, laatste = new_token()
with database.sessions.begin() as db:
    db.add(AutomationFlow(id=flow_id, source_id="ex45", source_flow_id=str(flow_id), label="Proefflow",
                          owner="proefeigenaar", status="actief"))
    db.flush()
    db.add(AutomationFlowToken(flow_id=flow_id, token_sha256=afdruk, last4=laatste, created_by="ex45",
                               allowed_actions=["notification.send"]))

with httpx.Client(base_url="http://localhost:30420", trust_env=False) as api:
    antwoord = api.post(PREFIX + "/actions", json={**voorstel, "reason": "Negatieve lokale flowproef"},
                        headers={"Authorization": "Bearer " + token, "Idempotency-Key": str(uuid.uuid4()),
                                 "X-Trace-Id": str(spoor)})
reden = antwoord.json().get("details", {}).get("reason")
with database.sessions() as db:
    audit = list(db.scalars(select(AuditEvent).where(AuditEvent.trace_id == spoor)))
    events = list(db.scalars(select(OutboxEvent).where(OutboxEvent.entity_id == str(flow_id))))
    weigeringen = [e for e in events if e.event_type == "flow.action.rejected"]
    assert all(e.trace_id == spoor and e.payload["reden"] == reden for e in weigeringen)
    assert all(a.entity_id == str(flow_id) and a.details.get("reden") == reden for a in audit)
    print(json.dumps({
        "http": antwoord.status_code, "reden": "destructief_verboden" if reden == "destructief_verboden" else "anders",
        "audit": sum(a.action == "automation.action.rejected" for a in audit), "geweigerd": len(weigeringen),
        "aangevraagd": sum(e.event_type == "flow.action.requested" for e in events)}))
