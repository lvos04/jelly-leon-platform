"""JP10: deze dienst echt koppelen met de Hub (simulator of echte Hub) via de eigen worker, en daarna een door die
Hub gemaakte gebruikers-JWT op de eigen API gebruiken (gepinde sleutel, ``token_use``, ``jti``-replaycache).
Vereist PostgreSQL (``LIFECYCLE_TEST_PG``, zoals de andere integratietests) en een tweede vers enrollment-token."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from starlette.testclient import TestClient

from lifecycle.app import create_app, default_http_client_factory
from lifecycle.config import Settings
from lifecycle.migrations_util import upgrade
from lifecycle.secrets_store import ensure_secrets
from lifecycle.worker import Worker

PG_ADMIN = os.environ.get("LIFECYCLE_TEST_PG", "postgresql+psycopg://postgres@127.0.0.1:5432/postgres")


@pytest.fixture
def service_db() -> Iterator[str]:
    admin = sa.create_engine(PG_ADMIN, isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            name = f"jlp_conf_svc_{uuid.uuid4().hex[:8]}"
            conn.execute(sa.text(f'CREATE DATABASE "{name}"'))
    except sa.exc.OperationalError:
        pytest.skip("PostgreSQL niet bereikbaar (zet LIFECYCLE_TEST_PG)")
    url = f"{PG_ADMIN.rsplit('/', 1)[0]}/{name}"
    upgrade(url)
    yield url
    with admin.connect() as conn:
        conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


def test_worker_links_with_hub_and_hub_jwt_is_accepted(target: Any, service_db: str, tmp_path: Path) -> None:
    if not target.token2:
        pytest.skip("geen tweede enrollment-token (JLP_HUB_ENROLLMENT_TOKEN_2)")
    ensure_secrets(tmp_path / "secrets")
    settings = Settings(data_dir=tmp_path, database_url=service_db, hub_url=target.base_url,
                        enrollment_token=target.token2, hub_key_fingerprint=target.fingerprint,
                        allow_loopback_hub=True)
    worker = Worker(settings, http_client_factory=default_http_client_factory(settings))
    try:
        assert worker.tick()["enrollment"] == "gekoppeld", target.kind
        doc = json.loads((tmp_path / "secrets" / "hub-link.json").read_text())
        assert doc["state"] == "gekoppeld" and doc["confirmed_at"], doc.get("last_error")
        assert doc["pins"]["keyset_fingerprint"] == (target.fingerprint or doc["pins"]["keyset_fingerprint"])
        with TestClient(create_app(settings), base_url="http://lifecycle.test") as c:
            jwt_ = target.mint_user_jwt()
            assert c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {jwt_}"}).status_code == 200
            h = {"Authorization": f"Bearer {jwt_}", "Idempotency-Key": str(uuid.uuid4())}
            body = {"app_id": "uptime-kuma", "kind": "install", "target_version": "1.2.25", "reason": "conformiteit"}
            r = c.post("/lifecycle/v1/runs", json=body, headers=h)
            assert r.status_code == 201, r.text
            r = c.post("/lifecycle/v1/runs", json=body, headers={**h, "Idempotency-Key": str(uuid.uuid4())})
            assert r.status_code == 401 and r.json()["details"]["reason"] == "token_replayed"
            own = doc["service_token"]
            r = c.get("/connector/v1/hub-link", headers={"Authorization": f"Bearer {own}"})
            assert r.status_code == 401 and r.json()["details"]["reason"] == "service_token_not_accepted"
        # outbox naar de inbox van deze Hub (service-token), daarna niets meer open
        worker.tick()
        with worker.engine.connect() as conn:
            open_events = conn.execute(sa.text("SELECT count(*) FROM outbox_events WHERE delivered_at IS NULL "
                                               "AND dead_letter = false")).scalar_one()
        assert open_events == 0, target.kind
    finally:
        worker.engine.dispose()
