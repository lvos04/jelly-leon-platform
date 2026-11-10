"""Worker (apart proces in de alles-in-één image). Voert in fase P0 niets uit op de NAS.

Per ronde: hartslag; koppelen met het eenmalige env-token (backoff, 60-minutengrens, herstel na herstart);
de koppeling bevestigen met ``GET /api/v1/services/self``; het service-token roteren als de Hub
``Hub-Token-Rotation: required`` meldt (de aanvraag staat als ``token_rotation_requested_at`` in ``hub-link.json`` en
overleeft zo een herstart, contract 1.3 §3.5/§3.8); de outbox naar de Hub (alleen gekoppeld, RV12; een 401 → controle
of de koppeling is ingetrokken); ondertekende sleutelrotaties ophalen (≤ elke 15 min); verlopen Idempotency- en
replay-records opruimen. Geeft de Hub 429/503 met ``Retry-After``, dan spreekt de worker hem niet eerder opnieuw aan
(contract 1.3 §5); een tijdelijk mislukte rotatie gaat opnieuw met backoff + jitter (5 s → 60 s), nooit elke ronde.

Events over de eigen koppeling (contract 1.3 §7, vaste ``service.*``-namen, ``entity`` = ``{service, <app_id>}``): de
worker vergelijkt elke ronde de koppelstatus met de vorige (ook als het API-proces de wijziging deed) en schrijft per
overgang één event in de outbox: ``service.hub_link.linked`` (ook opnieuw koppelen), ``.unlinked``, ``.revoked``,
``service.key_rotation.applied`` / ``.rejected``; ``service.token.rotated`` na een geslaagde rotatie.
"""

from __future__ import annotations

import logging
import random
import signal
import threading
import time
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from lifecycle import APP_ID, __version__
from lifecycle.app import HttpClientFactory, default_http_client_factory
from lifecycle.config import Settings
from lifecycle.db import make_engine, make_session_factory
from lifecycle.enrollment import (
    EnvEnrollment,
    HubSignals,
    _persist,
    check_revoked,
    confirm,
    rotate_service_token,
)
from lifecycle.hublink import HubLinkStore, now_iso
from lifecycle.idempotency import Idempotency
from lifecycle.keyrotation import poll as poll_key_rotations
from lifecycle.models import WorkerHeartbeat
from lifecycle.outbox import emit, push_pending
from lifecycle.replay import ReplayCache
from lifecycle.secrets_store import SecretsError, load_identity, secrets_present
from lifecycle.timeutil import utcnow

log = logging.getLogger("lifecycle.worker")

CONFIRM_RETRY_S = 60.0
PURGE_INTERVAL_S = 300.0
# Rotatie op verzoek van de Hub (§3.8: binnen 5 min): mislukt ze tijdelijk, dan opnieuw met backoff + jitter (5 s →
# hooguit 60 s), nooit elke ronde.
ROTATE_BACKOFF_START_S = 5.0
ROTATE_BACKOFF_MAX_S = 60.0


