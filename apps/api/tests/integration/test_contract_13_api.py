"""Contract 1.3 tegen de echte app (PostgreSQL + Hub-simulator): JWT-regels en controlevolgorde (C1.3-JLP-04),
foutcatalogus en ``Idempotency-Key`` dynamisch (C1.3-JLP-12, §5), connector-health en versheid (C1.3-JLP-08) en de
witte lijst van de koppelstatus in ``/readyz`` (C1.3-JLP-03)."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

AUD = "jelly-leon-platform"
MUTATING = {"post", "put", "patch", "delete"}
EXEMPT_501 = {"/connector/v1/propose", "/connector/v1/apply", "/connector/v1/rollback"}


def _mutating_paths(client: Any) -> list[tuple[str, str]]:
    out = []
    for template, operations in client.app.openapi()["paths"].items():
        path = template.replace("{run_id}", str(uuid.uuid4())).replace("{evidence_id}", str(uuid.uuid4()))
        out += [(m.upper(), path) for m in operations if m in MUTATING]
    assert len(out) >= 10
    return out


def test_unreadable_body_without_valid_auth_is_always_401(client: Any, hub: Any) -> None:
    """§4.7 (RV9): authenticatie vóór de body; zonder geldige aanmelding altijd 401, ook bij een onleesbare body of een
    ontbrekende ``Idempotency-Key`` (FastAPI ontleedt JSON vóór de afhankelijkheden: zonder de regel gaf dit 422)."""
    bad_aud = hub.state.sim.mint("u1", ["admin"], AUD, extra={"aud": [AUD]})
    for method, path in _mutating_paths(client):
        for headers in ({}, {"Authorization": "Bearer kapot"}, {"Authorization": f"Bearer {bad_aud}"}):
            r = client.request(method, path, content=b"{kapot", headers={"Content-Type": "application/json",
                                                                        **headers})
            assert r.status_code == 401 and r.json()["code"] == "unauthorized", (method, path, headers, r.text)
            assert r.json()["details"]["reason"] == "invalid_token"


def test_unreadable_body_with_valid_auth_is_422_with_fouten_and_consumes_the_jti(client: Any, hub: Any) -> None:
    """Met geldige aanmelding: 422 ``validation_error`` met ``details.fouten[]``; de POST verbruikte de ``jti`` (§4.6),
    dus hetzelfde token werkt daarna voor geen enkele mutatie meer."""
    token = hub.state.sim.mint("u1", ["admin"], AUD)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
               "Idempotency-Key": str(uuid.uuid4())}
    r = client.post("/lifecycle/v1/runs", content=b"{kapot", headers=headers)
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    assert r.json()["details"]["fouten"] and {"veld", "fout", "type"} <= set(r.json()["details"]["fouten"][0])
    again = client.post("/lifecycle/v1/runs", json={"app_id": "gotify", "kind": "install"},
                        headers={**headers, "Idempotency-Key": str(uuid.uuid4())})
    assert again.status_code == 401 and again.json()["details"]["reason"] == "token_replayed"


def test_every_mutation_without_key_gets_400_after_authentication(client: Any, auth: Any) -> None:
    """§5 dynamisch: zonder ``Idempotency-Key`` → 400 ``idempotency_key_required`` (pas ná de authenticatie); alleen de
    501-routes van de connector zijn uitgezonderd (zolang ze niets uitvoeren). Met een geldige body, zodat de
    schemacontrole (ook ná de authenticatie) niet eerst antwoordt."""
    bodies = {"/lifecycle/v1/runs": {"app_id": "gotify", "kind": "install"}}
    for method, path in _mutating_paths(client):
        body = bodies.get(path, {"to_state": "aborted", "reason": "test"} if path.endswith("/transitions") else {})
        r = client.request(method, path, json=body, headers=auth("admin"))
        if path in EXEMPT_501:
            assert r.status_code == 501 and r.json()["code"] == "capability_unavailable", (path, r.text)
        else:
            assert r.status_code == 400 and r.json()["code"] == "idempotency_key_required", (method, path, r.text)
        r = client.request(method, path, json={}, headers={"Idempotency-Key": "geen-uuid"})
        assert r.status_code == 401, (method, path)  # zonder aanmelding nooit eerst de sleutel beoordelen


def test_aud_as_list_is_refused_over_http(client: Any, hub: Any) -> None:
    sim = hub.state.sim
    for aud in ([AUD], [AUD, "home-control-hub"]):
        r = client.get("/lifecycle/v1/runs",
                       headers={"Authorization": "Bearer " + sim.mint("u1", ["viewer"], AUD, extra={"aud": aud})})
        assert r.status_code == 401 and r.json()["details"]["reason"] == "invalid_token", aud


def test_forbidden_has_details_reason(client: Any, auth: Any) -> None:
    r = client.get("/lifecycle/v1/runs", headers=auth("kind"))
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    assert r.json()["details"]["reason"] == "rol_onvoldoende"


def test_precondition_failed_names_huidige_etag(client: Any, auth: Any) -> None:
    run = client.post("/lifecycle/v1/runs", json={"app_id": "gotify", "kind": "install"},
                      headers=auth("operator", idem=True)).json()
    r = client.post(f"/lifecycle/v1/runs/{run['id']}/transitions", json={"to_state": "aborted", "reason": "test"},
                    headers={**auth("operator", idem=True), "If-Match": '"run-oud"'})
    assert r.status_code == 412 and r.json()["details"]["huidige_etag"] == run["etag"]


def test_replay_check_unavailable_has_retry_after() -> None:
    from lifecycle.replay import unavailable

    err = unavailable()
    assert err.status == 503 and err.code == "replay_check_unavailable" and err.headers["Retry-After"]


# --------------------------------------------------------------------------------------------- connector (§6)
def _beat(app: Any, age_s: int = 0) -> None:
    from datetime import timedelta

    from lifecycle.models import WorkerHeartbeat
    from lifecycle.timeutil import utcnow

    with app.state.session_factory() as session:
        session.merge(WorkerHeartbeat(name="worker", beat_at=utcnow() - timedelta(seconds=age_s), detail={}))
        session.commit()


def test_health_total_is_the_worst_check_with_fixed_names(make_app: Any, auth: Any) -> None:
    with TestClient(make_app(), base_url="http://nas.local") as c:
        _beat(c.app)
        h = c.get("/connector/v1/health", headers=auth("viewer")).json()
        assert h["status"] == "healthy" and h["reason_code"] is None
        assert {"database", "worker", "hub_link"} <= {x["name"] for x in h["checks"]}
        worker = next(x for x in h["checks"] if x["name"] == "worker")
        _beat(c.app, age_s=600)  # verouderde hartslag (§6.2): unknown + meting_verouderd, observed_at = de hartslag
        h = c.get("/connector/v1/health", headers=auth("viewer")).json()
        worker = next(x for x in h["checks"] if x["name"] == "worker")
        assert worker["status"] == "unknown" and h["status"] == "unknown" and h["reason_code"] == "meting_verouderd"
        assert worker["observed_at"] < h["observed_at"]


def test_health_hub_link_states_never_green(make_app: Any, auth: Any, data_dir: Path) -> None:
    from lifecycle.hublink import HubLinkStore

    with TestClient(make_app(), base_url="http://nas.local") as c:
        _beat(c.app)
        HubLinkStore(data_dir / "secrets").update(lambda d: d.update(confirmed_at=None))
        h = c.get("/connector/v1/health", headers=auth("viewer")).json()
        assert h["status"] == "degraded" and h["reason_code"] == "hub_unconfirmed"


def test_readyz_shows_whitelist_of_unreadable_link_and_stays_200(make_app: Any, data_dir: Path) -> None:
    """§3.5/§5: een onleesbaar koppelbestand maakt de dienst niet 'not ready', maar /readyz meldt het wel."""
    path = data_dir / "secrets" / "hub-link.json"
    with TestClient(make_app(linked=False), base_url="http://nas.local") as c:
        body = c.get("/readyz").json()
        assert body["hub_link"]["state"] == "nooit_gekoppeld" and body["hub_link"]["last_error"] is None
        path.write_text("{ kapot")
        os.chmod(path, 0o600)
        r = c.get("/readyz")
        assert r.status_code == 200 and r.json()["status"] == "ready"
        assert r.json()["hub_link"] == {"state": None, "koppelen_bezig": False,
                                        "last_error": r.json()["hub_link"]["last_error"]}
        assert r.json()["hub_link"]["last_error"]["code"] == "koppelstatus_onleesbaar"
        assert "service_token" not in json.dumps(r.json())


@pytest.mark.parametrize("mode", [0o644, 0o640])
def test_wrong_mode_fails_closed_for_jwts(make_app: Any, auth: Any, data_dir: Path, mode: int) -> None:
    """§3.5: verkeerde rechten = onleesbaar: geen enkele Hub-JWT wordt vertrouwd (401 not_linked)."""
    with TestClient(make_app(), base_url="http://nas.local") as c:
        assert c.get("/lifecycle/v1/runs", headers=auth("viewer")).status_code == 200
        os.chmod(data_dir / "secrets" / "hub-link.json", mode)
        r = c.get("/lifecycle/v1/runs", headers=auth("viewer"))
        assert r.status_code == 401 and r.json()["details"]["reason"] == "not_linked"
