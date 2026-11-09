"""Koppeling met de Hub (precies één rij ``hub_link``): lezen, opslaan en ontkoppelen. Nooit leesbare geheimen."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from lifecycle.models import HubLink
from lifecycle.redact import last4
from lifecycle.secrets_store import Vault
from lifecycle.timeutil import iso_z, utcnow

SERVICE_TOKEN_AAD = "hub_link.service_token"  # noqa: S105 - AAD-label, geen geheim
JWKS_PATH = "/api/v1/auth/jwks"

STATE_LABELS_NL = {
    "niet_gekoppeld": "Niet gekoppeld met de Hub",
    "gekoppeld": "Gekoppeld met de Hub",
    "fout": "Koppelen mislukt",
}


@dataclass(frozen=True)
class LinkSnapshot:
    state: str
    hub_url: str | None
    service_id: str | None
    service_token_last4: str | None
    service_token_sha256: str | None
    linked_via: str | None
    enrolled_at: datetime | None
    last_attempt_at: datetime | None
    last_error: str | None
    attempts: int
    used_enrollment_token_sha256: str | None
    jwks: dict[str, Any] | None
    jwks_fetched_at: datetime | None = None

    @property
    def jwks_url(self) -> str | None:
        """Alleen de Hub waarmee de koppeling geslaagd is levert sleutels (nooit een adres uit een mislukte poging)."""
        if self.state != "gekoppeld" or not self.hub_url:
            return None
        return self.hub_url + JWKS_PATH

    def public(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "state_label_nl": STATE_LABELS_NL.get(self.state, self.state),
            "hub_url": self.hub_url,
            "service_id": self.service_id,
            "service_token_last4": self.service_token_last4,
            "linked_via": self.linked_via,
            "enrolled_at": iso_z(self.enrolled_at),
            "last_attempt_at": iso_z(self.last_attempt_at),
            "last_error": self.last_error,
            "attempts": self.attempts,
        }


EMPTY = LinkSnapshot("niet_gekoppeld", None, None, None, None, None, None, None, None, 0, None, None)


def _snapshot(row: HubLink | None) -> LinkSnapshot:
    if row is None:
        return EMPTY
    return LinkSnapshot(
        state=row.state,
        hub_url=row.hub_url,
        service_id=row.service_id,
        service_token_last4=row.service_token_last4,
        service_token_sha256=row.service_token_sha256,
        linked_via=row.linked_via,
        enrolled_at=row.enrolled_at,
        last_attempt_at=row.last_attempt_at,
        last_error=row.last_error,
        attempts=row.attempts,
        used_enrollment_token_sha256=row.used_enrollment_token_sha256,
        jwks=row.jwks,
        jwks_fetched_at=row.jwks_fetched_at,
    )


def get_or_create(session: Session) -> HubLink:
    row = session.get(HubLink, 1)
    if row is None:
        row = HubLink(id=1, state="niet_gekoppeld", attempts=0)
        session.add(row)
        session.flush()
    return row


class HubLinkService:
    """Leest de koppeling met een korte cache (zodat niet elk verzoek de database raakt)."""

    def __init__(self, factory: sessionmaker[Session], cache_seconds: float = 2.0) -> None:
        self._factory = factory
        self._cache_seconds = cache_seconds
        self._lock = threading.Lock()
        self._cached: LinkSnapshot | None = None
        self._cached_at = 0.0

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None

    def current(self) -> LinkSnapshot:
        with self._lock:
            if self._cached is not None and time.monotonic() - self._cached_at < self._cache_seconds:
                return self._cached
        with self._factory() as session:
            snap = _snapshot(session.execute(select(HubLink).where(HubLink.id == 1)).scalar_one_or_none())
        with self._lock:
            self._cached, self._cached_at = snap, time.monotonic()
        return snap

    def service_token_hash(self) -> str | None:
        snap = self.current()
        return snap.service_token_sha256 if snap.state == "gekoppeld" else None

    def hub_url(self, fallback: str | None = None) -> str | None:
        snap = self.current()
        return snap.hub_url or fallback

    def jwks_url(self) -> str | None:
        return self.current().jwks_url

    def service_token(self, vault: Vault) -> str | None:
        with self._factory() as session:
            row = session.get(HubLink, 1)
            if row is None or row.state != "gekoppeld" or not row.service_token_enc:
                return None
            return vault.decrypt(row.service_token_enc, SERVICE_TOKEN_AAD)


def store_link(
    session: Session,
    vault: Vault,
    *,
    hub_url: str,
    service_id: str,
    service_token: str,
    service_token_sha256: str,
    jwks: dict[str, Any] | None,
    policy_public_key: str | None,
    linked_via: str,
    used_enrollment_token_sha256: str | None,
) -> HubLink:
    row = get_or_create(session)
    row.state = "gekoppeld"
    row.hub_url = hub_url
    row.service_id = service_id
    row.service_token_enc = vault.encrypt(service_token, SERVICE_TOKEN_AAD)
    row.service_token_last4 = last4(service_token)
    row.service_token_sha256 = service_token_sha256
    row.jwks = jwks
    row.jwks_fetched_at = utcnow() if jwks else None
    row.policy_public_key = policy_public_key
    row.linked_via = linked_via
    row.enrolled_at = utcnow()
    row.last_attempt_at = utcnow()
    row.last_error = None
    if used_enrollment_token_sha256:
        row.used_enrollment_token_sha256 = used_enrollment_token_sha256
    return row


def record_failure(
    session: Session, *, hub_url: str | None, message: str, final: bool, used_token_sha256: str | None = None
) -> HubLink:
    row = get_or_create(session)
    if row.state != "gekoppeld":
        row.state = "fout" if final else "niet_gekoppeld"
        row.hub_url = hub_url or row.hub_url
    row.last_attempt_at = utcnow()
    row.last_error = message
    row.attempts = (row.attempts or 0) + 1
    if final and used_token_sha256:
        row.used_enrollment_token_sha256 = used_token_sha256
    return row


def unlink(session: Session) -> HubLink:
    row = get_or_create(session)
    row.state = "niet_gekoppeld"
    row.service_id = None
    row.service_token_enc = None
    row.service_token_last4 = None
    row.service_token_sha256 = None
    row.policy_public_key = None
    row.jwks = None
    row.jwks_fetched_at = None
    row.linked_via = None
    row.enrolled_at = None
    row.last_error = None
    row.attempts = 0
    return row
