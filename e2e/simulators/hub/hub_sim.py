"""Hub-simulator (alleen voor tests en de integrale E2E-omgeving — géén productiecode).

Bootst het deel van de Hub (ai-server-manager) na dat diensten nodig hebben (contract §3/§4/§7):

- ``POST /api/v1/services/enroll``  eenmalig enrollment-token of koppelcode → service-token + JWKS + policy-sleutel
- ``GET  /api/v1/auth/jwks``        publieke Ed25519-sleutels (EdDSA)
- ``POST /api/v1/events/inbox``     events van diensten (Bearer service-token, dedupe op ``event_id``)

Testbediening (``/_sim/…``): grants toevoegen, JWT's slaan, ontvangen events lezen, storingen injecteren.
Het enroll-schema is net zo streng als de echte Hub (``ai-server-manager`` ``EnrollIn``, ``extra="forbid"``) en
contract 1.2 §3.3: onbekende velden, beide of geen van ``enrollment_token``/``pairing_code`` of een JWK met andere
velden dan ``kty/crv/x/kid`` → 422; ongeldig/verlopen/gebruikt of verkeerde app → 401 ``invalid_enrollment``;
succes → 200 met ``Cache-Control: no-store``. Niet tegen de echte Hub gevalideerd (markering: Sim-getest).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import re
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


ENROLL_FIELDS = {"app_id", "enrollment_token", "pairing_code", "version", "base_url", "public_key_jwk"}
APP_ID_RE = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
BASE_URL_RE = re.compile(r"^https?://[^\s/]+(:\d+)?(/.*)?$")


def enroll_problems(body: Any) -> list[str]:
    """Schemacontrole zoals de echte Hub (``EnrollIn``) en contract 1.2 §3.3; lege lijst = geldig."""
    if not isinstance(body, dict):
        return ["body moet een JSON-object zijn"]
    problems = [f"onbekend veld: {k}" for k in sorted(set(body) - ENROLL_FIELDS)]
    app_id = body.get("app_id")
    if not isinstance(app_id, str) or not APP_ID_RE.fullmatch(app_id) or len(app_id) > 40:
        problems.append("app_id ongeldig")
    token, code = body.get("enrollment_token"), body.get("pairing_code")
    if bool(token) == bool(code):
        problems.append("geef precies één van enrollment_token of pairing_code")
    if token is not None and (not isinstance(token, str) or len(token) > 128):
        problems.append("enrollment_token ongeldig")
    if code is not None and (not isinstance(code, str) or len(code) > 32):
        problems.append("pairing_code ongeldig (max. 32 tekens)")
    version = body.get("version", "")
    if not isinstance(version, str) or len(version) > 64:
        problems.append("version ongeldig")
    base_url = body.get("base_url")
    if base_url is not None and (not isinstance(base_url, str) or len(base_url) > 512
                                 or not BASE_URL_RE.fullmatch(base_url)):
        problems.append("base_url ongeldig")
    jwk = body.get("public_key_jwk")
    if jwk is not None:
        if not isinstance(jwk, dict) or not all(isinstance(v, str) for v in jwk.values()):
            problems.append("public_key_jwk moet een object met tekstwaarden zijn")
        else:
            extra = sorted(set(jwk) - {"kty", "crv", "x", "kid"})
            if extra:
                problems.append(f"public_key_jwk: niet toegestane velden {extra}")
            if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
                problems.append("public_key_jwk moet OKP/Ed25519 zijn")
            x = jwk.get("x", "")
            try:
                raw = base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))
            except (ValueError, binascii.Error):
                raw = b""
            if len(raw) != 32:
                problems.append("public_key_jwk.x moet 32 bytes zijn")
    return problems


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
        try:
            body = await request.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            state.enroll_requests.append(
                {k: v for k, v in body.items() if k not in {"enrollment_token", "pairing_code"}}
            )
        problems = enroll_problems(body)
        if problems:
            return err(422, "validation_error", "Ongeldig enroll-verzoek: " + "; ".join(problems))
        assert isinstance(body, dict)
        secret = body.get("enrollment_token") or body.get("pairing_code") or ""
        digest = hashlib.sha256(str(secret).encode()).hexdigest()
        with state.lock:
            grant = state.grants.get(digest)
            # één code voor alles (geen orakel), zoals de echte Hub: ongeldig, verlopen, gebruikt of andere app
            if (grant is None or grant["used"] or grant["expires"] < time.time()
                    or grant["app_id"] != body.get("app_id")):
                return err(401, "invalid_enrollment",
                           "Enrollment-token of koppelcode ongeldig, al gebruikt of verlopen.")
            grant["used"] = True
            token = "svc_" + secrets.token_urlsafe(32)
            service_id = str(uuid.uuid4())
            state.services[hashlib.sha256(token.encode()).hexdigest()] = {
                "service_id": service_id, "app_id": body.get("app_id"), "public_key_jwk": body.get("public_key_jwk"),
            }
        policy_raw = state.policy_key.public_key().public_bytes(serialization.Encoding.Raw,
                                                                 serialization.PublicFormat.Raw)
        policy_jwk = {"kty": "OKP", "crv": "Ed25519", "x": _b64url(policy_raw), "kid": "hub-sim-policy-1",
                      "use": "sig", "alg": "EdDSA"}
        return JSONResponse(
            {"service_id": service_id, "app_id": body["app_id"], "service_token": token, "token_type": "Bearer",
             "hub_issuer": "hub", "jwks": state.jwks(), "jwks_url": "/api/v1/auth/jwks",
             "policy_public_key": policy_jwk, "events_inbox_url": "/api/v1/events/inbox"},
            headers={"Cache-Control": "no-store"},
        )

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
    parser.add_argument("--grant", action="append", default=[], help="kind:app_id:geheim (herhaalbaar), alleen test")
    args = parser.parse_args()
    state = HubState()
    for grant in args.grant:
        kind, app_id, secret = grant.split(":", 2)
        state.add_grant(kind, app_id, secret)
    uvicorn.run(create_hub_app(state), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
