"""Idempotency-Key (contract §5): verplicht op mutaties; herhaling geeft hetzelfde resultaat zonder tweede uitvoering.

- Andere body met dezelfde sleutel → 422 ``idempotency_key_reused``.
- Eerste verzoek nog bezig → 409 ``idempotency_in_progress`` (na 5 minuten mag een nieuw verzoek het overnemen).
- 2xx- en 4xx-antwoorden worden bewaard en exact herhaald met de kop ``Idempotent-Replay: true`` (contract 1.3 §5;
  de 1.2-kop ``Idempotent-Replayed: true`` gaat één overgangsrelease mee); 5xx niet, zodat een herhaling na een interne
  fout opnieuw mag proberen.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from lifecycle.errors import ApiError, error_body
from lifecycle.models import IdempotencyRecord
from lifecycle.timeutil import utcnow

IN_PROGRESS_TTL = timedelta(minutes=5)
HEADER = "Idempotency-Key"

Handler = Callable[[Session], tuple[int, dict[str, Any], dict[str, str]]]


def parse_key(raw: str | None) -> uuid.UUID:
    if not raw:
        raise ApiError(400, "idempotency_key_required", "Header Idempotency-Key (UUID) is verplicht bij wijzigingen.")
    try:
        return uuid.UUID(raw.strip())
    except ValueError as exc:
        raise ApiError(400, "idempotency_key_invalid", "Idempotency-Key moet een UUID zijn.") from exc


def request_hash(method: str, path: str, body: Any) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(f"{method.upper()} {path}\n{canonical}".encode()).hexdigest()


class Idempotency:
    def __init__(self, factory: sessionmaker[Session], ttl_hours: int = 24) -> None:
        self._factory = factory
        self._ttl = timedelta(hours=ttl_hours)

    def _claim(self, principal: str, key: uuid.UUID, method: str, path: str, digest: str) -> IdempotencyRecord | None:
        """Probeert de sleutel te claimen. Geeft het bestaande record terug als dat er al is (anders None)."""
        for _ in range(2):
            try:
                with self._factory.begin() as session:
                    session.add(
                        IdempotencyRecord(
                            principal=principal,
                            key=key,
                            method=method,
                            path=path,
                            request_hash=digest,
                            status="in_progress",
                            created_at=utcnow(),
                            expires_at=utcnow() + IN_PROGRESS_TTL,
                        )
                    )
                return None
            except IntegrityError:
                pass
            with self._factory.begin() as session:
                existing = session.execute(
                    select(IdempotencyRecord).where(
                        IdempotencyRecord.principal == principal, IdempotencyRecord.key == key
                    )
                ).scalar_one_or_none()
                if existing is None:
                    continue
                if existing.status == "in_progress" and existing.expires_at <= utcnow():
                    session.delete(existing)
                    continue
                session.expunge(existing)
                return existing
        raise ApiError(409, "idempotency_in_progress", "Een verzoek met deze Idempotency-Key is nog bezig.")

    def execute(
        self,
        *,
        principal: str,
        raw_key: str | None,
        method: str,
        path: str,
        body: Any,
        handler: Handler,
        trace_id: str = "",
    ) -> JSONResponse:
        key = parse_key(raw_key)
        digest = request_hash(method, path, body)
        existing = self._claim(principal, key, method, path, digest)
        if existing is not None:
            if existing.request_hash != digest:
                raise ApiError(
                    422,
                    "idempotency_key_reused",
                    "Deze Idempotency-Key is al gebruikt voor een ander verzoek; gebruik een nieuwe sleutel.",
                )
            if existing.status != "completed":
                raise ApiError(409, "idempotency_in_progress", "Een verzoek met deze Idempotency-Key is nog bezig.")
            headers = dict(existing.response_headers or {})
            headers["Idempotent-Replay"] = "true"
            headers["Idempotent-Replayed"] = "true"  # 1.2-naam, één overgangsrelease (ADR-0019)
            replay = dict(existing.response_body or {})
            if (existing.response_status or 200) >= 400:
                replay["trace_id"] = trace_id
            return JSONResponse(replay, status_code=existing.response_status or 200, headers=headers)

        try:
            with self._factory.begin() as session:
                status, payload, headers = handler(session)
        except ApiError as err:
            if err.status >= 500:
                self._forget(principal, key)
                raise
            status, headers = err.status, dict(err.headers)
            payload = error_body(err.code, err.message, err.details, "")
            payload.pop("trace_id")
            self._complete(principal, key, status, payload, headers)
            raise
        except Exception:
            self._forget(principal, key)
            raise
        self._complete(principal, key, status, payload, headers)
        return JSONResponse(payload, status_code=status, headers=headers)

    def _complete(
        self, principal: str, key: uuid.UUID, status: int, payload: dict[str, Any], headers: dict[str, str]
    ) -> None:
        with self._factory.begin() as session:
            record = session.execute(
                select(IdempotencyRecord).where(IdempotencyRecord.principal == principal, IdempotencyRecord.key == key)
            ).scalar_one()
            record.status = "completed"
            record.response_status = status
            record.response_body = payload
            record.response_headers = headers
            record.expires_at = utcnow() + self._ttl

    def _forget(self, principal: str, key: uuid.UUID) -> None:
        with self._factory.begin() as session:
            session.execute(
                delete(IdempotencyRecord).where(IdempotencyRecord.principal == principal, IdempotencyRecord.key == key)
            )

    def purge_expired(self) -> int:
        with self._factory.begin() as session:
            result = session.execute(delete(IdempotencyRecord).where(IdempotencyRecord.expires_at < utcnow()))
            return int(getattr(result, "rowcount", 0) or 0)
