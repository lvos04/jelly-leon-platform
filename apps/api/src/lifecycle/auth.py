"""Authenticatie (contract 1.3 §4): alleen Hub-JWT's, alleen met **gepinde** sleutels, altijd fail-closed.

- Vertrouwensset = de bij een geslaagde koppeling gepinde sleutels in ``hub-link.json`` (``pins.jwt``). Een via HTTP
  opgehaalde JWKS voegt nooit vertrouwen toe; nieuwe sleutels komen alleen via opnieuw koppelen of een ondertekende
  sleutelrotatie (``lifecycle.keyrotation``). Een onbekende ``kid`` → 401 ``unknown_key`` en een extra poll op de
  achtergrond (het verzoek wacht daar niet op).
- Twee soorten JWT (``token_use``): ``user`` (gebruikerscontext, rollen admin/operator/viewer/kind; ``kind`` wint
  altijd) en ``hub_service`` (de Hub zelf: ``sub: "hub"``, ``roles: ["service"]``, eigen ``service_id``).
- Een inkomend ``Bearer svc_…`` (het eigen service-token) wordt **niet** geaccepteerd: dat token gaat bij elke
  dienst → Hub-aanroep over het LAN en mag niemand als de Hub laten optreden.
- ``aud`` is **precies één string** gelijk aan de eigen ``app_id``; een lijst (ook met de eigen ``app_id`` erin) → 401
  ``invalid_token`` (1.3 §4.2: één JWT werkt nooit bij meerdere diensten met elk een eigen replaycache).
- ``jti`` verplicht; **elke** POST/PUT/PATCH/DELETE verbruikt de ``jti``, ook zonder ``Idempotency-Key``; een al
  gebruikte → 401 ``token_replayed`` (``lifecycle.replay``).
- 401 ``{code: "unauthorized", details: {reason}}`` met de vaste ``reason`` uit §4.7 (geen header, ander schema of lege
  waarde → ``invalid_token``; geen ``kid`` → ``unknown_key``) en de controlevolgorde ``1 MiB-laag → state → tokensoort →
  handtekening/kid → claims → token_use → jti → body``. Rol (403) en ``Idempotency-Key`` komen pas ná de authenticatie;
  een onleesbare body zonder geldige aanmelding geeft dus ook 401 (``lifecycle.errors``, RV9).
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jwt
from fastapi import Request

from lifecycle import APP_ID
from lifecycle.errors import ApiError
from lifecycle.hublink import HubLinkStore, Link, same_uuid
from lifecycle.keys import KeyFormatError
from lifecycle.replay import ReplayCache, TokenReplayed

log = logging.getLogger("lifecycle.auth")

USER_ROLES = frozenset({"admin", "operator", "viewer", "kind"})
SERVICE_ROLE = "service"
MAX_JWT_LIFETIME_S = 120
CLOCK_SKEW_S = 30
MAX_TOKEN_CHARS = 8192

READ_ROLES = frozenset({"admin", "operator", "viewer", SERVICE_ROLE})
WRITE_ROLES = frozenset({"admin", "operator", SERVICE_ROLE})
ADMIN_ROLES = frozenset({"admin"})
UNLINK_ROLES = frozenset({"admin", SERVICE_ROLE})  # §3.8/§6: dienst-JWT of gebruikers-JWT admin

REASON_MESSAGES_NL = {
    "not_linked": "Deze dienst is niet met de Hub gekoppeld; aanmelding geweigerd.",
    "revoked": "De Hub heeft deze koppeling ingetrokken; aanmelding geweigerd. Opnieuw koppelen gaat via de Hub.",
    "service_token_not_accepted": "Een service-token wordt hier niet geaccepteerd; de Hub meldt zich met een "
    "dienst-JWT.",
    "unknown_key": "Het token is ondertekend met een sleutel die deze dienst niet vertrouwt.",
    "token_expired": "Het token is verlopen.",
    "invalid_token": "Het token is ongeldig.",
    "token_use_invalid": "Soort token (token_use), onderwerp of rollen kloppen niet.",
    "jti_missing": "Het token mist een jti.",
    "token_replayed": "Dit token is al gebruikt voor een andere wijziging; vraag een nieuw token aan.",
}


@dataclass(frozen=True)
class Principal:
    kind: str  # "user" | "service"
    subject: str
    roles: frozenset[str] = field(default_factory=frozenset)

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.subject}"

    @property
    def actor(self) -> dict[str, str]:
        return {"type": "user" if self.kind == "user" else "system", "id": self.subject}


class AuthError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.message = REASON_MESSAGES_NL[reason]


def unauthorized(reason: str) -> ApiError:
    return ApiError(401, "unauthorized", REASON_MESSAGES_NL[reason], {"reason": reason}, {"WWW-Authenticate": "Bearer"})


def _int_claim(claims: dict[str, Any], name: str) -> int:
    value = claims.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise AuthError("invalid_token")
    return value


def verify_hub_jwt(
    token: str,
    link: Link,
    *,
    audience: str = APP_ID,
    issuer: str = "hub",
    now: float | None = None,
    on_unknown_kid: Callable[[], None] | None = None,
) -> tuple[Principal, dict[str, Any]]:
    """Tokensoort → handtekening/kid → claims → token_use (de ``state``-controle doet de aanroeper eerst)."""
    if token.startswith("svc_"):
        raise AuthError("service_token_not_accepted")
    if len(token) > MAX_TOKEN_CHARS or token.count(".") != 2:
        raise AuthError("invalid_token")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError("invalid_token") from exc
    if header.get("alg") != "EdDSA" or str(header.get("typ", "JWT")).upper() != "JWT":
        raise AuthError("invalid_token")
    kid = header.get("kid")
    pins = link.pin_map("jwt")
    pin = pins.get(kid) if isinstance(kid, str) else None
    if pin is None:
        if on_unknown_kid is not None:
            on_unknown_kid()
        raise AuthError("unknown_key")
    try:
        claims = jwt.decode(
            token,
            pin.public_key(),  # vergelijking op de gepinde sleutel zelf (x), niet alleen op kid
            algorithms=["EdDSA"],
            options={"verify_exp": False, "verify_nbf": False, "verify_iat": False, "verify_aud": False,
                     "verify_iss": False, "verify_sub": False, "verify_jti": False, "require": []},
        )
    except (jwt.PyJWTError, KeyFormatError, ValueError) as exc:
        raise AuthError("invalid_token") from exc
    now = time.time() if now is None else now
    if claims.get("iss") != issuer:
        raise AuthError("invalid_token")
    aud = claims.get("aud")
    if not isinstance(aud, str) or aud != audience:  # 1.3 §4.2: een lijst is altijd ongeldig
        raise AuthError("invalid_token")
    exp, iat, nbf = _int_claim(claims, "exp"), _int_claim(claims, "iat"), _int_claim(claims, "nbf")
    if exp - iat > MAX_JWT_LIFETIME_S or exp < iat or nbf > exp:
        raise AuthError("invalid_token")
    if iat > now + CLOCK_SKEW_S or nbf > now + CLOCK_SKEW_S:
        raise AuthError("invalid_token")
    if now > exp + CLOCK_SKEW_S:
        raise AuthError("token_expired")
    sub, raw_roles = claims.get("sub"), claims.get("roles")
    if not isinstance(sub, str) or not sub or len(sub) > 128:
        raise AuthError("invalid_token")
    if not isinstance(raw_roles, list) or not all(isinstance(r, str) for r in raw_roles):
        raise AuthError("invalid_token")
    token_use = claims.get("token_use")
    if token_use == "user":  # noqa: S105 - tokensoort, geen wachtwoord
        if SERVICE_ROLE in raw_roles:
            raise AuthError("token_use_invalid")
        roles = frozenset(r for r in raw_roles if r in USER_ROLES)  # onbekende rollen geven nooit rechten
        if "kind" in roles:
            roles = frozenset({"kind"})  # §4.2: rol 'kind' wint altijd
        principal = Principal("user", sub, roles)
    elif token_use == "hub_service":  # noqa: S105 - tokensoort, geen wachtwoord
        if sub != "hub" or raw_roles != [SERVICE_ROLE] or not same_uuid(claims.get("service_id"), link.service_id):
            raise AuthError("token_use_invalid")
        principal = Principal("service", "hub", frozenset({SERVICE_ROLE}))
    else:
        raise AuthError("token_use_invalid")
    jti = claims.get("jti")
    if jti is None or jti == "":
        raise AuthError("jti_missing")
    if not isinstance(jti, str) or len(jti) > 64:
        raise AuthError("invalid_token")
    try:
        uuid.UUID(jti)
    except ValueError as exc:
        raise AuthError("invalid_token") from exc
    return principal, claims


class Authenticator:
    """Volledige controle in de volgorde van §4.7, inclusief de replaycache voor mutaties."""

    def __init__(
        self,
        store: HubLinkStore,
        replay: ReplayCache | None,
        *,
        on_unknown_kid: Callable[[], None] | None = None,
        issuer: str = "hub",
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.replay = replay
        self.on_unknown_kid = on_unknown_kid
        self.issuer = issuer
        self.clock = clock

    def authenticate(
        self, authorization: str | None, *, method: str = "GET", path: str = "/", idempotency_key: str | None = None
    ) -> Principal:
        link = self.store.read()
        if not link.linked:
            raise unauthorized("revoked" if link.state == "ingetrokken" and not link.corrupt else "not_linked")
        scheme, _, token = (authorization or "").partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            raise unauthorized("invalid_token")
        try:
            principal, claims = verify_hub_jwt(
                token, link, issuer=self.issuer, now=self.clock(), on_unknown_kid=self.on_unknown_kid
            )
            if self.replay is not None:
                self.replay.check(iss=self.issuer, jti=str(claims["jti"]), exp=int(claims["exp"]), method=method,
                                  path=path, idempotency_key=idempotency_key)
        except AuthError as exc:
            raise unauthorized(exc.reason) from exc
        except TokenReplayed as exc:
            raise unauthorized("token_replayed") from exc
        return principal


def require_roles(allowed: frozenset[str]) -> Callable[[Request], Principal]:
    def dependency(request: Request) -> Principal:
        authenticator: Authenticator = request.app.state.authenticator
        principal = authenticator.authenticate(
            request.headers.get("authorization"),
            method=request.method,
            path=request.url.path,
            idempotency_key=request.headers.get("idempotency-key"),
        )
        if not principal.roles & allowed:
            raise ApiError(
                403,
                "forbidden",
                "Je rol heeft geen toestemming voor deze actie.",
                {"reason": "rol_onvoldoende", "required_any_of": sorted(allowed), "roles": sorted(principal.roles)},
            )
        request.state.principal = principal
        return principal

    return dependency


require_read = require_roles(READ_ROLES)
require_write = require_roles(WRITE_ROLES)
require_admin = require_roles(ADMIN_ROLES)
require_unlink = require_roles(UNLINK_ROLES)
