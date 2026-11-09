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
    with TestClient(make_app(linked=False)) as c:
        r = c.get("/readyz")
        assert r.status_code == 503
        assert any(x["name"] == "secrets" and x["status"] == "down" for x in r.json()["checks"])


def test_error_format_and_auth_required(client: Any) -> None:
    r = client.get("/lifecycle/v1/runs")
    assert r.status_code == 401
    body = r.json()
    assert set(body) == {"code", "message", "details", "trace_id"} and body["code"] == "unauthorized"
    uuid.UUID(body["trace_id"])
    r = client.get("/lifecycle/v1/runs", headers={"Authorization": "Bearer verkeerd-token-1234"})
    assert r.status_code == 401 and r.json()["details"]["reason"] == "invalid_token"


def test_not_linked_refuses_every_credential(make_app: Any, token: Any) -> None:
    """ADR-0005/0009: niet gekoppeld = alles 401, ook een geldig Hub-JWT en ook als HUB_URL in de omgeving staat."""
    from starlette.testclient import TestClient

    with TestClient(make_app(linked=False)) as c:
        r = c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {token('admin')}"})
        assert r.status_code == 401 and r.json()["details"]["reason"] == "not_linked"
        r = c.get("/lifecycle/v1/runs", headers={"Authorization": "Bearer niet-gekoppeld-token-1234"})
        assert r.status_code == 401 and r.json()["details"]["reason"] == "not_linked"
        assert c.get("/readyz").status_code == 200  # gezondheid blijft zonder auth zichtbaar


def test_roles(client: Any, auth: Any) -> None:
    assert client.get("/lifecycle/v1/runs", headers=auth("viewer")).status_code == 200
    r = client.get("/lifecycle/v1/runs", headers=auth("kind"))
    assert r.status_code == 403 and r.json()["code"] == "forbidden"
    body = {"app_id": "uptime-kuma", "kind": "install", "target_version": "1.2.25"}
    r = client.post("/lifecycle/v1/runs", json=body, headers=auth("viewer", idem=True))
    assert r.status_code == 403  # viewer mag niets wijzigen (I23-23-04)
    r = client.post("/connector/v1/hub-link/unlink", headers=auth("operator", idem=True))
    assert r.status_code == 403


