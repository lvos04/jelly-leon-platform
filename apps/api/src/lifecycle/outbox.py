"""Outbox (contract 1.3 §7): eigen events met retry/backoff, dead-letter na 10 pogingen; ook pollbaar via de connector.

- **Domeinen per eigenaar:** jelly-leon-platform schrijft alleen ``lifecycle.*``, de vaste ``service.*``-events en
  ``app.health.changed`` over zichzelf. ``event_type`` = precies drie segmenten; het derde is het werkwoord.
- **Vaste ``service.*``-events** (``entity: {type: "service", id: <app_id>}``): ``service.hub_link.linked`` (payload
  ``{via}``), ``service.hub_link.unlinked``, ``service.hub_link.revoked``, ``service.token.rotated``,
  ``service.key_rotation.applied``, ``service.key_rotation.rejected``. De 1.2-naam ``service.enrollment.completed``
  vervalt.
- **Grenzen van de Hub-inbox** worden vóór verzending gecontroleerd (``envelope_problems``): een event dat de inbox zou
  weigeren, gaat direct naar dead-letter met de reden, in plaats van tien keer verstuurd te worden.
- ``payload.ernst`` (optioneel) alleen met één van de vijf Peil-woorden.
- Niet gekoppeld = niet pushen en geen pogingen tellen (RV12, ``worker.push_outbox``); een 401 stopt de ronde zonder
  het event een poging aan te rekenen; een 429/503 stopt de ronde en volgt ``Retry-After`` (§5).
"""

from __future__ import annotations

import json
import logging
import random
import re
import uuid
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from lifecycle import APP_ID, SCHEMA_VERSION
from lifecycle.enrollment import HubSignals, note_signals, retry_after_seconds
from lifecycle.models import OutboxEvent
from lifecycle.redact import redact
from lifecycle.timeutil import iso_z, utcnow

log = logging.getLogger("lifecycle.outbox")

MAX_ATTEMPTS = 10
INBOX_PATH = "/api/v1/events/inbox"
EVENT_TYPE_RE = re.compile(r"^[a-z0-9_]+\.[a-z0-9_]+\.[a-z0-9_]+$")
ENTITY_TYPE_RE = re.compile(r"^[a-z0-9_.-]{1,64}$")
SERVICE_EVENTS = frozenset({"service.hub_link.linked", "service.hub_link.unlinked", "service.hub_link.revoked",
                            "service.token.rotated", "service.key_rotation.applied", "service.key_rotation.rejected"})
ERNST = frozenset({"kritiek", "hoog", "middel", "laag", "info"})
MAX_PAYLOAD_BYTES = 16 * 1024
ENVELOPE_FIELDS = frozenset({"schema_version", "event_id", "source_app", "source_event_id", "event_type", "entity",
                             "timestamp_utc", "trace_id", "correlation_id", "actor", "classification", "payload",
                             "payload_ref"})


def event_type_problem(event_type: str) -> str | None:
    """Eigen domein en vaste namen (§7)."""
    if len(event_type) > 128 or not EVENT_TYPE_RE.fullmatch(event_type):
        return "event_type moet precies drie segmenten [a-z0-9_] hebben (≤ 128 tekens)"
    domein = event_type.split(".", 1)[0]
    if domein == "service" and event_type not in SERVICE_EVENTS:
        return "service.* alleen uit de vaste lijst van §7"
    if domein == "app" and event_type != "app.health.changed":
        return "in het gedeelde domein app alleen app.health.changed"
    if domein not in {"lifecycle", "service", "app"}:
        return f"domein {domein} is niet van jelly-leon-platform (eigen domein: lifecycle)"
    return None


def envelope_problems(env: dict[str, Any]) -> list[str]:
    """De grenzen van de Hub-inbox (§7), vóór verzending: wat de inbox zou weigeren, wordt hier al gevonden."""
    problems: list[str] = []
    if set(env) != ENVELOPE_FIELDS:
        problems.append("onbekende of ontbrekende velden")
    if env.get("schema_version") != "1.0":
        problems.append("schema_version moet 1.0 zijn")
    if (p := event_type_problem(str(env.get("event_type", "")))) is not None:
        problems.append(p)
    if not 1 <= len(str(env.get("source_event_id") or "")) <= 200:
        problems.append("source_event_id 1–200 tekens")
    entity: dict[str, Any] = env["entity"] if isinstance(env.get("entity"), dict) else {}
    if not ENTITY_TYPE_RE.fullmatch(str(entity.get("type", ""))) or not 1 <= len(str(entity.get("id") or "")) <= 200:
        problems.append("entity.type of entity.id ongeldig")
    actor: dict[str, Any] = env["actor"] if isinstance(env.get("actor"), dict) else {}
    if not 1 <= len(str(actor.get("id") or "")) <= 128:
        problems.append("actor.id 1–128 tekens")
    payload = env.get("payload")
    if not isinstance(payload, dict):
        problems.append("payload is geen object")
    else:
        if len(json.dumps(payload, ensure_ascii=False, default=str).encode()) > MAX_PAYLOAD_BYTES:
            problems.append("payload groter dan 16 KiB")
        if "ernst" in payload and payload["ernst"] not in ERNST:
            problems.append("payload.ernst niet uit kritiek|hoog|middel|laag|info")
    if not str(env.get("timestamp_utc") or "").endswith("Z"):
        problems.append("timestamp_utc zonder tijdzone")
    return problems


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
    """Een eigen event in de outbox. Een eventnaam buiten het eigen domein of de vaste lijst is een programmeerfout."""
    if (problem := event_type_problem(event_type)) is not None:
        raise ValueError(f"{event_type}: {problem}")
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
    de worker controleert dan met ``GET /api/v1/services/self`` of de koppeling is ingetrokken (contract 1.3 §3.8).
    Een 429 of 503 telt als poging en stopt de ronde: het event krijgt zijn backoff, maar nooit korter dan
    ``Retry-After``, en via ``signals.retry_after_s`` spreekt de worker de Hub niet eerder opnieuw aan (§5).
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
        if problems := envelope_problems(envelope(event)):  # zou de inbox weigeren: niet versturen (§7)
            event.dead_letter = True
            event.last_error = ("ongeldig: " + "; ".join(problems))[:500]
            log.warning("event %s niet verstuurd: %s", event.event_id, event.last_error)
            continue
        hub_says_wait = False
        not_before: float | None = None
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
            # §5: bij 429/503 nooit eerder dan Retry-After; de Hub zegt "wacht" voor de hele dienst → ronde stoppen
            hub_says_wait = response.status_code in (429, 503)
            not_before = retry_after_seconds(response) if hub_says_wait else None
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
            delay = max(backoff_seconds(event.attempts), not_before or 0.0)
            event.next_attempt_at = utcnow() + timedelta(seconds=delay)
        if hub_says_wait:
            break
    return delivered
