"""Enrollment-client (contract §3): eenmalig token uit de omgeving of koppelcode via de statuspagina.

``POST {HUB_URL}/api/v1/services/enroll`` → eigen service-token + Hub-JWKS + publieke policy-sleutel.
Het verzoek- en antwoordschema staat niet in het contract; deze client volgt ADR-0004 en accepteert in het antwoord
zowel ``service_token`` als ``token`` en zowel ``service_id`` als ``id``. Zonder Hub blijft de dienst eerlijk
"niet gekoppeld"; een ongeldig of verlopen token wordt nooit opnieuw geprobeerd (het is eenmalig).
"""

from __future__ import annotations

import base64
import contextlib
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from sqlalchemy.orm import Session, sessionmaker

from lifecycle import APP_ID, __version__
from lifecycle.auth import JwksCache, parse_jwks, sha256_hex
from lifecycle.config import Settings
from lifecycle.hublink import JWKS_PATH, get_or_create, record_failure, store_link
from lifecycle.outbox import emit
from lifecycle.secrets_store import Vault, public_key_b64url

log = logging.getLogger("lifecycle.enrollment")

ENROLL_PATH = "/api/v1/services/enroll"
CAPABILITIES = ["read"]
FINAL_STATUS = {400, 401, 403, 404, 409, 410, 422}


class EnrollmentError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class EnrollResult:
    service_id: str
    service_token: str
    jwks: dict[str, Any] | None
    policy_public_key: str | None


def public_jwk(identity: Ed25519PrivateKey) -> dict[str, str]:
    x = public_key_b64url(identity)
    kid = base64.urlsafe_b64encode(sha256_hex(x).encode()[:16]).rstrip(b"=").decode()
    return {"kty": "OKP", "crv": "Ed25519", "x": x, "kid": f"{APP_ID}-{kid}", "use": "sig", "alg": "EdDSA"}


def build_request(kind: str, secret: str, identity: Ed25519PrivateKey, settings: Settings) -> dict[str, Any]:
    if kind not in {"token", "koppelcode"}:
        raise ValueError("kind moet 'token' of 'koppelcode' zijn")
    body: dict[str, Any] = {
        "app_id": APP_ID,
        "version": __version__,
        "kind": kind,
        "base_url": settings.public_url,
        "public_key_jwk": public_jwk(identity),
        "capabilities": CAPABILITIES,
        "schema_version": "1.0",
    }
    body["enrollment_token" if kind == "token" else "pairing_code"] = secret
    return body


def parse_response(data: Any) -> EnrollResult:
    if not isinstance(data, dict):
        raise EnrollmentError("bad_response", "De Hub gaf een onverwacht antwoord (geen JSON-object).", retryable=False)
    token = data.get("service_token") or data.get("token")
    service_id = data.get("service_id") or data.get("id")
    if not isinstance(token, str) or len(token) < 16:
        raise EnrollmentError("bad_response", "De Hub gaf geen bruikbaar service-token terug.", retryable=False)
    if not isinstance(service_id, str | int) or str(service_id) == "":
        raise EnrollmentError("bad_response", "De Hub gaf geen service-ID terug.", retryable=False)
    jwks = data.get("jwks")
    if jwks is not None:
        try:
            if not parse_jwks(jwks):
                jwks = None
        except ValueError:
            jwks = None
    policy_key = data.get("policy_public_key")
    if isinstance(policy_key, dict):
        policy_key = policy_key.get("x") or None
    return EnrollResult(
        service_id=str(service_id),
        service_token=token,
        jwks=jwks if isinstance(jwks, dict) else None,
        policy_public_key=policy_key if isinstance(policy_key, str) else None,
    )


def call_enroll(client: httpx.Client, hub_url: str, body: dict[str, Any]) -> EnrollResult:
    try:
        response = client.post(hub_url + ENROLL_PATH, json=body)
    except httpx.TimeoutException as exc:
        raise EnrollmentError("hub_timeout", "De Hub reageert niet op tijd; later opnieuw.", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise EnrollmentError("hub_unreachable", "De Hub is niet bereikbaar; later opnieuw.", retryable=True) from exc
    if response.status_code in FINAL_STATUS:
        code = "invalid_grant"
        with contextlib.suppress(Exception):  # antwoord zonder JSON: generieke code blijft staan
            code = str(response.json().get("code") or code)
        raise EnrollmentError(
            code,
            "De Hub weigerde de koppeling: het token of de koppelcode is ongeldig, verlopen of al gebruikt. "
            "Maak in de Hub een nieuwe koppelcode en vul die in op deze statuspagina.",
            retryable=False,
        )
    if response.status_code >= 300:
        raise EnrollmentError(
            "hub_error", f"De Hub gaf foutstatus {response.status_code}; later opnieuw.", retryable=True
        )
    try:
        data = response.json()
    except ValueError as exc:
        raise EnrollmentError("bad_response", "De Hub gaf geen geldige JSON terug.", retryable=False) from exc
    return parse_response(data)


def enroll_and_store(
    factory: sessionmaker[Session],
    vault: Vault,
    identity: Ed25519PrivateKey,
    settings: Settings,
    client: httpx.Client,
    *,
    hub_url: str,
    kind: str,
    secret: str,
    jwks_cache: JwksCache | None = None,
) -> EnrollResult:
    """Voert de enrollment uit en legt het resultaat (of de fout) vast. Gooit ``EnrollmentError`` bij falen."""
    secret_hash = sha256_hex(secret)
    body = build_request(kind, secret, identity, settings)
    try:
        result = call_enroll(client, hub_url, body)
    except EnrollmentError as exc:
        with factory.begin() as session:
            record_failure(
                session,
                # adres uit een mislukte koppelcode-poging niet bewaren (kan van iedereen op het netwerk komen)
                hub_url=hub_url if kind == "token" else None,
                message=exc.message,
                final=not exc.retryable,
                used_token_sha256=secret_hash if kind == "token" else None,
            )
        log.warning("enrollment mislukt: %s (opnieuw proberen: %s)", exc.code, exc.retryable)
        raise
    with factory.begin() as session:
        store_link(
            session,
            vault,
            hub_url=hub_url,
            service_id=result.service_id,
            service_token=result.service_token,
            service_token_sha256=sha256_hex(result.service_token),
            jwks=result.jwks,
            policy_public_key=result.policy_public_key,
            linked_via=kind,
            used_enrollment_token_sha256=secret_hash if kind == "token" else None,
        )
        emit(
            session,
            "service.enrollment.completed",
            "service",
            APP_ID,
            {"linked_via": kind, "service_id": result.service_id},
            actor={"type": "system", "id": APP_ID},
        )
    if jwks_cache is not None:
        jwks_cache.clear()
        if result.jwks:
            jwks_cache.seed(result.jwks, hub_url + JWKS_PATH)
    log.info("gekoppeld met de Hub (via %s)", kind)
    return result


def env_enrollment_needed(factory: sessionmaker[Session], settings: Settings) -> bool:
    """True als HUB_URL + eenmalig token gezet zijn, de dienst niet gekoppeld is en dit token nog niet faalde."""
    if not settings.hub_url or not settings.enrollment_token:
        return False
    with factory.begin() as session:
        row = get_or_create(session)
        if row.state == "gekoppeld":
            return False
        return row.used_enrollment_token_sha256 != sha256_hex(settings.enrollment_token)
