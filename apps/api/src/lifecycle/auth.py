"""Authenticatie (contract §4): Hub-JWT's via JWKS en het eigen service-token. Altijd fail-closed.

- **Gebruikerscontext**: ``Authorization: Bearer <JWT>`` van de Hub (EdDSA, ``iss=hub``, ``aud=jelly-leon-platform``,
  ``exp`` ≤ 120 s, rollen in ``roles``). De publieke sleutels komen uit ``GET {HUB_URL}/api/v1/auth/jwks`` en worden
  gecachet (TTL, minimale verversingsafstand, maximale veroudering). Is de JWKS onbereikbaar en de cache te oud of leeg,
  dan wordt **elk** JWT geweigerd (401) — nooit "open access".
- **Dienst-naar-dienst**: ``Authorization: Bearer <service-token>``: het per-app token uit de enrollment (gedeeld
  geheim Hub ↔ deze app). Alleen de SHA-256 staat in de database; vergelijking in constante tijd.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from fastapi import Request

from lifecycle import APP_ID
from lifecycle.errors import ApiError

log = logging.getLogger("lifecycle.auth")

USER_ROLES = frozenset({"admin", "operator", "viewer", "kind"})
SERVICE_ROLE = "service"
MAX_JWT_LIFETIME_S = 120
CLOCK_SKEW_S = 5

READ_ROLES = frozenset({"admin", "operator", "viewer", SERVICE_ROLE})
WRITE_ROLES = frozenset({"admin", "operator", SERVICE_ROLE})
ADMIN_ROLES = frozenset({"admin"})


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
    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


def unauthorized(reason: str, message: str = "Niet aangemeld of ongeldige aanmelding.") -> ApiError:
    return ApiError(401, "unauthorized", message, {"reason": reason}, {"WWW-Authenticate": "Bearer"})


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _b64url_decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def parse_jwks(document: Any) -> dict[str, Ed25519PublicKey]:
    """Alleen OKP/Ed25519-sleutels met een ``kid`` worden geaccepteerd; al het andere wordt genegeerd."""
    if not isinstance(document, dict) or not isinstance(document.get("keys"), list):
        raise ValueError("JWKS heeft geen 'keys'-lijst")
    keys: dict[str, Ed25519PublicKey] = {}
    for jwk in document["keys"]:
        if not isinstance(jwk, dict):
            continue
        if jwk.get("kty") != "OKP" or jwk.get("crv") != "Ed25519" or not jwk.get("kid") or not jwk.get("x"):
            continue
        if jwk.get("use") not in (None, "sig") or jwk.get("alg") not in (None, "EdDSA", "Ed25519"):
            continue
        raw = _b64url_decode(str(jwk["x"]))
        if len(raw) != 32:
            continue
        keys[str(jwk["kid"])] = Ed25519PublicKey.from_public_bytes(raw)
    return keys


class JwksCache:
    """Thread-veilige JWKS-cache. ``fetch`` haalt het JWKS-document op (gooit bij een fout)."""

    def __init__(
        self,
        url_provider: Callable[[], str | None],
        fetch: Callable[[str], Any],
        ttl_s: int = 300,
        min_refresh_s: int = 30,
        max_stale_s: int = 900,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url_provider = url_provider
        self._fetch = fetch
        self.ttl_s = ttl_s
        self.min_refresh_s = min_refresh_s
        self.max_stale_s = max_stale_s
        self._clock = clock
        self._lock = threading.Lock()
        self._keys: dict[str, Ed25519PublicKey] = {}
        self._fetched_at: float | None = None
        self._last_attempt: float | None = None
        self.last_error: str | None = None

    def seed(self, document: Any) -> None:
        """JWKS uit de enrollment-respons: telt als verse ophaling."""
        keys = parse_jwks(document)
        with self._lock:
            self._keys = keys
            self._fetched_at = self._clock()

    def status(self) -> dict[str, Any]:
        with self._lock:
            age = None if self._fetched_at is None else round(self._clock() - self._fetched_at, 1)
            return {"keys": len(self._keys), "age_s": age, "last_error": self.last_error}

    def _refresh_locked(self) -> None:
        url = self._url_provider()
        if not url:
            self.last_error = "geen Hub gekoppeld (HUB_URL onbekend)"
            return
        self._last_attempt = self._clock()
        try:
            keys = parse_jwks(self._fetch(url))
        except Exception as exc:  # netwerk, JSON, formaat
            self.last_error = f"JWKS ophalen mislukt: {type(exc).__name__}"
            log.warning("JWKS ophalen mislukt (%s)", type(exc).__name__)
            return
        if not keys:
            self.last_error = "JWKS bevat geen bruikbare Ed25519-sleutels"
            return
        self._keys = keys
        self._fetched_at = self._clock()
        self.last_error = None

    def key_for(self, kid: str | None) -> Ed25519PublicKey:
        with self._lock:
            now = self._clock()
            age = None if self._fetched_at is None else now - self._fetched_at
            unknown_kid = kid is not None and kid not in self._keys
            needs_refresh = age is None or age >= self.ttl_s or unknown_kid
            may_refresh = self._last_attempt is None or now - self._last_attempt >= self.min_refresh_s
            if needs_refresh and may_refresh:
                self._refresh_locked()
                age = None if self._fetched_at is None else self._clock() - self._fetched_at
            if age is None or age > self.max_stale_s:
                raise AuthError("jwks_unavailable", "Sleutels van de Hub zijn niet beschikbaar; aanmelding geweigerd.")
            if kid is None:
                if len(self._keys) == 1:
                    return next(iter(self._keys.values()))
                raise AuthError("jwt_kid_missing", "Token mist een sleutel-ID (kid).")
            key = self._keys.get(kid)
            if key is None:
                raise AuthError("jwt_kid_unknown", "Token is ondertekend met een onbekende sleutel.")
            return key


def verify_hub_jwt(token: str, cache: JwksCache, issuer: str = "hub", audience: str = APP_ID) -> Principal:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError("jwt_malformed", "Token is onleesbaar.") from exc
    if header.get("alg") != "EdDSA":
        raise AuthError("jwt_alg_rejected", "Alleen EdDSA-tokens van de Hub worden geaccepteerd.")
    kid = header.get("kid")
    key = cache.key_for(str(kid) if kid is not None else None)
    try:
        claims = jwt.decode(
            token,
            key,
            algorithms=["EdDSA"],
            audience=audience,
            issuer=issuer,
            leeway=CLOCK_SKEW_S,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("jwt_expired", "Token is verlopen.") from exc
    except jwt.InvalidAudienceError as exc:
        raise AuthError("jwt_audience", "Token is niet voor deze dienst bedoeld.") from exc
    except jwt.InvalidIssuerError as exc:
        raise AuthError("jwt_issuer", "Token is niet door de Hub uitgegeven.") from exc
    except jwt.PyJWTError as exc:
        raise AuthError("jwt_invalid", "Token is ongeldig.") from exc
    exp, iat = int(claims["exp"]), int(claims["iat"])
    if exp - iat > MAX_JWT_LIFETIME_S or exp - time.time() > MAX_JWT_LIFETIME_S + CLOCK_SKEW_S:
        raise AuthError("jwt_lifetime", "Token leeft langer dan 120 seconden; geweigerd.")
    raw_roles = claims.get("roles", [])
    if not isinstance(raw_roles, list) or not all(isinstance(r, str) for r in raw_roles):
        raise AuthError("jwt_roles", "Rollen in het token zijn ongeldig.")
    roles = frozenset(r for r in raw_roles if r in USER_ROLES)
    return Principal("user", str(claims["sub"]), roles)


def looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2 and token.startswith("eyJ")


class Authenticator:
    """Koppelt JWT- en service-tokencontrole; ``service_token_hash`` levert de opgeslagen SHA-256 (of None)."""

    def __init__(self, cache: JwksCache, service_token_hash: Callable[[], str | None], issuer: str = "hub") -> None:
        self.cache = cache
        self._service_token_hash = service_token_hash
        self.issuer = issuer

    def authenticate(self, authorization: str | None) -> Principal:
        if not authorization:
            raise unauthorized("missing", "Aanmelding vereist (Authorization: Bearer …).")
        scheme, _, token = authorization.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            raise unauthorized("scheme", "Alleen Bearer-aanmelding wordt ondersteund.")
        if looks_like_jwt(token):
            try:
                return verify_hub_jwt(token, self.cache, issuer=self.issuer)
            except AuthError as exc:
                raise unauthorized(exc.reason, exc.message) from exc
        expected = self._service_token_hash()
        if not expected:
            raise unauthorized("not_linked", "Deze dienst is nog niet met de Hub gekoppeld; service-token onbekend.")
        if not hmac.compare_digest(sha256_hex(token), expected):
            raise unauthorized("service_token_invalid")
        return Principal("service", "hub", frozenset({SERVICE_ROLE}))


def require_roles(allowed: frozenset[str]) -> Callable[[Request], Principal]:
    def dependency(request: Request) -> Principal:
        authenticator: Authenticator = request.app.state.authenticator
        principal = authenticator.authenticate(request.headers.get("authorization"))
        if not principal.roles & allowed:
            raise ApiError(
                403,
                "forbidden",
                "Je rol heeft geen toestemming voor deze actie.",
                {"required_any_of": sorted(allowed), "roles": sorted(principal.roles)},
            )
        request.state.principal = principal
        return principal

    return dependency


require_read = require_roles(READ_ROLES)
require_write = require_roles(WRITE_ROLES)
require_admin = require_roles(ADMIN_ROLES)
