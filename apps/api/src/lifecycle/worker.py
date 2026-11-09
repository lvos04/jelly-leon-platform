"""Worker (apart proces in de alles-in-één image). Voert in fase P0 niets uit op de NAS.

Per ronde: hartslag; koppelen met het eenmalige env-token (backoff, 60-minutengrens, herstel na herstart);
de koppeling bevestigen met ``GET /api/v1/services/self``; het service-token roteren als de Hub
``Hub-Token-Rotation: required`` meldt; de outbox naar de Hub (een 401 → controle of de koppeling is ingetrokken);
ondertekende sleutelrotaties ophalen (≤ elke 15 min); verlopen Idempotency- en replay-records opruimen.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from typing import Any

from sqlalchemy.dialects.postgresql import insert

from lifecycle import __version__
from lifecycle.app import HttpClientFactory, default_http_client_factory
from lifecycle.config import Settings
from lifecycle.db import make_engine, make_session_factory
from lifecycle.enrollment import (
    EnvEnrollment,
    HubSignals,
    check_revoked,
    confirm,
    rotate_service_token,
)
from lifecycle.hublink import HubLinkStore
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
        self.last_purge = 0.0
        self.recovered = False
        self.last_state: tuple[str, str | None] = self._state()

    def _state(self) -> tuple[str, str | None]:
        link = self.store.read()
        return ("beschadigd" if link.corrupt else link.state), link.doc.get("linked_at")

    def heartbeat(self, detail: dict[str, Any]) -> None:
        with self.factory.begin() as session:
            stmt = insert(WorkerHeartbeat).values(name="worker", beat_at=utcnow(), detail=detail)
            session.execute(
                stmt.on_conflict_do_update(index_elements=["name"], set_={"beat_at": utcnow(), "detail": detail})
            )

    def _emit_transition(self, before: tuple[str, str | None], after: tuple[str, str | None]) -> None:
        """Eén event per overgang van de koppelstatus (drie segmenten, contract §7); geen geheimen. Een nieuwe
        koppeling (ook opnieuw koppelen: ander ``linked_at``) geeft ``service.enrollment.completed``."""
        if before == after:
            return
        link = self.store.read()
        types = {"gekoppeld": "service.enrollment.completed", "ingetrokken": "service.hub_link.revoked",
                 "ontkoppeld": "service.hub_link.unlinked"}
        if after[0] not in types or (after[0] == before[0] and after[0] != "gekoppeld"):
            return
        with self.factory.begin() as session:
            emit(session, types[after[0]], "service", link.service_id or "onbekend",
                 {"state": after[0], "via": link.doc.get("via"), "keyset_fingerprint": link.keyset_fingerprint},
                 actor={"type": "system", "id": "jelly-leon-platform"})

    def enroll(self) -> str:
        if not self.recovered:
            self.env.recover_after_restart()
            self.recovered = True
        try:
            return self.env.tick(self.client_factory, lambda: load_identity(self.settings.secrets_dir))
        except SecretsError as exc:
            return f"geheimen:{exc}"

    def confirm_link(self) -> str:
        link = self.store.read()
        if not link.linked or link.doc.get("confirmed_at") or time.monotonic() < self.next_confirm_at:
            return "niet_nodig"
        with self.client_factory() as client:
            result = confirm(self.store, client, self.signals)
        if result != "bevestigd":
            self.next_confirm_at = time.monotonic() + CONFIRM_RETRY_S
        return result

    def rotate_if_requested(self) -> str:
        if not self.signals.rotation_required or not self.store.read().linked:
            return "niet_nodig"
        with self.client_factory() as client:
            return rotate_service_token(self.store, client, self.signals)

    def push_outbox(self) -> int:
        link = self.store.read()
        if not link.linked or not link.hub_url or not link.service_token:
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
