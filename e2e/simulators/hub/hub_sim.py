"""Hub-simulator (alleen voor tests en de integrale E2E-omgeving — géén productiecode).

Volgt contract 1.2 **exact** voor wat diensten van de Hub nodig hebben (niet tegen de echte Hub gevalideerd:
Sim-getest; de echte Hub bouwt H1–H6 nog):

- ``POST /api/v1/services/enroll`` (§3.3): onbekend veld, schemafout, beide/geen geheim, JWK met ``d``/``use``/``alg``
  of een ``kid`` ≠ thumbprint → 422 ``validation_error`` (telt niet mee); onbekend/verlopen/gebruikt/andere app →
  één code 401 ``invalid_enrollment``; vanaf 10 mislukte pogingen (401) per bron-IP binnen 15 min → 429
  ``rate_limited`` + ``Retry-After``; toegestane herhaling (zelfde grant, app, thumbprint, binnen 60 min, uitgegeven
  token nooit gebruikt) → 200 met ``enrollment_replayed: true``; antwoord met alle velden + ``Cache-Control:
  no-store``.
- ``GET /api/v1/services/self`` en ``POST /api/v1/services/self/rotate`` (§3.6, §3.8): oude tokens geldig tot 10 min
  na het eerste gebruik van het nieuwe (max. 24 uur); ``Hub-Token-Rotation: required`` na een rotatie-aanvraag of als
  een ouder token wordt gebruikt terwijl het nieuwste nog nooit gebruikt is.
- ``POST /api/v1/events/inbox`` (Bearer service-token, dedupe op ``event_id``).
- ``GET /api/v1/auth/jwks`` en ``GET /api/v1/auth/key-rotations?after=n`` (§4.5, ondertekende statements).
- JWT's: gebruikers-JWT (``token_use: user``) en dienst-JWT (``token_use: hub_service``), met ``nbf`` en ``jti``.

De sleutelset-vingerafdruk is hier **eigen code** (niet die van de dienst); beide volgen de testvectoren van §4.5.
Testbediening: Python-methoden op ``HubState`` en een paar ``/_sim/…``-routes (compose/handmatig).
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ENROLL_FIELDS = {"app_id", "enrollment_token", "pairing_code", "version", "base_url", "public_key_jwk"}
APP_ID_RE = re.compile(r"^[a-z]([-a-z0-9]*[a-z0-9])?$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]*$")
BASE_URL_RE = re.compile(r"^https?://[^\s/:?#@]+(:\d{1,5})?(/[^\s?#]*)?$")
ENR_RE = re.compile(r"^enr_[A-Za-z0-9_-]{43}$")
TOKEN_TTL_S = 3600
FAIL_WINDOW_S = 900
FAIL_LIMIT = 10
GRACE_AFTER_FIRST_USE_S = 600
GRACE_MAX_S = 86400
FAULTS = {"jwks_down", "inbox_500", "enroll_down", "enroll_redirect", "enroll_404", "bad_fingerprint",
          "bad_response", "alias_only", "self_down", "self_404", "self_401", "rotations_404"}


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def raw_x(key: Ed25519PrivateKey) -> str:
    return b64url(key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))


def thumbprint(x: str) -> str:
    return b64url(hashlib.sha256(('{"crv":"Ed25519","kty":"OKP","x":"' + x + '"}').encode()).digest())


def fingerprint(jwt_xs: list[str], policy_x: str) -> str:
    """Eigen implementatie van §4.5 (bewust los van de dienst)."""
    jwt_part = ",".join(sorted(thumbprint(x) for x in jwt_xs))
    text = "jl-hub-keyset-v1\njwt:" + jwt_part + "\npolicy:" + thumbprint(policy_x)
    value = int.from_bytes(hashlib.sha256(text.encode()).digest()[:10], "big")
    chars = "".join(CROCKFORD[(value >> (75 - 5 * i)) & 31] for i in range(16))
    return "-".join(chars[i:i + 4] for i in range(0, 16, 4))


def normalize_code(value: str) -> str | None:
    if len(value) > 32:
        return None
    flat = "".join(c for c in value.upper() if c.isascii() and c.isalnum()).replace("O", "0").replace(
        "I", "1").replace("L", "1")
    return flat if len(flat) == 12 and all(c in CROCKFORD for c in flat) else None


def _canonical_x(x: Any) -> bool:
    if not isinstance(x, str) or len(x) != 43 or not re.fullmatch(r"[A-Za-z0-9_-]+", x):
        return False
    try:
        raw = base64.urlsafe_b64decode(x + "=")
    except (binascii.Error, ValueError):
        return False
    return len(raw) == 32 and b64url(raw) == x


def enroll_problems(body: Any) -> list[str]:
    """Schemacontrole van §3.3; lege lijst = geldig."""
    if not isinstance(body, dict):
        return ["body moet een JSON-object zijn"]
    problems = [f"onbekend veld: {k}" for k in sorted(set(body) - ENROLL_FIELDS)]
    app_id = body.get("app_id")
    if not isinstance(app_id, str) or not APP_ID_RE.fullmatch(app_id) or not 1 <= len(app_id) <= 40:
        problems.append("app_id ongeldig")
    token, code = body.get("enrollment_token"), body.get("pairing_code")
    if (token is None) == (code is None):
        problems.append("geef precies één van enrollment_token of pairing_code")
    if token is not None and (not isinstance(token, str) or not ENR_RE.fullmatch(token)):
        problems.append("enrollment_token ongeldig (enr_ + 43 tekens)")
    if code is not None and (not isinstance(code, str) or normalize_code(code) is None):
        problems.append("pairing_code ongeldig (12 tekens Crockford na normalisatie, max. 32 invoer)")
    version = body.get("version")
    if not isinstance(version, str) or not 1 <= len(version) <= 64 or not VERSION_RE.fullmatch(version):
        problems.append("version ongeldig")
    if "base_url" not in body:
        problems.append("base_url ontbreekt (mag null zijn)")
    base_url = body.get("base_url")
    if base_url is not None and (not isinstance(base_url, str) or len(base_url) > 512
                                 or not BASE_URL_RE.fullmatch(base_url)):
        problems.append("base_url ongeldig")
    jwk = body.get("public_key_jwk")
    if not isinstance(jwk, dict):
        problems.append("public_key_jwk ontbreekt")
    else:
        extra = sorted(set(jwk) - {"kty", "crv", "x", "kid"})
        if extra:
            problems.append(f"public_key_jwk: niet toegestane velden {extra}")
        if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519":
            problems.append("public_key_jwk moet OKP/Ed25519 zijn")
        if not _canonical_x(jwk.get("x")):
            problems.append("public_key_jwk.x moet 32 bytes canonieke base64url zijn")
        elif "kid" in jwk and jwk["kid"] != thumbprint(str(jwk["x"])):
            problems.append("public_key_jwk.kid moet de RFC 7638-thumbprint zijn")
    return problems


@dataclass
class Token:
    sha: str
    created_at: float
    first_used_at: float | None = None
    last_used_at: float | None = None
    revoked: bool = False


@dataclass
class Service:
    service_id: str
    app_id: str
    version: str
    enrolled_at: float
    tokens: list[Token] = field(default_factory=list)
    status: str = "actief"
    rotation_requested: bool = False


@dataclass
class Grant:
    kind: str
    app_id: str
    expires: float
    used: bool = False
    used_with_thumbprint: str | None = None
    issued_token_sha: str | None = None


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class HubState:
    clock: Callable[[], float] = time.time
    jwt_keys: list[tuple[str, Ed25519PrivateKey]] = field(
        default_factory=lambda: [("hub-sim-1", Ed25519PrivateKey.generate())])
    policy_keys: list[tuple[str, Ed25519PrivateKey]] = field(
        default_factory=lambda: [("hub-sim-policy-1", Ed25519PrivateKey.generate())])
    grants: dict[str, Grant] = field(default_factory=dict)
    services: dict[str, Service] = field(default_factory=dict)  # per app_id (één instantie per app_id)
    failures: defaultdict[str, deque[float]] = field(default_factory=lambda: defaultdict(deque))
    events: list[dict[str, Any]] = field(default_factory=list)
    seen_event_ids: set[str] = field(default_factory=set)
    faults: set[str] = field(default_factory=set)
    enroll_requests: list[dict[str, Any]] = field(default_factory=list)
    rotations: list[dict[str, Any]] = field(default_factory=list)
    calls: defaultdict[str, int] = field(default_factory=lambda: defaultdict(int))
    lock: threading.RLock = field(default_factory=threading.RLock)

    # ------------------------------------------------------------------ sleutels
    @property
    def kid(self) -> str:
        return self.jwt_keys[-1][0]

    @property
    def signing_key(self) -> Ed25519PrivateKey:
        return self.jwt_keys[-1][1]

    @staticmethod
    def _jwk(kid: str, key: Ed25519PrivateKey) -> dict[str, str]:
        return {"kty": "OKP", "crv": "Ed25519", "x": raw_x(key), "kid": kid, "use": "sig", "alg": "EdDSA"}

    def public_jwk(self) -> dict[str, str]:
        return self._jwk(*self.jwt_keys[-1])

    def jwks(self) -> dict[str, Any]:
        return {"keys": [self._jwk(k, v) for k, v in self.jwt_keys]}

    def policy_jwk(self) -> dict[str, str]:
        return self._jwk(*self.policy_keys[-1])

    def keyset_fingerprint(self) -> str:
        return fingerprint([raw_x(k) for _, k in self.jwt_keys], raw_x(self.policy_keys[-1][1]))

    # ------------------------------------------------------------------ grants en tokens
    def add_grant(self, kind: str, app_id: str, secret: str | None = None, ttl_s: int = TOKEN_TTL_S) -> str:
        if secret is None:
            if kind == "token":
                secret = "enr_" + secrets.token_urlsafe(32)
            else:
                raw = "".join(secrets.choice(CROCKFORD) for _ in range(12))
                secret = f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"
        key = normalize_code(secret) if kind == "koppelcode" else secret
        with self.lock:
            self.grants[_sha(key or secret)] = Grant(kind, app_id, self.clock() + ttl_s)
        return secret

    def _issue(self, service: Service) -> str:
        token = "svc_" + secrets.token_urlsafe(32)
        service.tokens.append(Token(_sha(token), self.clock()))
        return token

    def register_service(self, app_id: str, version: str = "0.0.0") -> tuple[str, str]:
        """Zoals na een geslaagde enrollment (voor tests die een gekoppelde dienst nodig hebben)."""
        with self.lock:
            service = self.services.get(app_id) or Service(str(uuid.uuid4()), app_id, version, self.clock())
            self.services[app_id] = service
            for t in service.tokens:
                t.revoked = True
            return service.service_id, self._issue(service)

    def _find(self, token: str) -> tuple[Service, Token] | None:
        sha = _sha(token)
        for service in self.services.values():
            for t in service.tokens:
                if t.sha == sha:
                    return service, t
        return None

    def _valid(self, service: Service, t: Token) -> bool:
        if t.revoked or service.status != "actief":
            return False
        newest = service.tokens[-1]
        if t is newest:
            return True
        bound = newest.created_at + GRACE_MAX_S
        if newest.first_used_at is not None:
            bound = min(bound, newest.first_used_at + GRACE_AFTER_FIRST_USE_S)
        return self.clock() < bound

    def authenticate(self, authorization: str | None) -> tuple[Service, Token, bool] | None:
        """(dienst, token, rotatie vereist) of None. Werkt ``first_used_at``/``last_used_at`` bij."""
        token = (authorization or "").removeprefix("Bearer ").strip()
        with self.lock:
            found = self._find(token)
            if found is None or not self._valid(*found):
                return None
            service, t = found
            now = self.clock()
            t.first_used_at = t.first_used_at or now
            t.last_used_at = now
            newest = service.tokens[-1]
            older_while_newest_unused = t is not newest and newest.first_used_at is None
            return service, t, service.rotation_requested or older_while_newest_unused

    def revoke(self, app_id: str) -> None:
        with self.lock:
            service = self.services[app_id]
            service.status = "ingetrokken"
            for t in service.tokens:
                t.revoked = True

    def request_rotation(self, app_id: str) -> None:
        with self.lock:
            self.services[app_id].rotation_requested = True

    # ------------------------------------------------------------------ JWT's
    def mint(self, sub: str, roles: list[str], aud: str, ttl_s: int = 60, iss: str = "hub", kid: str | None = None,
             key: Ed25519PrivateKey | None = None, iat_offset: int = 0, token_use: str | None = "user",
             omit: tuple[str, ...] = (), extra: dict[str, Any] | None = None, jti: str | None = None,
             typ: str = "JWT") -> str:
        now = int(self.clock()) + iat_offset
        claims: dict[str, Any] = {"iss": iss, "aud": aud, "sub": sub, "token_use": token_use, "roles": roles,
                                  "iat": now, "nbf": now, "exp": now + ttl_s, "jti": jti or str(uuid.uuid4())}
        claims.update(extra or {})
        for name in omit:
            claims.pop(name, None)
        if token_use is None:
            claims.pop("token_use", None)
        return jwt.encode(claims, key or self.signing_key, algorithm="EdDSA",
                          headers={"kid": kid or self.kid, "typ": typ})

    def mint_service(self, aud: str, service_id: str | None = None, **kw: Any) -> str:
        """Dienst-JWT (§4.3): ``sub: hub``, ``roles: [service]``, ``token_use: hub_service``, ``service_id``."""
        sid = service_id or (self.services[aud].service_id if aud in self.services else str(uuid.uuid4()))
        extra = {"service_id": sid, **kw.pop("extra", {})}
        return self.mint(kw.pop("sub", "hub"), kw.pop("roles", ["service"]), aud, token_use="hub_service",
                         extra=extra, **kw)

    # ------------------------------------------------------------------ sleutelrotatie (§4.5)
    def rotate_key(self, purpose: str = "jwt", *, retire_old: bool = False, signer: tuple[str, Ed25519PrivateKey]
                   | None = None, sequence: int | None = None, payload_override: dict[str, Any] | None = None) -> str:
        """Nieuwe sleutel + ondertekend statement. ``signer`` (standaard: huidige sleutel van dat doel) en
        ``sequence``/``payload_override`` maken ongeldige statements voor negatieve tests."""
        keys = self.jwt_keys if purpose == "jwt" else self.policy_keys
        new_kid = f"hub-sim-{purpose}-{len(keys) + 1}-{secrets.token_hex(2)}"
        new_key = Ed25519PrivateKey.generate()
        signer = signer or keys[-1]
        seq = sequence if sequence is not None else len(self.rotations) + 1
        payload = {"type": "hub_key_rotation", "iss": "hub", "sequence": seq, "issued_at": _iso(self.clock()),
                   "purpose": purpose, "add": [self._jwk(new_kid, new_key)],
                   "retire": [keys[-1][0]] if retire_old else []}
        payload.update(payload_override or {})
        statement = jwt.api_jws.PyJWS().encode(
            json.dumps(payload, separators=(",", ":")).encode(), signer[1], algorithm="EdDSA",
            headers={"kid": signer[0], "typ": "hub-key-rotation+jws"})
        with self.lock:
            self.rotations.append({"sequence": seq, "statement": statement})
            keys.append((new_kid, new_key))
            if retire_old:
                del keys[-2]
        return new_kid


def create_hub_app(state: HubState | None = None) -> FastAPI:
    state = state or HubState()
    app = FastAPI(title="hub-simulator")
    app.state.sim = state

    def err(status: int, code: str, message: str, headers: dict[str, str] | None = None) -> JSONResponse:
        return JSONResponse({"code": code, "message": message, "details": {}, "trace_id": str(uuid.uuid4())},
                            status_code=status, headers=headers)

    def enroll_response(service: Service, token: str, replayed: bool) -> JSONResponse:
        body: dict[str, Any] = {
            "schema_version": "1.2", "service_id": service.service_id, "app_id": service.app_id,
            "service_token": token, "token_type": "Bearer", "hub_issuer": "hub", "jwks": state.jwks(),
            "policy_public_key": state.policy_jwk(), "keyset_fingerprint": state.keyset_fingerprint(),
            "jwks_url": "/api/v1/auth/jwks", "events_inbox_url": "/api/v1/events/inbox",
            "key_rotation_url": "/api/v1/auth/key-rotations", "enrollment_replayed": replayed,
        }
        if "bad_fingerprint" in state.faults:
            body["keyset_fingerprint"] = "0000-0000-0000-0000"
        if "bad_response" in state.faults:
            body.pop("key_rotation_url")
        if "alias_only" in state.faults:
            body = {"id": service.service_id, "token": token, "jwks": state.jwks()}
        return JSONResponse(body, headers={"Cache-Control": "no-store"})

    @app.post("/api/v1/services/enroll")
    async def enroll(request: Request) -> Response:
        state.calls["enroll"] += 1
        if "enroll_down" in state.faults:
            return err(503, "service_unavailable", "Hub (sim) tijdelijk niet beschikbaar")
        if "enroll_404" in state.faults:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if "enroll_redirect" in state.faults:
            return Response(status_code=307, headers={"Location": "http://elders.test/api/v1/services/enroll"})
        if int(request.headers.get("content-length") or 0) > 1024 * 1024:
            return err(413, "payload_too_large", "Te groot")
        try:
            body = await request.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            state.enroll_requests.append(
                {k: v for k, v in body.items() if k not in {"enrollment_token", "pairing_code"}})
        problems = enroll_problems(body)
        if problems:
            return err(422, "validation_error", "Ongeldig enroll-verzoek: " + "; ".join(problems))
        assert isinstance(body, dict)
        source = request.client.host if request.client else "?"
        now = state.clock()
        with state.lock:
            fails = state.failures[source]
            while fails and now - fails[0] >= FAIL_WINDOW_S:
                fails.popleft()
            if len(fails) >= FAIL_LIMIT:
                retry = max(1, math.ceil(fails[0] + FAIL_WINDOW_S - now))
                return err(429, "rate_limited", "Te veel mislukte pogingen", {"Retry-After": str(retry)})
            secret = body.get("enrollment_token") or normalize_code(str(body.get("pairing_code"))) or ""
            grant = state.grants.get(_sha(secret))
            tp = thumbprint(body["public_key_jwk"]["x"])
            ok = grant is not None and grant.app_id == body["app_id"] and grant.expires > now
            if ok and grant is not None and not grant.used:
                service = state.services.get(grant.app_id)
                if service is None:
                    service = Service(str(uuid.uuid4()), grant.app_id, body["version"], now)
                    state.services[grant.app_id] = service
                for t in service.tokens:  # herinstallatie/opnieuw koppelen: oude tokens weg, service_id blijft
                    t.revoked = True
                service.status, service.version, service.enrolled_at = "actief", body["version"], now
                service.rotation_requested = False
                token = state._issue(service)
                grant.used, grant.used_with_thumbprint, grant.issued_token_sha = True, tp, _sha(token)
                fails.clear()
                return enroll_response(service, token, False)
            if ok and grant is not None and grant.used_with_thumbprint == tp:
                service = state.services[grant.app_id]
                issued = next((t for t in service.tokens if t.sha == grant.issued_token_sha), None)
                if issued is not None and issued.first_used_at is None and not issued.revoked:
                    issued.revoked = True
                    token = state._issue(service)
                    grant.issued_token_sha = _sha(token)
                    fails.clear()
                    return enroll_response(service, token, True)
            fails.append(now)
        return err(401, "invalid_enrollment", "Enrollment-token of koppelcode ongeldig, al gebruikt of verlopen.")

    @app.get("/api/v1/services/self")
    def self_info(authorization: str | None = Header(default=None)) -> Response:
        state.calls["self"] += 1
        if "self_down" in state.faults:
            return err(503, "service_unavailable", "sim")
        if "self_404" in state.faults:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        if "self_401" in state.faults:
            return err(401, "unauthorized", "Onbekend of ingetrokken service-token (sim)")
        found = state.authenticate(authorization)
        if found is None:
            return err(401, "unauthorized", "Onbekend of ingetrokken service-token")
        service, _t, rotate = found
        body = {"service_id": service.service_id, "app_id": service.app_id, "status": service.status,
                "version": service.version, "enrolled_at": _iso(service.enrolled_at),
                "keyset_fingerprint": state.keyset_fingerprint()}
        return JSONResponse(body, headers={"Hub-Token-Rotation": "required"} if rotate else None)

    @app.post("/api/v1/services/self/rotate")
    def self_rotate(authorization: str | None = Header(default=None)) -> Response:
        state.calls["rotate"] += 1
        found = state.authenticate(authorization)
        if found is None:
            return err(401, "unauthorized", "Onbekend of ingetrokken service-token")
        service, current, _ = found
        with state.lock:
            for t in service.tokens:  # eerder uitgegeven, nooit gebruikte nieuwe tokens intrekken
                if t is not current and t.first_used_at is None:
                    t.revoked = True
            token = state._issue(service)
            service.rotation_requested = False
        return JSONResponse({"service_id": service.service_id, "service_token": token,
                             "oude_tokens_geldig_tot": _iso(state.clock() + GRACE_MAX_S)},
                            headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/auth/jwks")
    def jwks() -> JSONResponse:
        if "jwks_down" in state.faults:
            return err(503, "service_unavailable", "JWKS (sim) niet beschikbaar")
        return JSONResponse(state.jwks())

    @app.get("/api/v1/auth/key-rotations")
    def key_rotations(after: int = 0) -> Response:
        state.calls["key_rotations"] += 1
        if "rotations_404" in state.faults:
            return JSONResponse({"detail": "Not Found"}, status_code=404)
        return JSONResponse({"items": [r for r in state.rotations if r["sequence"] > after]})

    @app.post("/api/v1/events/inbox")
    async def inbox(request: Request, authorization: str | None = Header(default=None)) -> JSONResponse:
        if "inbox_500" in state.faults:
            return err(500, "internal_error", "inbox (sim) faalt")
        found = state.authenticate(authorization)
        if found is None:
            return err(401, "unauthorized", "Onbekend of ingetrokken service-token")
        headers = {"Hub-Token-Rotation": "required"} if found[2] else None
        event = await request.json()
        with state.lock:
            if event["event_id"] in state.seen_event_ids:
                return JSONResponse({"status": "duplicate"}, status_code=200, headers=headers)
            state.seen_event_ids.add(event["event_id"])
            state.events.append(event)
        return JSONResponse({"status": "accepted"}, status_code=202, headers=headers)

    # ------------------------------------------------------------------ testbediening
    @app.post("/_sim/grants")
    async def add_grant(request: Request) -> dict[str, str]:
        body = await request.json()
        return {"secret": state.add_grant(body.get("kind", "token"), body["app_id"], body.get("secret"),
                                          int(body.get("ttl_s", TOKEN_TTL_S))),
                "keyset_fingerprint": state.keyset_fingerprint()}

    @app.post("/_sim/jwt")
    async def mint(request: Request) -> dict[str, str]:
        body = await request.json()
        if body.get("token_use") == "hub_service":
            return {"token": state.mint_service(body["aud"])}
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
        if name not in FAULTS:
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
    print(f"hub-sim: sleutelset-vingerafdruk {state.keyset_fingerprint()}", flush=True)
    uvicorn.run(create_hub_app(state), host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
