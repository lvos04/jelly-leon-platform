"""De nagebootste home-control-hub bezorgt het HTTP-voorstel van de proefflow aan de gekoppelde Hub."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from e2e.integral.project import MAP
from e2e.integral.scenarios.conftest import Kern, wacht
from lifecycle.keys import public_jwk

FIXTURES = MAP.parent / "fixtures/meldingen"
BRON = "home-control-hub"


class FlowBron:
    def __init__(self, kern: Kern):
        self.kern = kern
        self.flow_id = uuid.uuid4()
        self.actor = {"type": "flow", "id": f"flow:{self.flow_id}"}
        self.inbox = kern.client("hub", 30400)
        grant = kern.wijzig("POST", "/api/v1/services/enrollment-tokens", json={"app_id": BRON}).json()
        koppeling = self.inbox.post("/api/v1/services/enroll", json={
            "app_id": BRON, "version": "0.1.0", "base_url": None, "enrollment_token": grant["enrollment_token"],
            "public_key_jwk": public_jwk(Ed25519PrivateKey.generate()),
        }).raise_for_status().json()
        self.inbox.headers["Authorization"] = f"Bearer {koppeling['service_token']}"

    def event(self, inject: str) -> dict[str, Any]:
        # Deze ontvanger ziet uitsluitend het HTTP-bericht van de officiële Node-RED-runtime. De dienstsleutel
        # blijft bij de nagebootste afzender van de inbox. De actie en parameters komen uit de flow zelf.
        opvang = (FIXTURES / "flow-opvangen.cjs").read_text()
        voorstel = json.loads(self.kern.net.project.compose("exec", "-T", "node-red", "node", "-e", opvang, inject))
        aanleiding = str(uuid.uuid5(self.flow_id, voorstel.pop("aanleiding")))
        aanvraag = str(uuid.uuid4())
        return {
            "schema_version": "1.0", "event_type": "flow.action.requested", "event_id": str(uuid.uuid4()),
            "source_app": BRON, "source_event_id": aanleiding, "timestamp_utc": datetime.now(UTC).isoformat(),
            "trace_id": str(uuid.uuid4()), "correlation_id": aanvraag, "actor": self.actor,
            "entity": {"type": "flow", "id": str(self.flow_id)}, "classification": "internal",
            "payload": {**voorstel, "action_request_id": aanvraag, "idempotency_key": aanleiding,
                        "reason": "Voorstel van de lokale proefflow", "actor": self.actor, "expected_version": None,
                        "evidence_ids": [aanleiding], "expires_in_seconds": 900, "owner": "proefeigenaar",
                        "flow": {"id": str(self.flow_id), "label": "Proefflow", "version": 1},
                        "trigger_event_id": aanleiding, "origin_chain": [self.actor["id"]],
                        "risk_claim_genegeerd": False},
        }

    def lever(self, event: dict[str, Any]) -> str:
        r = self.inbox.post("/api/v1/events/inbox", json=event).raise_for_status()
        status = str(r.json()["status"])
        assert r.json()["event_id"] == event["event_id"]
        assert (r.status_code, status) in {(202, "accepted"), (200, "duplicate")}
        return status

    def acties(self) -> list[dict[str, Any]]:
        rijen = self.kern.hub.get("/api/v1/actions").raise_for_status().json()
        assert rijen["next_cursor"] is None
        return [r for r in rijen["items"] if r["actor"] == self.actor]

    def audit(self, actie: str) -> list[dict[str, Any]]:
        params = {"actor": self.actor["id"], "actie": actie}
        rijen = self.kern.hub.get("/api/v1/audit", params=params).raise_for_status().json()
        assert rijen["next_cursor"] is None
        return list(rijen["items"])


class Gotify:
    def __init__(self, kern: Kern):
        self.api = kern.client("gotify-sim", 30215)
        wacht("Gotify-simulator", lambda: self.api.get("/health").raise_for_status().json()["health"] == "green")
        sleutels = self.api.get("/_sim/sleutels").raise_for_status().json()
        self.applicaties = sleutels["applicaties"]
        self.api.headers["X-Gotify-Key"] = sleutels["client"]
        apps = self.api.get("/application").raise_for_status().json()
        self.bronnen = {a["name"]: a["id"] for a in apps}

    def berichten(self, bron: str) -> list[dict[str, Any]]:
        rijen = self.api.get("/message", params={"limit": 100}).raise_for_status().json()["messages"]
        return [r for r in rijen if r["appid"] == self.bronnen[bron]]
