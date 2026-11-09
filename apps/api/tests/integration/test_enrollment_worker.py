"""Integratie (echte PostgreSQL + Hub-simulator volgens 1.2): koppelen met het env-token in de worker, herhaling na
een herstart halverwege, definitieve en tijdelijke fouten, bevestigen met ``services/self``, intrekking, rotatie van
het service-token, opnieuw koppelen en de outbox."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.testclient import TestClient

from lifecycle.config import Settings
from lifecycle.enrollment import enroll_once, sha256_hex
from lifecycle.hublink import HubLinkCorrupt, HubLinkStore
from lifecycle.keys import public_jwk
from lifecycle.models import OutboxEvent
from lifecycle.secrets_store import load_identity
from lifecycle.timeutil import iso_z, utcnow
from lifecycle.worker import Worker

APP = "jelly-leon-platform"


def make_worker(clean_db: str, data_dir: Path, hub: Any, token: str | None, **kw: Any) -> Worker:
    settings = Settings(data_dir=data_dir, database_url=clean_db, hub_url="http://hub.test", enrollment_token=token,
                        **kw)
    return Worker(settings, http_client_factory=lambda: TestClient(hub, base_url="http://hub.test",
                                                                   follow_redirects=False))


def file_doc(data_dir: Path) -> dict[str, Any]:
    return dict(json.loads((data_dir / "secrets" / "hub-link.json").read_text()))


def test_env_token_enrollment_pins_confirms_and_never_reuses_the_token(
    clean_db: str, data_dir: Path, hub: Any, make_app: Any, auth: Any
) -> None:
    sim = hub.state.sim
    secret = sim.add_grant("token", APP)
    worker = make_worker(clean_db, data_dir, hub, secret, hub_key_fingerprint=sim.keyset_fingerprint())
    assert worker.tick()["enrollment"] == "gekoppeld"
    req = sim.enroll_requests[-1]
    assert set(req) == {"app_id", "version", "base_url", "public_key_jwk"}  # + het geheim (niet bewaard door de sim)
    doc = file_doc(data_dir)
    assert doc["state"] == "gekoppeld" and doc["via"] == "token" and doc["confirmed_at"]  # services/self gedaan
    assert doc["pins"]["keyset_fingerprint"] == sim.keyset_fingerprint() and doc["pending"] is None
    assert doc["used_enrollment_token_sha256"] == [sha256_hex(secret)] and doc["first_linked_at"]
    assert sim.calls["self"] >= 1
    stat = (data_dir / "secrets" / "hub-link.json").stat()
    assert oct(stat.st_mode & 0o777) == "0o600"
    # eenmalig token: een tweede ronde probeert niets meer
    assert worker.tick()["enrollment"] == "niet_nodig" and sim.calls["enroll"] == 1
    # de database bevat geen koppeling (database-herstel brengt nooit een token terug)
    with worker.engine.connect() as conn:
        assert "hub_link" not in sa.inspect(conn).get_table_names()

    with TestClient(make_app()) as c:
        link = c.get("/connector/v1/hub-link", headers=auth("viewer")).json()
        assert link["state"] == "gekoppeld" and link["via"] == "token" and doc["service_token"] not in str(link)
        assert link["pinned_kids"]["jwt"] == [sim.kid]
        c.post("/lifecycle/v1/runs", json={"app_id": "gotify", "kind": "install", "target_version": "1.1.17"},
               headers=auth("operator", idem=True))
    # outbox: events met het service-token naar de Hub-inbox (dedupe op event_id)
    worker.tick()
    types = [e["event_type"] for e in sim.events]
    assert "service.enrollment.completed" in types and "lifecycle.run.created" in types
    assert all(len(t.split(".")) == 3 for t in types)  # §7: precies drie segmenten
    n = len(sim.events)
    worker.tick()
    assert len(sim.events) == n


def test_restart_halfway_replays_with_same_token(clean_db: str, data_dir: Path, hub: Any) -> None:
    """§3.6: de dienst crasht na het antwoord maar vóór het opslaan; dezelfde poging na de herstart is een
    toegestane herhaling (zelfde grant, zelfde identiteitssleutel, uitgegeven token nooit gebruikt)."""
    sim = hub.state.sim
    secret = sim.add_grant("token", APP)
    store = HubLinkStore(data_dir / "secrets")
    body = {"app_id": APP, "enrollment_token": secret, "version": "0.1.0", "base_url": None,
            "public_key_jwk": public_jwk(load_identity(data_dir / "secrets"))}
    with TestClient(hub, base_url="http://hub.test") as client:
        r = client.post("/api/v1/services/enroll", json=body)
        assert r.status_code == 200 and r.json()["enrollment_replayed"] is False
    # 'crash': het antwoord is nooit opgeslagen; wel stond de poging als pending vast
    store.update(lambda d: d.update(pending={"kind": "token", "secret_sha256": sha256_hex(secret),
                                             "hub_url": "http://hub.test", "started_at": iso_z(utcnow()),
                                             "attempts": 1}))
    worker = make_worker(clean_db, data_dir, hub, secret)
    assert worker.tick()["enrollment"] == "gekoppeld"
    assert file_doc(data_dir)["state"] == "gekoppeld"
    # nu het token gebruikt is (services/self), is de herhaling dicht; een andere identiteitssleutel mag nooit
    with TestClient(hub, base_url="http://hub.test") as client:
        r = client.post("/api/v1/services/enroll", json=body)
        assert r.status_code == 401 and r.json()["code"] == "invalid_enrollment"


def test_restart_with_other_token_marks_interrupted(clean_db: str, data_dir: Path, hub: Any) -> None:
    store = HubLinkStore(data_dir / "secrets")
    store.update(lambda d: d.update(pending={"kind": "token", "secret_sha256": "b" * 64, "hub_url": "http://hub.test",
                                             "started_at": iso_z(utcnow()), "attempts": 2}))
    worker = make_worker(clean_db, data_dir, hub, None)
    worker.tick()
    doc = file_doc(data_dir)
    assert doc["pending"] is None and doc["last_error"]["code"] == "koppelen_onderbroken"
    assert doc["state"] == "nooit_gekoppeld"


@pytest.mark.parametrize(
    ("setup", "code"),
    [
        (lambda sim: "enr_" + "x" * 43, "hub_weigerde"),  # onbekend: 401 invalid_enrollment
        (lambda sim: sim.add_grant("token", "home-control-hub"), "hub_weigerde"),  # token van een andere app
        (lambda sim: sim.add_grant("token", APP, ttl_s=-1), "hub_weigerde"),  # verlopen
        (lambda sim: (sim.faults.add("enroll_404"), sim.add_grant("token", APP))[1], "enroll_endpoint_ontbreekt"),
        (lambda sim: (sim.faults.add("bad_fingerprint"), sim.add_grant("token", APP))[1], "hub_vingerafdruk_onjuist"),
        (lambda sim: (sim.faults.add("bad_response"), sim.add_grant("token", APP))[1], "hub_antwoord_ongeldig"),
        (lambda sim: (sim.faults.add("alias_only"), sim.add_grant("token", APP))[1], "hub_antwoord_ongeldig"),
        (lambda sim: "verkeerd-formaat", "token_formaat_ongeldig"),
    ],
)
def test_final_failures_store_nothing_and_are_never_retried(
    clean_db: str, data_dir: Path, hub: Any, setup: Callable[[Any], str], code: str
) -> None:
    sim = hub.state.sim
    secret = setup(sim)
    worker = make_worker(clean_db, data_dir, hub, secret)
    assert worker.tick()["enrollment"] == f"mislukt:{code}"
    doc = file_doc(data_dir)
    assert doc["state"] == "nooit_gekoppeld" and doc["service_token"] is None and doc["pins"] is None
    assert doc["pending"] is None and doc["last_error"]["code"] == code  # ook het Hub-adres is weg
    assert sha256_hex(secret) in doc["used_enrollment_token_sha256"]
    calls = sim.calls["enroll"]
    assert worker.tick()["enrollment"] == "niet_nodig" and sim.calls["enroll"] == calls  # eenmalig: nooit opnieuw


def test_env_fingerprint_mismatch_stores_nothing(clean_db: str, data_dir: Path, hub: Any) -> None:
    sim = hub.state.sim
    worker = make_worker(clean_db, data_dir, hub, sim.add_grant("token", APP),
                         hub_key_fingerprint="MEGG-8GFG-2E4Y-K2RQ")
    assert worker.tick()["enrollment"] == "mislukt:hub_vingerafdruk_onjuist"
    assert file_doc(data_dir)["state"] == "nooit_gekoppeld"


def test_hub_down_or_redirect_is_retried_with_backoff(clean_db: str, data_dir: Path, hub: Any) -> None:
    sim = hub.state.sim
    secret = sim.add_grant("token", APP)
    sim.faults.add("enroll_redirect")
    worker = make_worker(clean_db, data_dir, hub, secret)
    assert worker.tick()["enrollment"] == "opnieuw:hub_onbereikbaar"  # 3xx nooit gevolgd = storing
    doc = file_doc(data_dir)
    assert doc["pending"]["attempts"] == 1 and doc["last_error"]["code"] == "hub_onbereikbaar"
    assert worker.tick()["enrollment"] == "wachten"  # backoff: start 5 s (+ jitter)
    assert 4.0 <= worker.env.next_at - time.monotonic() <= 6.25
    sim.faults.discard("enroll_redirect")
    sim.faults.add("enroll_down")
    worker.env.next_at = 0
    assert worker.tick()["enrollment"] == "opnieuw:hub_onbereikbaar"
    assert worker.env.backoff == 20.0  # 5 → 10 → 20 (max. 15 min)
    sim.faults.discard("enroll_down")
    worker.env.next_at = 0
    assert worker.tick()["enrollment"] == "gekoppeld"
    assert file_doc(data_dir)["pending"] is None and file_doc(data_dir)["last_error"] is None


def test_rate_limit_respects_retry_after(clean_db: str, data_dir: Path, hub: Any) -> None:
    sim = hub.state.sim
    with TestClient(hub, base_url="http://hub.test") as client:
        body = {"app_id": APP, "version": "0.1.0", "base_url": None,
                "public_key_jwk": public_jwk(Ed25519PrivateKey.generate())}
        for _ in range(10):
            assert client.post("/api/v1/services/enroll",
                               json={**body, "enrollment_token": "enr_" + "y" * 43}).status_code == 401
    worker = make_worker(clean_db, data_dir, hub, sim.add_grant("token", APP))
    assert worker.tick()["enrollment"] == "opnieuw:rate_limited"
    wait = worker.env.next_at - time.monotonic()
    assert 800 < wait <= 900  # Retry-After van de Hub (≈ 15 min), niet eerder
    assert file_doc(data_dir)["last_error"]["code"] == "rate_limited"


def test_stops_60_minutes_after_first_attempt(clean_db: str, data_dir: Path, hub: Any) -> None:
    sim = hub.state.sim
    secret = sim.add_grant("token", APP)
    HubLinkStore(data_dir / "secrets").update(lambda d: d.update(pending={
        "kind": "token", "secret_sha256": sha256_hex(secret), "hub_url": "http://hub.test",
        "started_at": iso_z(utcnow() - timedelta(minutes=61)), "attempts": 9}))
    worker = make_worker(clean_db, data_dir, hub, secret)
    assert worker.tick()["enrollment"] == "mislukt:token_verlopen_na_60_min"
    assert file_doc(data_dir)["last_error"]["code"] == "token_verlopen_na_60_min"
    assert sim.calls["enroll"] == 0


def test_stops_60_minutes_after_first_attempt_also_with_unreadable_hub_link(
    clean_db: str, data_dir: Path, hub: Any
) -> None:
    """Review: met een onleesbaar hub-link.json kan ``pending`` niet worden vastgelegd; de 60-minutengrens gold dan
    nooit (eindeloos opnieuw proberen). Nu telt de eerste poging in het geheugen."""
    sim = hub.state.sim
    store = HubLinkStore(data_dir / "secrets")
    store.path.write_text("kapot")
    sim.faults.add("enroll_down")
    worker = make_worker(clean_db, data_dir, hub, sim.add_grant("token", APP))
    assert worker.tick()["enrollment"] == "opnieuw:hub_onbereikbaar"
    sha = worker.env.token_sha
    assert sha is not None and sha in worker.env.first_attempt
    worker.env.first_attempt[sha] -= timedelta(minutes=61)
    worker.env.next_at = 0
    calls = sim.calls["enroll"]
    assert worker.tick()["enrollment"] == "mislukt:token_verlopen_na_60_min"
    assert worker.tick()["enrollment"] == "niet_nodig" and sim.calls["enroll"] == calls
    assert store.read().corrupt  # niets vervangen zonder geslaagde koppeling


def test_relink_with_new_env_token_replaces_and_failed_relink_keeps_link(
    clean_db: str, data_dir: Path, hub: Any
) -> None:
    sim = hub.state.sim
    first = make_worker(clean_db, data_dir, hub, sim.add_grant("token", APP))
    first.tick()
    before = file_doc(data_dir)
    # mislukte nieuwe poging (verlopen token): bestaande koppeling blijft ongewijzigd
    bad = make_worker(clean_db, data_dir, hub, sim.add_grant("token", APP, ttl_s=-1))
    assert bad.tick()["enrollment"] == "mislukt:hub_weigerde"
    after_bad = file_doc(data_dir)
    assert after_bad["state"] == "gekoppeld" and after_bad["service_token"] == before["service_token"]
    assert after_bad["pins"] == before["pins"]
    # geslaagde nieuwe poging (Hub roteerde zijn sleutels tussendoor): token en pins vervangen, oude naar previous_pins
    sim.jwt_keys.append(("hub-sim-2", Ed25519PrivateKey.generate()))
    again = make_worker(clean_db, data_dir, hub, sim.add_grant("token", APP))
    assert again.tick()["enrollment"] == "gekoppeld"
    doc = file_doc(data_dir)
    assert doc["service_token"] != before["service_token"] and doc["previous_pins"] == before["pins"]
    assert doc["service_id"] == before["service_id"]  # zelfde registratie bij de Hub
    assert doc["first_linked_at"] == before["first_linked_at"] and doc["linked_at"] != before["linked_at"]
    assert len(doc["used_enrollment_token_sha256"]) == 3


def test_revocation_only_after_services_self_401(clean_db: str, data_dir: Path, hub: Any, make_app: Any,
                                                 auth: Any) -> None:
    sim = hub.state.sim
    worker = make_worker(clean_db, data_dir, hub, sim.add_grant("token", APP))
    worker.tick()
    with TestClient(make_app()) as c:
        c.post("/lifecycle/v1/runs", json={"app_id": "gotify", "kind": "install", "target_version": "1.1.17"},
               headers=auth("operator", idem=True))
        # 5xx/netwerk is nooit een intrekking
        sim.faults.add("inbox_500")
        worker.tick()
        assert file_doc(data_dir)["state"] == "gekoppeld"
        sim.faults.discard("inbox_500")
        sim.revoke(APP)
        c.post("/lifecycle/v1/runs", json={"app_id": "gotify", "kind": "update", "target_version": "1.1.18"},
               headers=auth("operator", idem=True))  # nieuw event, direct aan de beurt
        worker.tick()  # inbox 401 → services/self 401 → ingetrokken
        doc = file_doc(data_dir)
        assert doc["state"] == "ingetrokken" and doc["last_error"]["code"] == "ingetrokken_door_hub"
        assert doc["service_token"] is None and doc["pins"] is None and doc["previous_pins"]
        r = c.get("/lifecycle/v1/runs", headers=auth("admin"))
        assert r.status_code == 401 and r.json()["details"]["reason"] == "revoked"
    with worker.factory() as session:
        types = set(session.execute(sa.select(OutboxEvent.event_type)).scalars())
    assert "service.hub_link.revoked" in types  # lokaal vastgelegd (aflevering kan pas na opnieuw koppelen)


def test_unauthorized_from_outbox_but_self_ok_is_not_revocation(clean_db: str, data_dir: Path, hub: Any) -> None:
    sim = hub.state.sim
    worker = make_worker(clean_db, data_dir, hub, sim.add_grant("token", APP))
    worker.tick()
    sim.faults.add("self_down")  # services/self 503: niet intrekken
    sim.revoke(APP)
    worker.tick()
    assert file_doc(data_dir)["state"] == "gekoppeld"


def test_rotation_requested_by_hub(clean_db: str, data_dir: Path, hub: Any) -> None:
    sim = hub.state.sim
    worker = make_worker(clean_db, data_dir, hub, sim.add_grant("token", APP))
    worker.tick()
    old = file_doc(data_dir)["service_token"]
    sim.request_rotation(APP)
    worker.tick()  # outbox/self ziet Hub-Token-Rotation: required
    worker.tick()  # roteert, bevestigt met services/self
    doc = file_doc(data_dir)
    assert doc["service_token"] != old and doc["service_token_last4"] == doc["service_token"][-4:]
    assert sim.calls["rotate"] == 1 and doc["state"] == "gekoppeld"
    # het oude token blijft nog 10 min geldig na het eerste gebruik van het nieuwe (geen buitensluiting)
    with TestClient(hub, base_url="http://hub.test") as client:
        assert client.get("/api/v1/services/self", headers={"Authorization": f"Bearer {old}"}).status_code == 200
        sim.clock = lambda: __import__("time").time() + 601
        assert client.get("/api/v1/services/self", headers={"Authorization": f"Bearer {old}"}).status_code == 401


def test_interrupted_rotation_is_asked_again(clean_db: str, data_dir: Path, hub: Any) -> None:
    """Crash tijdens de rotatie: het nieuwe token is nooit opgeslagen. Het oude token werkt nog, maar de Hub zet
    ``Hub-Token-Rotation: required`` zolang het nieuwste nooit gebruikt is; de dienst roteert opnieuw."""
    sim = hub.state.sim
    worker = make_worker(clean_db, data_dir, hub, sim.add_grant("token", APP))
    worker.tick()
    old = file_doc(data_dir)["service_token"]
    with TestClient(hub, base_url="http://hub.test") as client:
        assert client.post("/api/v1/services/self/rotate", headers={"Authorization": f"Bearer {old}"}).status_code \
            == 200  # antwoord 'verloren'
    worker.next_confirm_at = 0
    HubLinkStore(data_dir / "secrets").update(lambda d: d.update(confirmed_at=None))
    worker.tick()  # bevestigen met het oude token → header required
    worker.tick()
    assert file_doc(data_dir)["service_token"] != old and sim.calls["rotate"] == 2


def test_only_env_token_may_replace_a_corrupt_file(clean_db: str, data_dir: Path, hub: Any) -> None:
    sim = hub.state.sim
    store = HubLinkStore(data_dir / "secrets")
    store.path.write_text("kapot")
    with TestClient(hub, base_url="http://hub.test") as client, pytest.raises(HubLinkCorrupt):
        enroll_once(store, client, load_identity(data_dir / "secrets"), Settings(data_dir=data_dir),
                    kind="koppelcode", secret=sim.add_grant("koppelcode", APP), hub_url="http://hub.test",
                    expected_fingerprint=None)
    assert store.read().corrupt and sim.calls["enroll"] == 0  # geweigerd vóór de Hub: de code blijft geldig
    worker = make_worker(clean_db, data_dir, hub, sim.add_grant("token", APP))
    assert worker.tick()["enrollment"] == "gekoppeld" and store.read().linked
    assert [p for p in (data_dir / "secrets").iterdir() if p.name.startswith("hub-link.json.beschadigd-")]
