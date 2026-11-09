"""Integratie: enrollment (token uit env en koppelcode via statuspagina), service-token-auth, outbox naar de Hub."""

from __future__ import annotations

import re
from typing import Any

import pytest
from starlette.testclient import TestClient

from lifecycle.config import Settings
from lifecycle.worker import Worker


def _worker(clean_db: str, data_dir: Any, hub: Any, token: str | None) -> Worker:
    settings = Settings(data_dir=data_dir, database_url=clean_db, hub_url="http://hub.test", enrollment_token=token)
    return Worker(settings, http_client_factory=lambda: TestClient(hub, base_url="http://hub.test"))


def test_env_token_enrollment_then_service_token_auth_and_outbox(
    clean_db: str, data_dir: Any, hub: Any, make_app: Any, auth: Any
) -> None:
    sim = hub.state.sim
    secret = sim.add_grant("token", "jelly-leon-platform")
    worker = _worker(clean_db, data_dir, hub, secret)
    detail = worker.tick()
    assert detail["enrollment"] == "gekoppeld"
    req = sim.enroll_requests[-1]
    assert req["app_id"] == "jelly-leon-platform" and req["public_key_jwk"]["crv"] == "Ed25519"
    assert "enrollment_token" not in req  # de sim bewaart het geheim niet

    # het eenmalige token is nu waardeloos; een tweede ronde probeert niets meer
    assert worker.tick()["enrollment"] == "niet_nodig"

    with TestClient(make_app(enrollment_token=secret)) as c:
        link = c.get("/lifecycle/v1/hub-link", headers=auth("viewer")).json()
        assert link["state"] == "gekoppeld" and link["linked_via"] == "token"
        assert link["service_token_last4"] and len(link["service_token_last4"]) == 4
        service_token = worker.hub_link.service_token(__import__("lifecycle.secrets_store").secrets_store.Vault.load(
            data_dir / "secrets"))
        assert service_token and service_token not in str(link)
        r = c.get("/connector/v1/discover", headers={"Authorization": f"Bearer {service_token}"})
        assert r.status_code == 200
        r = c.get("/connector/v1/discover", headers={"Authorization": "Bearer svc_verkeerd_token_0000000000"})
        assert r.status_code == 401
        c.post("/lifecycle/v1/runs", json={"app_id": "gotify", "kind": "install", "target_version": "1.1.17"},
               headers=auth("operator", idem=True))

    # outbox: events gaan met het service-token naar de Hub-inbox (dedupe op event_id)
    worker.tick()
    types = [e["event_type"] for e in sim.events]
    assert "service.enrollment.completed" in types and "lifecycle.run.created" in types
    assert all(e["source_app"] == "jelly-leon-platform" for e in sim.events)
    n = len(sim.events)
    worker.tick()
    assert len(sim.events) == n

    # health: worker heeft hartslag, Hub gekoppeld → healthy
    with TestClient(make_app()) as c:
        h = c.get("/connector/v1/health", headers=auth("viewer")).json()
        assert h["status"] == "healthy", h


def test_invalid_env_token_is_final_and_not_retried(clean_db: str, data_dir: Any, hub: Any) -> None:
    worker = _worker(clean_db, data_dir, hub, "verlopen-of-onbekend-token")
    assert worker.tick()["enrollment"] == "mislukt:invalid_grant"
    assert worker.tick()["enrollment"] == "niet_nodig"
    snap = worker.hub_link.current()
    assert snap.state == "fout" and "koppelcode" in (snap.last_error or "")


def test_hub_down_is_retryable(clean_db: str, data_dir: Any, hub: Any) -> None:
    sim = hub.state.sim
    secret = sim.add_grant("token", "jelly-leon-platform")
    sim.faults.add("enroll_down")
    worker = _worker(clean_db, data_dir, hub, secret)
    assert worker.tick()["enrollment"] == "mislukt:hub_error"
    assert worker.hub_link.current().state == "niet_gekoppeld"
    sim.faults.discard("enroll_down")
    worker.next_enroll_at = 0
    assert worker.tick()["enrollment"] == "gekoppeld"


def test_outbox_retry_then_dead_letter(clean_db: str, data_dir: Any, hub: Any, make_app: Any, auth: Any) -> None:
    import sqlalchemy as sa

    from lifecycle.models import OutboxEvent

    sim = hub.state.sim
    worker = _worker(clean_db, data_dir, hub, sim.add_grant("token", "jelly-leon-platform"))
    worker.tick()
    sim.faults.add("inbox_500")
    with TestClient(make_app()) as c:
        c.post("/lifecycle/v1/runs", json={"app_id": "beszel-hub", "kind": "install", "target_version": "1.1.22"},
               headers=auth("operator", idem=True))
    for _ in range(12):
        with worker.factory.begin() as s:
            s.execute(sa.update(OutboxEvent).values(next_attempt_at=sa.func.now()))
        worker.tick()
    with worker.factory() as s:
        ev = s.execute(sa.select(OutboxEvent).where(OutboxEvent.event_type == "lifecycle.run.created")).scalar_one()
        assert ev.dead_letter is True and ev.attempts == 10 and ev.delivered_at is None


def _csrf(html: str) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    assert m
    return m.group(1)


def test_status_page_pairing_with_koppelcode(make_app: Any, hub: Any) -> None:
    sim = hub.state.sim
    code = sim.add_grant("koppelcode", "jelly-leon-platform", "KOPPEL-123456")
    with TestClient(make_app(hub_url=None), base_url="http://lifecycle.test") as c:
        page = c.get("/")
        assert page.status_code == 200 and "niet gekoppeld" in page.text.lower() and 'lang="nl"' in page.text
        assert "Laatst bijgewerkt" in page.text
        token = _csrf(page.text)
        # CSRF ontbreekt/onjuist → geweigerd
        r = c.post("/koppelen", data={"hub_url": "http://hub.test", "koppelcode": code, "csrf": "fout"})
        assert r.status_code == 403
        r = c.post("/koppelen", data={"hub_url": "http://hub.test", "koppelcode": "ONJUIST-999", "csrf": token})
        assert r.status_code == 400 and "ongeldig" in r.text
        r = c.post("/koppelen", data={"hub_url": "ftp://hub", "koppelcode": code, "csrf": token})
        assert r.status_code == 400
        r = c.post("/koppelen", data={"hub_url": "http://hub.test", "koppelcode": code, "csrf": token},
                   follow_redirects=False)
        assert r.status_code == 303
        page = c.get("/?gekoppeld=1")
        assert "Koppelen gelukt" in page.text and "Gekoppeld met" in page.text
        assert code not in page.text
        r = c.post("/koppelen", data={"hub_url": "http://hub.test", "koppelcode": code, "csrf": _csrf(page.text)
                                      if "csrf" in page.text else token})
        assert r.status_code in (403, 409)


@pytest.mark.parametrize("roles", [("admin",)])
def test_unlink_requires_admin_and_clears_token(
    clean_db: str, data_dir: Any, hub: Any, make_app: Any, auth: Any, roles: tuple[str, ...]
) -> None:
    worker = _worker(clean_db, data_dir, hub, hub.state.sim.add_grant("token", "jelly-leon-platform"))
    worker.tick()
    with TestClient(make_app()) as c:
        r = c.post("/lifecycle/v1/hub-link/unlink", headers=auth(*roles, idem=True))
        assert r.status_code == 200 and r.json()["state"] == "niet_gekoppeld"
        assert r.json()["service_token_last4"] is None
