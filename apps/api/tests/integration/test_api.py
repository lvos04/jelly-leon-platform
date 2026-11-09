"""Integratie: echte PostgreSQL + FastAPI TestClient + Hub-simulator (JWKS/enroll/inbox)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest


def test_healthz_readyz_without_auth(client: Any) -> None:
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    r = client.get("/readyz")
    assert r.status_code == 200, r.json()
    assert {c["name"] for c in r.json()["checks"]} == {"database", "migrations", "secrets"}
    assert "X-Trace-Id" in r.headers


def test_readyz_not_ready_without_secrets(make_app: Any, data_dir: Any) -> None:
    from starlette.testclient import TestClient

    for p in (data_dir / "secrets").iterdir():
        p.unlink()
    with TestClient(make_app()) as c:
        r = c.get("/readyz")
        assert r.status_code == 503
        assert any(x["name"] == "secrets" and x["status"] == "down" for x in r.json()["checks"])


def test_error_format_and_auth_required(client: Any) -> None:
    r = client.get("/lifecycle/v1/runs")
    assert r.status_code == 401
    body = r.json()
    assert set(body) == {"code", "message", "details", "trace_id"} and body["code"] == "unauthorized"
    uuid.UUID(body["trace_id"])
    r = client.get("/lifecycle/v1/runs", headers={"Authorization": "Bearer niet-gekoppeld-token-1234"})
    assert r.status_code == 401 and r.json()["details"]["reason"] == "not_linked"


def test_roles(client: Any, auth: Any) -> None:
    assert client.get("/lifecycle/v1/runs", headers=auth("viewer")).status_code == 200
    r = client.get("/lifecycle/v1/runs", headers=auth("kind"))
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    body = {"app_id": "uptime-kuma", "kind": "install", "target_version": "1.2.25"}
    r = client.post("/lifecycle/v1/runs", json=body, headers=auth("viewer", idem=True))
    assert r.status_code == 403  # viewer mag niets wijzigen (I23-23-04)
    r = client.post("/lifecycle/v1/hub-link/unlink", headers=auth("operator", idem=True))
    assert r.status_code == 403


def test_jwks_down_fails_closed(make_app: Any, hub: Any, token: Any) -> None:
    from starlette.testclient import TestClient

    hub.state.sim.faults.add("jwks_down")
    with TestClient(make_app()) as c:
        r = c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {token('admin')}"})
        assert r.status_code == 401 and r.json()["details"]["reason"] == "jwks_unavailable"


def test_create_run_idempotent(client: Any, auth: Any) -> None:
    h = auth("operator", idem=True)
    body = {"app_id": "uptime-kuma", "kind": "install", "target_version": "1.2.25", "reason": "nieuwe monitoring"}
    r1 = client.post("/lifecycle/v1/runs", json=body, headers=h)
    assert r1.status_code == 201, r1.json()
    run = r1.json()
    assert run["state"] == "discovered" and run["state_label_nl"] == "Ontdekt"
    assert run["manual_transitions_now"] == ["aborted"] and run["allowed_next"] == ["aborted", "inventory"]
    assert r1.headers["ETag"] == run["etag"]
    r2 = client.post("/lifecycle/v1/runs", json=body, headers=h)
    assert r2.status_code == 201 and r2.json()["id"] == run["id"]
    assert r2.headers["Idempotent-Replayed"] == "true"
    listing = client.get("/lifecycle/v1/runs", headers=auth("viewer")).json()
    assert [i["id"] for i in listing["items"]] == [run["id"]]  # géén tweede uitvoering
    r3 = client.post("/lifecycle/v1/runs", json={**body, "reason": "anders"}, headers=h)
    assert r3.status_code == 422 and r3.json()["code"] == "idempotency_key_reused"


def test_idempotency_key_required_and_latest_forbidden(client: Any, auth: Any) -> None:
    body = {"app_id": "gotify", "kind": "update", "target_version": "1.1.17"}
    r = client.post("/lifecycle/v1/runs", json=body, headers=auth("operator"))
    assert r.status_code == 400 and r.json()["code"] == "idempotency_key_required"
    r = client.post("/lifecycle/v1/runs", json={**body, "target_version": "latest"}, headers=auth("admin", idem=True))
    assert r.status_code == 422 and r.json()["code"] == "latest_forbidden"
    r = client.post("/lifecycle/v1/runs", json={**body, "app_id": "Geen_Geldige"}, headers=auth("admin", idem=True))
    assert r.status_code == 422 and r.json()["code"] == "validation_error"


def _new_run(client: Any, auth: Any, **body: Any) -> dict[str, Any]:
    payload = {"app_id": "node-red", "kind": "install", "target_version": "1.4.35", **body}
    r = client.post("/lifecycle/v1/runs", json=payload, headers=auth("operator", idem=True))
    assert r.status_code == 201
    return dict(r.json())


def test_transition_abort_with_etag(client: Any, auth: Any) -> None:
    run = _new_run(client, auth)
    url = f"/lifecycle/v1/runs/{run['id']}/transitions"
    body = {"to_state": "aborted", "reason": "gebruiker zag af van installatie"}
    r = client.post(url, json=body, headers=auth("operator", idem=True))
    assert r.status_code == 428
    r = client.post(url, json=body, headers=auth("operator", idem=True, **{"If-Match": '"oud"'}))
    assert r.status_code == 412 and r.json()["details"]["current_etag"] == run["etag"]
    r = client.post(url, json=body, headers=auth("operator", idem=True, **{"If-Match": run["etag"]}))
    assert r.status_code == 200, r.json()
    done = r.json()
    assert done["state"] == "aborted" and done["allowed_next"] == [] and len(done["transitions"]) == 2
    r = client.post(url, json=body, headers=auth("operator", idem=True, **{"If-Match": done["etag"]}))
    assert r.status_code == 409 and r.json()["code"] == "run_terminal"


def test_transitions_needing_engine_are_capability_unavailable(client: Any, auth: Any) -> None:
    run = _new_run(client, auth)
    url = f"/lifecycle/v1/runs/{run['id']}/transitions"
    for evidence in ({}, {"inventory_ref": "nep-bewijs"}):
        r = client.post(
            url,
            json={"to_state": "inventory", "reason": "probeer verder", "evidence": evidence},
            headers=auth("admin", idem=True, **{"If-Match": run["etag"]}),
        )
        assert r.status_code == 501 and r.json()["code"] == "capability_unavailable"
    r = client.post(
        url,
        json={"to_state": "deployed", "reason": "overslaan", "evidence": {"execution_evidence_ref": "x"}},
        headers=auth("admin", idem=True, **{"If-Match": run["etag"]}),
    )
    assert r.status_code == 409 and r.json()["code"] == "invalid_transition"
    assert client.get(f"/lifecycle/v1/runs/{run['id']}", headers=auth("viewer")).json()["state"] == "discovered"


@pytest.mark.parametrize("path", ["install", "update", "rollback", "uninstall", "move"])
def test_p2_plans_are_honest_501(client: Any, auth: Any, path: str) -> None:
    r = client.post(f"/lifecycle/v1/plans/{path}/preview", json={}, headers=auth("admin", idem=True))
    assert r.status_code == 501 and r.json()["code"] == "capability_unavailable"
    assert r.json()["details"]["planned_phase"] == "P2"


def test_pagination_cursor(client: Any, auth: Any) -> None:
    ids = [_new_run(client, auth, app_id=f"app-{i}")["id"] for i in range(5)]
    seen: list[str] = []
    cursor = None
    while True:
        params = {"limit": 2, **({"cursor": cursor} if cursor else {})}
        page = client.get("/lifecycle/v1/runs", params=params, headers=auth("viewer")).json()
        seen += [i["id"] for i in page["items"]]
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert seen == list(reversed(ids))
    assert client.get("/lifecycle/v1/runs", params={"limit": 500}, headers=auth("viewer")).status_code == 400
    one = client.get("/lifecycle/v1/runs", params={"app_id": "app-3"}, headers=auth("viewer")).json()
    assert len(one["items"]) == 1


def test_connector_contract(client: Any, auth: Any) -> None:
    d = client.get("/connector/v1/discover", headers=auth("viewer")).json()
    assert d["app_id"] == "jelly-leon-platform" and d["schema_version"] == "1.0" and d["capabilities"] == ["read"]
    assert d["timestamp_utc"].endswith("Z")
    h = client.get("/connector/v1/health", headers=auth("viewer")).json()
    assert h["status"] == "degraded"  # worker draait niet in deze test, Hub niet gekoppeld: nooit "healthy"
    names = {c["name"]: c["status"] for c in h["checks"]}
    assert names["database"] == "healthy" and names["worker"] == "unknown" and names["hub_link"] == "unknown"
    for path in ("propose", "apply", "rollback"):
        r = client.post(f"/connector/v1/{path}", json={}, headers=auth("admin", idem=True))
        assert r.status_code == 501 and r.json()["code"] == "capability_unavailable"
    run = _new_run(client, auth)
    items = client.get("/connector/v1/items", params={"type": "lifecycle_run"}, headers=auth("viewer")).json()
    assert items["type"] == "lifecycle_run" and items["items"][0]["id"] == run["id"]
    assert client.get("/connector/v1/items", params={"type": "x"}, headers=auth("viewer")).status_code == 400
    ev = client.get("/connector/v1/events", headers=auth("viewer")).json()
    created = [e for e in ev["items"] if e["event_type"] == "lifecycle.run.created"]
    assert created and created[0]["source_app"] == "jelly-leon-platform" and created[0]["entity"]["id"] == run["id"]
    assert created[0]["schema_version"] == "1.0" and created[0]["timestamp_utc"].endswith("Z")
    ev2 = client.get("/connector/v1/events", params={"cursor": ev["next_cursor"]}, headers=auth("viewer")).json()
    assert ev2["items"] == []


def test_evidence_is_redacted(client: Any, auth: Any) -> None:
    import sqlalchemy as sa

    from lifecycle.models import AuditEntry

    engine = client.app.state.engine
    with engine.begin() as conn:
        audit_id = uuid.uuid4()
        conn.execute(
            sa.insert(AuditEntry.__table__).values(
                id=audit_id, at=sa.func.now(), actor_type="system", actor_id="t", action="test", target_type="x",
                target_id="y", outcome="ok", detail={"api_key": "zeer-geheim", "note": "Bearer abc.def.ghi"},
                trace_id=uuid.uuid4(),
            )
        )
    r = client.get(f"/connector/v1/evidence/{audit_id}", headers=auth("viewer"))
    assert r.status_code == 200
    assert "zeer-geheim" not in r.text and "abc.def.ghi" not in r.text
    assert client.get(f"/connector/v1/evidence/{uuid.uuid4()}", headers=auth("viewer")).status_code == 404


def test_openapi_has_no_docs_ui_and_security_headers(client: Any) -> None:
    assert client.get("/docs").status_code == 404
    r = client.get("/openapi.json")
    assert r.status_code == 200 and r.headers["X-Frame-Options"] == "DENY"
