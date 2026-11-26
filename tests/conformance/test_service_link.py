"""JP10: deze dienst echt koppelen met de Hub (simulator of echte Hub) via de eigen worker, en daarna een door die
Hub gemaakte gebruikers-JWT op de eigen API gebruiken (gepinde sleutel, ``token_use``, ``jti``-replaycache).
Vereist PostgreSQL (``LIFECYCLE_TEST_PG``, zoals de andere integratietests) en een tweede vers enrollment-token.

PYTEST_DONT_REWRITE houdt de koppelgegevens en autorisatieheaders uit de foutuitvoer.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from starlette.testclient import TestClient

from lifecycle.app import create_app, default_http_client_factory
from lifecycle.config import Settings
from lifecycle.secrets_store import ensure_secrets
from lifecycle.worker import Worker


def test_worker_links_with_hub_and_hub_jwt_is_accepted(target: Any, service_db: str, tmp_path: Path) -> None:
    if not target.token2:
        pytest.skip("geen tweede enrollment-token (JLP_HUB_ENROLLMENT_TOKEN_2)")
    ensure_secrets(tmp_path / "secrets")
    settings = Settings(data_dir=tmp_path, database_url=service_db, hub_url=target.base_url,
                        enrollment_token=target.token2, hub_key_fingerprint=target.fingerprint,
                        allow_loopback_hub=True)
    worker = Worker(settings, http_client_factory=default_http_client_factory(settings))
    try:
        assert worker.tick()["enrollment"] == "gekoppeld", "De worker heeft de dienst niet gekoppeld"
        doc = json.loads((tmp_path / "secrets" / "hub-link.json").read_text())
        assert doc["state"] == "gekoppeld" and doc["confirmed_at"], "Bevestiging van de koppeling ontbreekt"
        assert doc["pins"]["keyset_fingerprint"] == (target.fingerprint or doc["pins"]["keyset_fingerprint"]), (
            "Opgeslagen sleutelvingerafdruk wijkt af")
        with TestClient(create_app(settings), base_url="http://lifecycle.test") as c:
            jwt_ = target.mint_user_jwt()
            r = c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {jwt_}"})
            assert r.status_code == 200, "Gebruikers-JWT geweigerd bij het lezen van runs"
            h = {"Authorization": f"Bearer {jwt_}", "Idempotency-Key": str(uuid.uuid4())}
            body = {"app_id": "uptime-kuma", "kind": "install", "target_version": "1.2.25", "reason": "conformiteit"}
            r = c.post("/lifecycle/v1/runs", json=body, headers=h)
            assert r.status_code == 201, "Run aanmaken met het gebruikers-JWT mislukt"
            r = c.post("/lifecycle/v1/runs", json=body, headers={**h, "Idempotency-Key": str(uuid.uuid4())})
            assert r.status_code == 401 and r.json()["details"]["reason"] == "token_replayed", "JWT-herhaling aanvaard"
            own = doc["service_token"]
            r = c.get("/connector/v1/hub-link", headers={"Authorization": f"Bearer {own}"})
            assert r.status_code == 401 and r.json()["details"]["reason"] == "service_token_not_accepted", (
                "Servicetoken op de gebruikersroute niet juist geweigerd")
        # outbox naar de inbox van deze Hub (service-token), daarna niets meer open
        worker.tick()
        with worker.engine.connect() as conn:
            open_events = conn.execute(sa.text("SELECT count(*) FROM outbox_events WHERE delivered_at IS NULL "
                                               "AND dead_letter = false")).scalar_one()
        assert open_events == 0, "Er staan nog onbezorgde berichten in de outbox"
    finally:
        worker.engine.dispose()
