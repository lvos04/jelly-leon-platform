"""Eigen auditspoor (geredigeerd opvraagbaar via ``/connector/v1/evidence/{id}``)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from lifecycle.models import AuditEntry
from lifecycle.redact import redact
from lifecycle.timeutil import iso_z, utcnow


def audit(
    session: Session,
    *,
    actor: dict[str, str],
    action: str,
    target_type: str,
    target_id: str,
    outcome: str,
    trace_id: uuid.UUID,
    detail: dict[str, Any] | None = None,
) -> AuditEntry:
    entry = AuditEntry(
        id=uuid.uuid4(),
        at=utcnow(),
        actor_type=actor.get("type", "system"),
        actor_id=actor.get("id", "onbekend"),
        action=action,
        target_type=target_type,
        target_id=target_id,
        outcome=outcome,
        detail=redact(detail or {}),
        trace_id=trace_id,
    )
    session.add(entry)
    return entry


def audit_view(entry: AuditEntry) -> dict[str, Any]:
    return {
        "id": str(entry.id),
        "at": iso_z(entry.at),
        "actor": {"type": entry.actor_type, "id": entry.actor_id},
        "action": entry.action,
        "target": {"type": entry.target_type, "id": entry.target_id},
        "outcome": entry.outcome,
        "detail": redact(entry.detail),
        "trace_id": str(entry.trace_id),
    }
