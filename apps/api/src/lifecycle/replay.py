"""Persistente ``jti``-replaycache (contract 1.3 §4.6), gedeeld door alle processen via de tabel ``jwt_replay``.

- **Elke** mutatie (POST/PUT/PATCH/DELETE, ook zonder ``Idempotency-Key`` en ook als de route niets wijzigt) verbruikt
  de ``jti``. Een mutatie met een al gebruikte ``(iss, jti)`` → 401 ``token_replayed``, behalve een
  exacte herhaling (zelfde ``jti``, methode, pad en ``Idempotency-Key``): die gaat door naar de idempotency-laag, die
  het opgeslagen resultaat teruggeeft zonder tweede uitvoering.
- Lezen (GET/HEAD) wordt niet vastgelegd en mag binnen de levensduur vaker.
- Is de controle niet mogelijk (database weg), dan 503 ``replay_check_unavailable`` met ``Retry-After`` (fail-closed).
- Records blijven tot ``exp`` + 30 s (klokspeling); de worker ruimt ze daarna op.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from lifecycle.errors import ApiError
from lifecycle.models import JwtReplay
from lifecycle.timeutil import utcnow

MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
KEEP_AFTER_EXP = timedelta(seconds=30)


class TokenReplayed(Exception):
    """De ``jti`` is al gebruikt voor een andere mutatie."""


def unavailable() -> ApiError:
    return ApiError(
        503,
        "replay_check_unavailable",
        "De controle op hergebruikte tokens is nu niet mogelijk; er is niets uitgevoerd. Probeer het later opnieuw.",
        headers={"Retry-After": "5"},
    )


class ReplayCache:
    def __init__(self, factory: sessionmaker[Session]) -> None:
        self._factory = factory

    def check(self, *, iss: str, jti: str, exp: int, method: str, path: str, idempotency_key: str | None) -> None:
        method = method.upper()
        if method not in MUTATING_METHODS:
            return
        expires_at = datetime.fromtimestamp(exp, UTC) + KEEP_AFTER_EXP
        raw = (idempotency_key or "").strip()
        key = raw if 0 < len(raw) <= 64 else None  # nooit afkappen: een te lange sleutel is nooit een exacte herhaling
        try:
            with self._factory.begin() as session:
                stmt = (
                    insert(JwtReplay)
                    .values(iss=iss, jti=jti, method=method, path=path[:512], idempotency_key=key,
                            created_at=utcnow(), expires_at=expires_at)
                    .on_conflict_do_nothing(index_elements=["iss", "jti"])
                    .returning(JwtReplay.jti)
                )
                if session.execute(stmt).scalar_one_or_none() is not None:
                    return
                row = session.execute(
                    select(JwtReplay).where(JwtReplay.iss == iss, JwtReplay.jti == jti)
                ).scalar_one_or_none()
        except SQLAlchemyError as exc:
            raise unavailable() from exc
        if row is not None and key is not None and (row.method, row.path, row.idempotency_key) == (method, path, key):
            return  # exacte herhaling: de idempotency-laag geeft het opgeslagen antwoord
        raise TokenReplayed()

    def purge_expired(self) -> int:
        with self._factory.begin() as session:
            result = session.execute(delete(JwtReplay).where(JwtReplay.expires_at < utcnow()))
            return int(getattr(result, "rowcount", 0) or 0)
