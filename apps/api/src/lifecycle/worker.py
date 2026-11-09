"""Worker (apart proces in de alles-in-één image): hartslag, enrollment met het eenmalige token, outbox naar de Hub
en opruimen van verlopen Idempotency-records. Voert in fase P0 niets uit op de NAS."""

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
from lifecycle.enrollment import EnrollmentError, enroll_and_store, env_enrollment_needed
from lifecycle.hublink import HubLinkService
from lifecycle.idempotency import Idempotency
from lifecycle.models import WorkerHeartbeat
from lifecycle.outbox import push_pending
from lifecycle.secrets_store import SecretsError, Vault, load_identity
from lifecycle.timeutil import utcnow

log = logging.getLogger("lifecycle.worker")


class Worker:
    def __init__(self, settings: Settings, http_client_factory: HttpClientFactory | None = None) -> None:
        self.settings = settings
        self.engine = make_engine(settings.database_url)
        self.factory = make_session_factory(self.engine)
        self.client_factory = http_client_factory or default_http_client_factory(settings)
        self.hub_link = HubLinkService(self.factory, cache_seconds=0)
        self.idempotency = Idempotency(self.factory, settings.idempotency_ttl_hours)
        self.stop = threading.Event()
        self.next_enroll_at = 0.0
        self.enroll_backoff = 5.0
        self.last_purge = 0.0

    def heartbeat(self, detail: dict[str, Any]) -> None:
        with self.factory.begin() as session:
            stmt = insert(WorkerHeartbeat).values(name="worker", beat_at=utcnow(), detail=detail)
            session.execute(
                stmt.on_conflict_do_update(index_elements=["name"], set_={"beat_at": utcnow(), "detail": detail})
            )

    def try_enroll(self, vault: Vault) -> str:
        if not env_enrollment_needed(self.factory, self.settings) or time.monotonic() < self.next_enroll_at:
            return "niet_nodig"
        assert self.settings.hub_url and self.settings.enrollment_token
        try:
            identity = load_identity(self.settings.secrets_dir)
            with self.client_factory() as client:
                enroll_and_store(
                    self.factory,
                    vault,
                    identity,
                    self.settings,
                    client,
                    hub_url=self.settings.hub_url,
                    kind="token",
                    secret=self.settings.enrollment_token,
                )
        except EnrollmentError as exc:
            if exc.retryable:
                self.next_enroll_at = time.monotonic() + self.enroll_backoff
                self.enroll_backoff = min(600.0, self.enroll_backoff * 2)
            return f"mislukt:{exc.code}"
        self.enroll_backoff = 5.0
        return "gekoppeld"

    def push_outbox(self, vault: Vault) -> int:
        snap = self.hub_link.current()
        if snap.state != "gekoppeld" or not snap.hub_url:
            return 0
        token = self.hub_link.service_token(vault)
        if not token:
            return 0
        with self.client_factory() as client, self.factory.begin() as session:
            return push_pending(session, client, snap.hub_url, token)

    def tick(self) -> dict[str, Any]:
        detail: dict[str, Any] = {"version": __version__}
        try:
            vault: Vault | None = Vault.load(self.settings.secrets_dir)
        except SecretsError as exc:
            vault = None
            detail["secrets"] = str(exc)
        if vault is not None:
            detail["enrollment"] = self.try_enroll(vault)
            try:
                detail["outbox_delivered"] = self.push_outbox(vault)
            except Exception as exc:  # outbox-fouten mogen de worker nooit stoppen
                log.warning("outbox mislukt: %s", type(exc).__name__)
                detail["outbox_error"] = type(exc).__name__
        if time.monotonic() - self.last_purge > 3600:
            detail["idempotency_purged"] = self.idempotency.purge_expired()
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
