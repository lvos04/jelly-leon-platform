"""Datamodel (SQLAlchemy 2). Eén schema in de eigen, ingebouwde PostgreSQL 16 — nooit in de database van een
andere app. Geheimen staan nooit leesbaar in tabellen: het service-token van de Hub-koppeling staat AES-256-GCM-
versleuteld (sleutel ``/appdata/secrets/vault.key``) plus een SHA-256 voor verificatie; alleen de laatste 4 tekens zijn
leesbaar.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from lifecycle.statemachine import LifecycleState, RunKind

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}  # noqa: RUF012 - SQLAlchemy-conventie


def _in(values: list[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


STATE_VALUES = [s.value for s in LifecycleState]
KIND_VALUES = [k.value for k in RunKind]


class LifecycleRun(Base):
    """Eén geplande of uitgevoerde levenscyclusstap (installeren, bijwerken, terugzetten, verwijderen, verhuizen)."""

    __tablename__ = "lifecycle_runs"
    __table_args__ = (
        CheckConstraint(f"state IN ({_in(STATE_VALUES)})", name="state_valid"),
        CheckConstraint(f"kind IN ({_in(KIND_VALUES)})", name="kind_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    app_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(24), default=LifecycleState.DISCOVERED.value)
    state_reason: Mapped[str | None] = mapped_column(Text)
    from_version: Mapped[str | None] = mapped_column(String(128))
    target_version: Mapped[str | None] = mapped_column(String(128))
    requested_by_type: Mapped[str] = mapped_column(String(16))
    requested_by_id: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    transitions: Mapped[list[LifecycleTransition]] = relationship(
        back_populates="run", order_by="LifecycleTransition.seq", cascade="all, delete-orphan"
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012 - SQLAlchemy-conventie


class LifecycleTransition(Base):
    __tablename__ = "lifecycle_transitions"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="run_seq"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("lifecycle_runs.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str] = mapped_column(String(24))
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict[str, Any]] = mapped_column(default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    run: Mapped[LifecycleRun] = relationship(back_populates="transitions")


class IdempotencyRecord(Base):
    """Idempotency-Key per principal: herhaling met dezelfde sleutel → hetzelfde resultaat, géén tweede uitvoering."""

    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("principal", "key", name="principal_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    principal: Mapped[str] = mapped_column(String(160))
    key: Mapped[uuid.UUID] = mapped_column(Uuid)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(512))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="in_progress")
    response_status: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column()
    response_headers: Mapped[dict[str, Any] | None] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class HubLink(Base):
    """Koppeling met de Hub (precies één rij, id=1). Bevat géén leesbare geheimen."""

    __tablename__ = "hub_link"
    __table_args__ = (
        CheckConstraint("id = 1", name="single_row"),
        CheckConstraint("state IN ('niet_gekoppeld', 'gekoppeld', 'fout')", name="state_valid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    state: Mapped[str] = mapped_column(String(16), default="niet_gekoppeld")
    hub_url: Mapped[str | None] = mapped_column(String(512))
    service_id: Mapped[str | None] = mapped_column(String(128))
    service_token_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    service_token_last4: Mapped[str | None] = mapped_column(String(4))
    service_token_sha256: Mapped[str | None] = mapped_column(String(64))
    policy_public_key: Mapped[str | None] = mapped_column(Text)
    jwks: Mapped[dict[str, Any] | None] = mapped_column()
    jwks_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    linked_via: Mapped[str | None] = mapped_column(String(16))
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_enrollment_token_sha256: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class OutboxEvent(Base):
    """Outbox (contract §7): push naar de Hub met retry/backoff, dead-letter na 10 pogingen; ook pollbaar."""

    __tablename__ = "outbox_events"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, unique=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(128))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(128))
    classification: Mapped[str] = mapped_column(String(16), default="internal")
    actor: Mapped[dict[str, Any]] = mapped_column(default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict)
    trace_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    dead_letter: Mapped[bool] = mapped_column(Boolean, default=False)
    last_error: Mapped[str | None] = mapped_column(Text)


class AuditEntry(Base):
    """Eigen auditspoor; via ``/connector/v1/evidence/{id}`` geredigeerd opvraagbaar."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    target_type: Mapped[str] = mapped_column(String(64))
    target_id: Mapped[str] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(16))
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
    trace_id: Mapped[uuid.UUID] = mapped_column(Uuid)


class WorkerHeartbeat(Base):
    """Laatste teken van leven van de worker (één rij per procesnaam); gebruikt door de connector-health."""

    __tablename__ = "worker_heartbeats"

    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    beat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    detail: Mapped[dict[str, Any]] = mapped_column(default=dict)