def test_pinned_keys_only_jwks_over_http_adds_no_trust(make_app: Any, hub: Any, token: Any) -> None:
    """§4.5: gepinde sleutels werken ook als de JWKS-route van de Hub weg is; een nieuwe sleutel die alleen via HTTP
    (JWKS) zichtbaar is, wordt nooit vertrouwd (401 unknown_key + achtergrondpoll van de ondertekende rotaties)."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from starlette.testclient import TestClient

    sim = hub.state.sim
    sim.faults.add("jwks_down")
    with TestClient(make_app()) as c:
        assert c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {token('admin')}"}).status_code == 200
        sim.faults.discard("jwks_down")
        sim.jwt_keys.append(("alleen-in-jwks", Ed25519PrivateKey.generate()))  # niet ondertekend geroteerd
        c.app.state.key_poller.run_inline = True
        r = c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {token('admin')}"})
        assert r.status_code == 401 and r.json()["details"]["reason"] == "unknown_key"
        assert c.app.state.key_poller.triggered == 1 and c.app.state.key_poller.last_result == "geen"
        c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {token('admin')}"})
        assert c.app.state.key_poller.triggered == 1  # max. 1× per 30 s


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
    # review P0-afronding: ook 'latest' als tag in een image-verwijzing, in elke schrijfwijze
    for bad in ("LATEST", "ghcr.io/lvos04/gotify:latest", "gotify:latest@sha256:" + "a" * 64):
        r = client.post("/lifecycle/v1/runs", json={**body, "target_version": bad}, headers=auth("admin", idem=True))
        assert r.status_code == 422 and r.json()["code"] == "latest_forbidden", bad
    for junk in ("1.0 beta", "<script>", "", " 1.0"):
        r = client.post("/lifecycle/v1/runs", json={**body, "target_version": junk}, headers=auth("admin", idem=True))
        assert r.status_code == 422 and r.json()["code"] == "validation_error", junk
    for good in ("v1.1.17", "sha256:" + "b" * 64, "ghcr.io/lvos04/gotify:1.1.17@sha256:" + "c" * 64, "latest-ish"):
        r = client.post("/lifecycle/v1/runs", json={**body, "target_version": good}, headers=auth("admin", idem=True))
        assert r.status_code == 201, (good, r.text)


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
    assert h["status"] == "degraded"  # worker draait niet in deze test (geen hartslag): nooit "healthy"
    names = {c["name"]: c["status"] for c in h["checks"]}
    assert names["database"] == "healthy" and names["worker"] == "unknown" and names["hub_link"] == "healthy"
    assert h["reason_code"] == "worker_unknown"
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


def test_transition_evidence_is_redacted_and_bounded(client: Any, auth: Any) -> None:
    """Contract §3: evidence nooit met geheimen; invoer begrensd (16 KiB)."""
    run = _new_run(client, auth)
    url = f"/lifecycle/v1/runs/{run['id']}/transitions"
    huge = {"to_state": "aborted", "reason": "te groot", "evidence": {"log": "x" * 20000}}
    r = client.post(url, json=huge, headers=auth("operator", idem=True, **{"If-Match": run["etag"]}))
    assert r.status_code == 422 and r.json()["code"] == "validation_error"
    body = {
        "to_state": "aborted",
        "reason": "afgebroken; token in tekst Bearer abc.def.ghi",
        "evidence": {"api_key": "zeer-geheim-1234", "ticket_ref": "inc-42", "note": "Bearer xyz.uvw.rst"},
    }
    r = client.post(url, json=body, headers=auth("operator", idem=True, **{"If-Match": run["etag"]}))
    assert r.status_code == 200, r.json()
    stored = client.get(f"/lifecycle/v1/runs/{run['id']}", headers=auth("viewer"))
    for text in (r.text, stored.text):
        assert "zeer-geheim-1234" not in text and "abc.def.ghi" not in text and "xyz.uvw.rst" not in text
    last = stored.json()["transitions"][-1]
    assert last["evidence"]["ticket_ref"] == "inc-42" and last["evidence"]["api_key"] == "***"


PUBLIC_PATHS = {"/healthz", "/readyz", "/openapi.json", "/", "/koppelen"}


def test_every_route_requires_auth_and_kind_role_is_refused(client: Any, auth: Any) -> None:
    """Review P0: autorisatie op elke route — zonder credentials 401, met rol 'kind' 403, nooit open bij fout."""
    checked = 0
    for template, operations in client.app.openapi()["paths"].items():  # alle API-routes (statuspagina zit er niet in)
        if template in PUBLIC_PATHS:
            continue
        path = template.replace("{run_id}", str(uuid.uuid4())).replace("{evidence_id}", str(uuid.uuid4()))
        for method in sorted(m.upper() for m in operations):
            r = client.request(method, path, json={})
            assert r.status_code == 401 and r.json()["code"] == "unauthorized", (method, path, r.status_code)
            r = client.request(method, path, json={}, headers=auth("kind", idem=True))
            assert r.status_code == 403 and r.json()["code"] == "forbidden", (method, path, r.status_code)
            checked += 1
    assert checked >= 20


def test_request_body_over_1_mib_is_refused_before_auth(client: Any, auth: Any) -> None:
    """Invoervalidatie (review P0-afronding): bodies > 1 MiB → 413 in contractformaat, ook zonder Content-Length en
    vóór de authenticatie (anders leest de app elke body volledig in het geheugen)."""
    from lifecycle.limits import MAX_BODY_BYTES

    big = b'{"app_id":"' + b"a" * (MAX_BODY_BYTES + 10) + b'"}'
    r = client.post("/lifecycle/v1/runs", content=big, headers={"Content-Type": "application/json"})
    assert r.status_code == 413, r.text
    body = r.json()
    assert set(body) == {"code", "message", "details", "trace_id"} and body["code"] == "payload_too_large"
    assert body["details"]["max_bytes"] == MAX_BODY_BYTES and r.headers["X-Trace-Id"] == body["trace_id"]
    uuid.UUID(body["trace_id"])

    def chunks() -> Any:  # zonder Content-Length (chunked): telt tijdens het lezen
        for _ in range(MAX_BODY_BYTES // 65536 + 2):
            yield b"x" * 65536

    r = client.post("/lifecycle/v1/runs", content=chunks(), headers={**auth("admin", idem=True),
                                                                     "Content-Type": "application/json"})
    assert r.status_code == 413 and r.json()["code"] == "payload_too_large", r.text
    assert client.get("/lifecycle/v1/runs", headers=auth("viewer")).json()["items"] == []  # niets aangemaakt
    r = client.post("/koppelen", content=chunks(), headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 413
    # net onder de grens: gewone afhandeling (hier: geen aanmelding → 401)
    ok = b'{"reason":"' + b"a" * (MAX_BODY_BYTES - 100) + b'"}'
    r = client.post("/lifecycle/v1/runs", content=ok, headers={"Content-Type": "application/json"})
    assert r.status_code == 401


def test_hub_link_on_connector_with_service_jwt_and_viewer(client: Any, hub: Any, auth: Any) -> None:
    sim = hub.state.sim
    service = {"Authorization": "Bearer " + sim.mint_service("jelly-leon-platform")}
    for headers in (service, auth("viewer")):
        r = client.get("/connector/v1/hub-link", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "gekoppeld" and body["pinned_kids"]["jwt"] == [sim.kid]
        assert body["rotation_sequence"] == 0 and body["keyset_fingerprint"] == sim.keyset_fingerprint()
        assert "service_token" not in body and "pending" not in body
    # de oude routes uit 1.1 bestaan niet meer
    assert client.get("/lifecycle/v1/hub-link", headers=auth("viewer")).status_code == 404
    assert client.post("/lifecycle/v1/hub-link/unlink", headers=auth("admin", idem=True)).status_code == 404


def test_incoming_service_token_is_refused(client: Any, data_dir: Any) -> None:
    from lifecycle.hublink import HubLinkStore

    own = HubLinkStore(data_dir / "secrets").read().service_token
    r = client.get("/connector/v1/discover", headers={"Authorization": f"Bearer {own}"})
    assert r.status_code == 401 and r.json()["details"]["reason"] == "service_token_not_accepted"


@pytest.mark.parametrize("who", ["service", "admin"])
def test_unlink_by_hub_or_admin_then_everything_refused(make_app: Any, hub: Any, auth: Any, who: str) -> None:
    from starlette.testclient import TestClient

    sim = hub.state.sim

    def creds(idem: str | None = None) -> dict[str, str]:
        if who == "service":
            h = {"Authorization": "Bearer " + sim.mint_service("jelly-leon-platform")}
            return {**h, **({"Idempotency-Key": idem} if idem else {})}
        return auth("admin", **({"Idempotency-Key": idem} if idem else {}))

    with TestClient(make_app()) as c:
        r = c.post("/connector/v1/hub-link/unlink", headers=creds())
        assert r.status_code == 400 and r.json()["code"] == "idempotency_key_required"
        key = str(uuid.uuid4())
        r = c.post("/connector/v1/hub-link/unlink", headers=creds(key))
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "ontkoppeld" and body["service_token_last4"] is None and body["pinned_kids"] == {
            "jwt": [], "policy": []}
        # herhaalde unlink (antwoord kwijt bij de Hub) → 401 not_linked: telt voor de Hub als geslaagd
        r = c.post("/connector/v1/hub-link/unlink", headers=creds(key))
        assert r.status_code == 401 and r.json()["details"]["reason"] == "not_linked"
        r = c.get("/lifecycle/v1/runs", headers=auth("admin"))
        assert r.status_code == 401 and r.json()["details"]["reason"] == "not_linked"
        page = c.get("/").text
        assert 'name="csrf"' not in page and "via de Hub" in page  # ooit gekoppeld: geen koppelformulier


def test_jti_replay_cache_for_mutations(client: Any, hub: Any) -> None:
    """§4.6: een mutatie met een al gebruikte jti → 401 token_replayed, behalve een exacte herhaling (zelfde jti,
    methode, pad en Idempotency-Key), die de idempotency-laag met het opgeslagen resultaat beantwoordt."""
    sim = hub.state.sim
    jti = str(uuid.uuid4())
    tok = sim.mint("operator-1", ["operator"], "jelly-leon-platform", jti=jti)
    body = {"app_id": "uptime-kuma", "kind": "install", "target_version": "1.2.25"}
    key = str(uuid.uuid4())
    h = {"Authorization": f"Bearer {tok}", "Idempotency-Key": key}
    r1 = client.post("/lifecycle/v1/runs", json=body, headers=h)
    assert r1.status_code == 201, r1.text
    r2 = client.post("/lifecycle/v1/runs", json=body, headers=h)  # exacte herhaling
    assert r2.status_code == 201 and r2.json()["id"] == r1.json()["id"] and r2.headers["Idempotent-Replayed"] == "true"
    r3 = client.post("/lifecycle/v1/runs", json=body, headers={**h, "Idempotency-Key": str(uuid.uuid4())})
    assert r3.status_code == 401 and r3.json()["details"]["reason"] == "token_replayed"
    r4 = client.post(f"/lifecycle/v1/runs/{r1.json()['id']}/transitions",
                     json={"to_state": "aborted", "reason": "ander pad"}, headers={**h, "If-Match": r1.json()["etag"]})
    assert r4.status_code == 401 and r4.json()["details"]["reason"] == "token_replayed"
    # lezen mag binnen de levensduur vaker, ook met dezelfde jti
    for _ in range(3):
        assert client.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    listing = client.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {tok}"}).json()
    assert len(listing["items"]) == 1  # geen tweede uitvoering


def test_replay_check_unavailable_is_fail_closed(client: Any, hub: Any) -> None:
    import sqlalchemy as sa

    with client.app.state.engine.begin() as conn:
        conn.execute(sa.text("ALTER TABLE jwt_replay RENAME TO jwt_replay_weg"))
    try:
        tok = hub.state.sim.mint("operator-1", ["operator"], "jelly-leon-platform")
        r = client.post("/lifecycle/v1/runs", json={"app_id": "gotify", "kind": "install", "target_version": "1.1.17"},
                        headers={"Authorization": f"Bearer {tok}", "Idempotency-Key": str(uuid.uuid4())})
        assert r.status_code == 503 and r.json()["code"] == "replay_check_unavailable"
        assert client.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {tok}"}).status_code == 200
    finally:
        with client.app.state.engine.begin() as conn:
            conn.execute(sa.text("ALTER TABLE jwt_replay_weg RENAME TO jwt_replay"))


def test_service_jwt_rights(client: Any, hub: Any) -> None:
    """De Hub (principal ``service:hub``) mag lezen en runs vastleggen, maar is nooit admin."""
    sim = hub.state.sim

    def h(idem: bool = False) -> dict[str, str]:
        base = {"Authorization": "Bearer " + sim.mint_service("jelly-leon-platform")}
        return {**base, "Idempotency-Key": str(uuid.uuid4())} if idem else base

    assert client.get("/connector/v1/items", headers=h()).status_code == 200
    r = client.post("/lifecycle/v1/runs", json={"app_id": "gotify", "kind": "install", "target_version": "1.1.17"},
                    headers=h(idem=True))
    assert r.status_code == 201 and r.json()["requested_by"] == {"type": "system", "id": "hub"}
