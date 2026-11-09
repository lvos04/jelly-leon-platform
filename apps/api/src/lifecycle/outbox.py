"""Outbox (contract §7): eigen events met retry/backoff, dead-letter na 10 pogingen; ook pollbaar via de connector."""

from __future__ import annotations

import logging
import random
import uuid
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from lifecycle import APP_ID, SCHEMA_VERSION
from lifecycle.enrollment import HubSignals, note_signals
from lifecycle.models import OutboxEvent
from lifecycle.redact import redact
from lifecycle.timeutil import iso_z, utcnow

log = logging.getLogger("lifecycle.outbox")

MAX_ATTEMPTS = 10
INBOX_PATH = "/api/v1/events/inbox"


def emit(
    session: Session,
    event_type: str,
    entity_type: str,
    entity_id: str,
    payload: dict[str, Any],
    *,
    actor: dict[str, str],
    trace_id: uuid.UUID | None = None,
    correlation_id: uuid.UUID | None = None,
    classification: str = "internal",
) -> OutboxEvent:
    event = OutboxEvent(
        event_id=uuid.uuid4(),
        event_type=event_type,
        entity_type=entity_type,
        entity_id=entity_id,
        classification=classification,
        actor=actor,
        payload=redact(payload),
        trace_id=trace_id or uuid.uuid4(),
        correlation_id=correlation_id,
        created_at=utcnow(),
        next_attempt_at=utcnow(),
        attempts=0,
        dead_letter=False,
    )
    session.add(event)
    return event


def envelope(event: OutboxEvent) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "event_id": str(event.event_id),
        "source_app": APP_ID,
        "source_event_id": str(event.id),
        "event_type": event.event_type,
        "entity": {"type": event.entity_type, "id": event.entity_id},
        "timestamp_utc": iso_z(event.created_at),
        "trace_id": str(event.trace_id),
        "correlation_id": str(event.correlation_id) if event.correlation_id else None,
        "actor": event.actor,
        "classification": event.classification,
        "payload": event.payload,
        "payload_ref": None,
    }


def backoff_seconds(attempts: int) -> float:
    base: float = min(3600.0, 2.0 ** max(0, attempts))
    return base + random.uniform(0, base / 4)  # noqa: S311 - jitter, geen cryptografie


def push_pending(
    session: Session,
    client: httpx.Client,
    hub_url: str,
    service_token: str,
    limit: int = 50,
    signals: HubSignals | None = None,
) -> int:
    """Stuurt openstaande events naar de Hub-inbox. Geeft het aantal afgeleverde events terug.

    Een 401 stopt de ronde zonder de pogingen van het event op te hogen (het ligt aan het token, niet aan het event);
    de worker controleert dan met ``GET /api/v1/services/self`` of de koppeling is ingetrokken (contract 1.2 §3.8).
    ``Hub-Token-Rotation: required`` op een antwoord wordt via ``signals`` doorgegeven.
    """
    now = utcnow()
    rows = (
        session.execute(
            select(OutboxEvent)
            .where(OutboxEvent.delivered_at.is_(None), OutboxEvent.dead_letter.is_(False))
            .where(OutboxEvent.next_attempt_at <= now)
            .order_by(OutboxEvent.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        .scalars()
        .all()
    )
    delivered = 0
    for event in rows:
        try:
            response = client.post(
                hub_url + INBOX_PATH,
                json=envelope(event),
                headers={"Authorization": f"Bearer {service_token}", "Idempotency-Key": str(event.event_id)},
            )
        except httpx.HTTPError as exc:
            ok, error = False, type(exc).__name__
        else:
            if signals is not None:
                note_signals(response, signals)
            if response.status_code == 401:
                break
            ok = 200 <= response.status_code < 300 or response.status_code == 409
            error = f"HTTP {response.status_code}"
        if ok:
            event.delivered_at = utcnow()
            event.last_error = None
            delivered += 1
            continue
        event.attempts += 1
        event.last_error = error
        if event.attempts >= MAX_ATTEMPTS:
            event.dead_letter = True
            log.warning("event %s naar dead-letter na %s pogingen", event.event_id, event.attempts)
        else:
            event.next_attempt_at = utcnow() + timedelta(seconds=backoff_seconds(event.attempts))
    return delivered