class Worker:
    def __init__(self, settings: Settings, http_client_factory: HttpClientFactory | None = None) -> None:
        self.settings = settings
        self.engine = make_engine(settings.database_url)
        self.factory = make_session_factory(self.engine)
        self.client_factory = http_client_factory or default_http_client_factory(settings)
        self.store = HubLinkStore(settings.secrets_dir)
        self.env = EnvEnrollment(settings, self.store)
        self.idempotency = Idempotency(self.factory, settings.idempotency_ttl_hours)
        self.replay = ReplayCache(self.factory)
        self.signals = HubSignals()
        self.stop = threading.Event()
        self.next_confirm_at = 0.0
        self.next_key_poll_at = 0.0
        self.next_rotate_at = 0.0
        self.rotate_backoff = ROTATE_BACKOFF_START_S
        # §5: na een 429/503 met Retry-After spreekt de worker de Hub (bevestigen, rotatie, outbox, sleutelrotaties)
        # niet eerder opnieuw aan
        self.hub_not_before = 0.0
        self.last_purge = 0.0
        self.recovered = False
        self.last_state: tuple[str, str | None, int, int | None] = self._state()
        # een aangevraagde rotatie overleeft een herstart (§3.5 token_rotation_requested_at)
        self.signals.rotation_required = bool(self.store.read().doc.get("token_rotation_requested_at"))

    def _state(self) -> tuple[str, str | None, int, int | None]:
        link = self.store.read()
        blocked = link.doc.get("key_rotation_blocked_sequence") if not link.corrupt else None
        return (("onleesbaar" if link.corrupt else link.state), link.doc.get("linked_at"), link.rotation_sequence,
                blocked if isinstance(blocked, int) else None)

    def heartbeat(self, detail: dict[str, Any]) -> None:
        with self.factory.begin() as session:
            stmt = insert(WorkerHeartbeat).values(name="worker", beat_at=utcnow(), detail=detail)
            session.execute(
                stmt.on_conflict_do_update(index_elements=["name"], set_={"beat_at": utcnow(), "detail": detail})
            )

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        with self.factory.begin() as session:
            emit(session, event_type, "service", APP_ID, payload, actor={"type": "system", "id": APP_ID})

    def _emit_transition(self, before: tuple[str, str | None, int, int | None],
                         after: tuple[str, str | None, int, int | None]) -> None:
        """Eén event per overgang (§7, vaste namen; geen geheimen). Een nieuwe koppeling (ook opnieuw koppelen: ander
        ``linked_at``) → ``service.hub_link.linked`` met ``{via}``."""
        if before == after:
            return
        state, linked_at, sequence, blocked = after
        link = self.store.read()
        if state == "gekoppeld" and (before[0] != "gekoppeld" or before[1] != linked_at):
            self._emit("service.hub_link.linked", {"via": link.doc.get("via"), "ernst": "info"})
        elif state == "ontkoppeld" and before[0] != "ontkoppeld":
            self._emit("service.hub_link.unlinked", {"ernst": "middel"})
        elif state == "ingetrokken" and before[0] != "ingetrokken":
            self._emit("service.hub_link.revoked", {"ernst": "hoog"})
        elif state == "gekoppeld" and before[0] == "gekoppeld" and before[1] == linked_at:
            if sequence > before[2]:
                self._emit("service.key_rotation.applied", {"rotation_sequence": sequence, "ernst": "info",
                                                           "keyset_fingerprint": link.keyset_fingerprint})
            if blocked is not None and blocked != before[3]:
                self._emit("service.key_rotation.rejected", {"sequence": blocked, "ernst": "hoog"})

    def enroll(self) -> str:
        if not self.recovered:
            self.env.recover_after_restart()
            self.recovered = True
        try:
            return self.env.tick(self.client_factory, lambda: load_identity(self.settings.secrets_dir))
        except SecretsError as exc:
            return f"geheimen:{exc}"

    def hub_waits(self) -> bool:
        """De Hub vroeg te wachten (429/503 met ``Retry-After``, §5): tot dan geen aanroepen naar de Hub."""
        return time.monotonic() < self.hub_not_before

    def note_hub_wait(self) -> None:
        if self.signals.retry_after_s:
            self.hub_not_before = max(self.hub_not_before, time.monotonic() + self.signals.retry_after_s)
            log.info("de Hub vraagt %.0f s te wachten (Retry-After); tot dan geen aanroepen",
                     self.signals.retry_after_s)
        self.signals.retry_after_s = None

    def confirm_link(self) -> str:
        link = self.store.read()
        if not link.linked or link.doc.get("confirmed_at") or time.monotonic() < self.next_confirm_at:
            return "niet_nodig"
        if self.hub_waits():
            return "wacht:retry_after"
        with self.client_factory() as client:
            result = confirm(self.store, client, self.signals)
        if result != "bevestigd":
            self.next_confirm_at = time.monotonic() + CONFIRM_RETRY_S
        return result

    def rotate_if_requested(self) -> str:
        link = self.store.read()
        if not link.linked:
            return "niet_nodig"
        if link.doc.get("token_rotation_requested_at"):
            self.signals.rotation_required = True
        if not self.signals.rotation_required:
            return "niet_nodig"
        if not link.doc.get("token_rotation_requested_at"):  # eerst vastleggen: overleeft een herstart (§3.5)
            _persist(self.store, lambda d: d.update(token_rotation_requested_at=now_iso())
                     if d.get("state") == "gekoppeld" else None)
        if self.hub_waits():
            return "wacht:retry_after"
        if time.monotonic() < self.next_rotate_at:
            return "wachten"
        with self.client_factory() as client:
            result = rotate_service_token(self.store, client, self.signals)
        if result == "geroteerd":
            self.rotate_backoff, self.next_rotate_at = ROTATE_BACKOFF_START_S, 0.0
            self._emit("service.token.rotated", {"ernst": "info"})
        elif result != "ingetrokken":  # tijdelijk mislukt: niet elke ronde opnieuw (§5 backoff + jitter)
            delay = min(ROTATE_BACKOFF_MAX_S, self.rotate_backoff * random.uniform(1.0, 1.25))  # noqa: S311
            self.next_rotate_at = time.monotonic() + delay
            self.rotate_backoff = min(ROTATE_BACKOFF_MAX_S, self.rotate_backoff * 2)
        return result

    def push_outbox(self) -> int:
        link = self.store.read()
        if not link.linked or not link.hub_url or not link.service_token or self.hub_waits():
            return 0
        self.signals.unauthorized = False
        with self.client_factory() as client:
            with self.factory.begin() as session:
                delivered = push_pending(session, client, link.hub_url, link.service_token, signals=self.signals)
            if self.signals.unauthorized:
                self.signals.unauthorized = False
                check_revoked(self.store, client, self.signals)  # alleen 401 op services/self = ingetrokken
        return delivered

    def poll_keys(self) -> str:
        if time.monotonic() < self.next_key_poll_at or not self.store.read().linked:
            return "niet_nodig"
        if self.hub_waits():
            return "wacht:retry_after"
        self.next_key_poll_at = time.monotonic() + self.settings.key_rotation_poll_seconds
        with self.client_factory() as client:
            return poll_key_rotations(self.store, client)

    def tick(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"version": __version__}
        before = self.last_state
        if not secrets_present(self.settings.secrets_dir):
            detail["secrets"] = "ontbreken"
        else:
            for name, step in (("enrollment", self.enroll), ("confirm", self.confirm_link),
                               ("rotation", self.rotate_if_requested), ("outbox_delivered", self.push_outbox),
                               ("key_rotation", self.poll_keys)):
                try:
                    detail[name] = step()
                except Exception as exc:  # één stap mag de worker nooit stoppen
                    log.warning("%s mislukt: %s", name, type(exc).__name__)
                    detail[f"{name}_error"] = type(exc).__name__
                self.note_hub_wait()  # een Retry-After van deze stap geldt ook voor de volgende stappen
            self.last_state = self._state()
            detail["hub_link"] = self.last_state[0]
            try:
                self._emit_transition(before, self.last_state)
            except Exception as exc:
                log.warning("event koppelstatus mislukt: %s", type(exc).__name__)
        if time.monotonic() - self.last_purge > PURGE_INTERVAL_S:
            detail["idempotency_purged"] = self.idempotency.purge_expired()
            detail["replay_purged"] = self.replay.purge_expired()
            self.last_purge = time.monotonic()
        self.heartbeat(detail)
        return detail

    def run(self) -> None:
        signal.signal(signal.SIGTERM, lambda *_: self.stop.set())
        signal.signal(signal.SIGINT, lambda *_: self.stop.set())
        log.info("worker gestart (interval %s s)", self.settings.worker_interval_seconds)
        while not self.stop.is_set():
            try:
                self.tick()
            except Exception:
                log.exception("worker-ronde mislukt; volgende ronde gaat door")
            self.stop.wait(self.settings.worker_interval_seconds)
        self.engine.dispose()
        log.info("worker gestopt")
