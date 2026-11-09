"""Hub-simulator (alleen voor tests en de integrale E2E-omgeving — géén productiecode).

Bootst het deel van de Hub (ai-server-manager) na dat diensten nodig hebben (contract §3/§4/§7):

- ``POST /api/v1/services/enroll``  eenmalig enrollment-token of koppelcode → service-token + JWKS + policy-sleutel
- ``GET  /api/v1/auth/jwks``        publieke Ed25519-sleutels (EdDSA)
- ``POST /api/v1/events/inbox``     events van diensten (Bearer service-token, dedupe op ``event_id``)

Testbediening (``/_sim/…``): grants toevoegen, JWT's slaan, ontvangen events lezen, storingen injecteren.
Het verzoek-/antwoordschema van enroll volgt ADR-0004 van jelly-leon-platform; de echte Hub kan afwijken
(markering: Sim-getest, niet tegen de echte Hub gevalideerd).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import secrets
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@dataclass
class HubState:
    signing_key: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    kid: str = "hub-sim-1"
    policy_key: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    grants: dict[str, dict[str, Any]] = field(default_factory=dict)  # sha256(secret) -> grant
    services: dict[str, dict[str, Any]] = field(default_factory=dict)  # sha256(token) -> service
    events: list[dict[str, Any]] = field(default_factory=list)
    seen_event_ids: set[str] = field(default_factory=set)
    faults: set[str] = field(default_factory=set)
    enroll_requests: list[dict[str, Any]] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def public_jwk(self) -> dict[str, str]:
        raw = self.signing_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return {"kty": "OKP", "crv": "Ed25519", "x": _b64url(raw), "kid": self.kid, "use": "sig", "alg": "EdDSA"}

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self.public_jwk()]}

    def add_grant(self, kind: str, app_id: str, secret: str | None = None, ttl_s: int = 3600) -> str:
        secret = secret or secrets.token_urlsafe(24)
        with self.lock:
            self.grants[hashlib.sha256(secret.encode()).hexdigest()] = {
                "kind": kind,
                "app_id": app_id,
                "expires": time.time() + ttl_s,
                "used": False,
            }
        return secret

    def mint(self, sub: str, roles: list[str], aud: str, ttl_s: int = 60, iss: str = "hub", kid: str | None = None,
             key: Ed25519PrivateKey | None = None, iat_offset: int = 0) -> str:
        now = int(time.time()) + iat_offset
        claims = {"iss": iss, "aud": aud, "sub": sub, "roles": roles, "iat": now, "exp": now + ttl_s,
                  "jti": str(uuid.uuid4())}
        pem = (key or self.signing_key).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        return jwt.encode(claims, pem, algorithm="EdDSA", headers={"kid": kid or self.kid})


def create_hub_app(state: HubState | None = None) -> FastAPI:
    state = state or HubState()
    app = FastAPI(title="hub-simulator")
    app.state.sim = state

    def err(status: int, code: str, message: str) -> JSONResponse:
        return JSONResponse({"code": code, "message": message, "details": {}, "trace_id": str(uuid.uuid4())},
                            status_code=status)

    @app.post("/api/v1/services/enroll")
    async def enroll(request: Request) -> JSONResponse:
        if "enroll_down" in state.faults:
            return err(503, "unavailable", "Hub (sim) tijdelijk niet beschikbaar")
        body = await request.json()
        state.enroll_requests.append({k: v for k, v in body.items() if k not in {"enrollment_token", "pairing_code"}})
        secret = body.get("enrollment_token") or body.get("pairing_code") or ""
        digest = hashlib.sha256(str(secret).encode()).hexdigest()
        with state.lock:
            grant = state.grants.get(digest)
            if grant is None or grant["used"] or grant["expires"] < time.time():
                return err(401, "invalid_grant", "Token of koppelcode ongeldig, verlopen of al gebruikt")
            if grant["app_id"] != body.get("app_id"):
                return err(403, "wrong_app", "Grant hoort bij een andere app")
            grant["used"] = True
            token = "svc_" + secrets.token_urlsafe(32)
            service_id = str(uuid.uuid4())
            state.services[hashlib.sha256(token.encode()).hexdigest()] = {
                "service_id": service_id, "app_id": body.get("app_id"), "public_key_jwk": body.get("public_key_jwk"),
            }
        policy_raw = state.policy_key.public_key().public_bytes(serialization.Encoding.Raw,
                                                                 serialization.PublicFormat.Raw)
        return JSONResponse({"service_id": service_id, "service_token": token, "jwks": state.jwks(),
                             "policy_public_key": _b64url(policy_raw)}, status_code=201)

    @app.get("/api/v1/auth/jwks")
    def jwks() -> JSONResponse:
        if "jwks_down" in state.faults:
            return err(503, "unavailable", "JWKS (sim) niet beschikbaar")
        return JSONResponse(state.jwks())

    @app.post("/api/v1/events/inbox")
    async def inbox(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
        if "inbox_500" in state.faults:
            return err(500, "internal_error", "inbox (sim) faalt")
        token = (authorization or "").removeprefix("Bearer ").strip()
        if hashlib.sha256(token.encode()).hexdigest() not in state.services:
            return err(401, "unauthorized", "Onbekend service-token")
        event = await request.json()
        with state.lock:
            if event["event_id"] in state.seen_event_ids:
                return JSONResponse({"status": "duplicate"}, status_code=200)
            state.seen_event_ids.add(event["event_id"])
            state.events.append(event)
        return JSONResponse({"status": "accepted"}, status_code=202)

    # ------------------------------------------------------------------ testbediening
    @app.post("/_sim/grants")
    async def add_grant(request: Request) -> dict[str, str]:
        body = await request.json()
        return {"secret": state.add_grant(body.get("kind", "token"), body["app_id"], body.get("secret"),
                                          int(body.get("ttl_s", 3600)))}

    @app.post("/_sim/jwt")
    async def mint(request: Request) -> dict[str, str]:
        body = await request.json()
        return {"token": state.mint(body.get("sub", "user-1"), body.get("roles", ["viewer"]), body["aud"],
                                    int(body.get("ttl_s", 60)))}

    @app.get("/_sim/events")
    def list_events() -> list[dict[str, Any]]:
        return state.events

    @app.get("/_sim/enroll-requests")
    def list_enroll_requests() -> list[dict[str, Any]]:
        return state.enroll_requests

    @app.post("/_sim/faults/{name}")
    def set_fault(name: str, on: bool = True) -> dict[str, list[str]]:
        if name not in {"jwks_down", "inbox_500", "enroll_down"}:
            raise HTTPException(404, "onbekende storing")
        (state.faults.add if on else state.faults.discard)(name)
        return {"faults": sorted(state.faults)}

    return app


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser(description="Hub-simulator (alleen test)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=30499)
    args = parser.parse_args()
    uvicorn.run(create_hub_app(), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
