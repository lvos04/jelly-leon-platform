"""Contract 1.2 §3.7: koppelen via de statuspagina — alleen zolang nooit gekoppeld, met verplichte vingerafdruk,
CSRF + Origin, 5 pogingen per 15 min per bron-IP (nooit ``X-Forwarded-For``) en 20 voor de hele dienst; het adres van
een mislukte poging wordt nergens bewaard."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
from hub_sim import HubState, create_hub_app
from starlette.testclient import TestClient

from lifecycle.app import create_app
from lifecycle.config import Settings
from lifecycle.hublink import HubLinkStore

APP = "jelly-leon-platform"
BASE = "http://lifecycle.test"
ORIGIN = {"Origin": BASE}


def csrf(page: str) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', page)
    assert m, "geen koppelformulier"
    return m.group(1)


def form(code: str, fingerprint: str, token: str, hub_url: str = "http://hub.test") -> dict[str, str]:
    return {"hub_adres": hub_url, "koppelcode": code, "hub_vingerafdruk": fingerprint, "csrf": token}


def unlinked_client(make_app: Any, ip: str = "192.0.2.10") -> TestClient:
    return TestClient(make_app(hub_url=None, linked=False), base_url=BASE, client=(ip, 50000), headers=ORIGIN)


def file_doc(data_dir: Path) -> dict[str, Any]:
    return dict(json.loads((data_dir / "secrets" / "hub-link.json").read_text()))


def test_pairing_with_code_and_fingerprint(make_app: Any, hub: Any, data_dir: Path) -> None:
    sim = hub.state.sim
    code = sim.add_grant("koppelcode", APP)
    with unlinked_client(make_app) as c:
        page = c.get("/")
        assert page.status_code == 200 and 'lang="nl"' in page.text and "Bijgewerkt" in page.text
        assert 'name="hub_vingerafdruk"' in page.text and page.headers["Referrer-Policy"] == "same-origin"
        token = csrf(page.text)
        r = c.post("/koppelen", data=form(code.lower(), sim.keyset_fingerprint().replace("-", " "), token),
                   follow_redirects=False)
        assert r.status_code == 303, r.text
        page = c.get("/?gekoppeld=1").text
        assert "De Hub heeft de koppeling bevestigd" in page and "Gekoppeld met de Hub" in page
        assert sim.keyset_fingerprint() in page
        assert code not in page and 'name="csrf"' not in page  # na de eerste koppeling nooit meer een formulier
        doc = file_doc(data_dir)
        assert doc["via"] == "koppelcode" and doc["confirmed_at"] and doc["service_token"] not in page
        r = c.post("/koppelen", data=form(code, sim.keyset_fingerprint(), token))
        assert r.status_code == 409 and "via de Hub" in r.text


def test_csrf_and_origin_are_required(make_app: Any, hub: Any) -> None:
    sim = hub.state.sim
    code = sim.add_grant("koppelcode", APP)
    with unlinked_client(make_app) as c:
        token = csrf(c.get("/").text)
        good = form(code, sim.keyset_fingerprint(), token)
        assert c.post("/koppelen", data={**good, "csrf": "fout"}).status_code == 403
        for origin in ("http://evil.test", "null", ""):
            assert c.post("/koppelen", data=good, headers={"Origin": origin}).status_code == 403, origin
        c.headers.pop("Origin")
        assert c.post("/koppelen", data=good).status_code == 403
        assert sim.calls["enroll"] == 0


def test_wrong_fingerprint_stores_nothing_and_forgets_the_address(make_app: Any, hub: Any, data_dir: Path) -> None:
    sim = hub.state.sim
    code = sim.add_grant("koppelcode", APP)
    with unlinked_client(make_app) as c:
        token = csrf(c.get("/").text)
        r = c.post("/koppelen", data=form(code, "MEGG-8GFG-2E4Y-K2RQ", token))
        assert r.status_code == 400 and "vingerafdruk" in r.text
        doc = file_doc(data_dir)
        assert doc["state"] == "nooit_gekoppeld" and doc["pending"] is None and doc["hub_url"] is None
        assert doc["last_error"]["code"] == "hub_vingerafdruk_onjuist"
        assert "hub.test" not in json.dumps(doc) and "hub.test" not in c.get("/").text
        assert c.get("/lifecycle/v1/runs").status_code == 401


def test_local_validation_without_hub_call(make_app: Any, hub: Any) -> None:
    sim = hub.state.sim
    with unlinked_client(make_app) as c:
        token = csrf(c.get("/").text)
        fp = sim.keyset_fingerprint()
        cases = [
            (form("ABCD-EFGH-JKM", fp, token), "12 tekens"),
            (form("ABCU-EFGH-JKMN", fp, token), "12 tekens"),
            (form("ABCD-EFGH-JKMN", "MEGG-8GFG", token), "16 tekens"),
            (form("ABCD-EFGH-JKMN", fp, token, hub_url="http://127.0.0.1:30400"), "loopback"),
        ]
        for data, needle in cases:
            r = c.post("/koppelen", data=data)
            assert r.status_code == 400 and needle in r.text, (data, r.text)
        r = c.post("/koppelen", data=form("ABCD-EFGH-JKMN", fp, token, hub_url="ftp://hub"))
        assert r.status_code == 400 and "http://" in r.text
        assert c.post("/koppelen", data=form("ABCD-EFGH-JKMN", fp, token)).status_code == 429  # 6e poging
        assert sim.calls["enroll"] == 0  # 5 pogingen gebruikt, geen enkele bij de Hub


def test_rate_limit_per_source_ip_and_for_the_whole_service(make_app: Any, hub: Any) -> None:
    sim = hub.state.sim
    app = make_app(hub_url=None, linked=False)
    fp = sim.keyset_fingerprint()

    def attempt(ip: str, code: str = "RADE-NRAD-EN00", **headers: str) -> int:
        with TestClient(app, base_url=BASE, client=(ip, 50000), headers={**ORIGIN, **headers}) as c:
            return c.post("/koppelen", data=form(code, fp, csrf(c.get("/").text))).status_code

    for _ in range(5):
        assert attempt("192.0.2.10") == 400
    assert attempt("192.0.2.10") == 429
    assert attempt("192.0.2.10", **{"X-Forwarded-For": "192.0.2.99"}) == 429  # header nooit gebruikt
    for ip in ("192.0.2.11", "192.0.2.12", "192.0.2.13"):  # dienstbreed max. 20 per 15 min
        for _ in range(5):
            assert attempt(ip) == 400
    assert attempt("192.0.2.14") == 429
    good = sim.add_grant("koppelcode", APP)
    assert attempt("192.0.2.15", code=good) == 429  # ook een geldige code: geen verzoek naar de Hub
    assert sim.calls["enroll"] == 20


def test_revoked_right_after_pairing_is_not_reported_as_success(make_app: Any, hub: Any, data_dir: Path) -> None:
    sim = hub.state.sim
    code = sim.add_grant("koppelcode", APP)
    sim.faults.add("self_401")
    with unlinked_client(make_app) as c:
        r = c.post("/koppelen", data=form(code, sim.keyset_fingerprint(), csrf(c.get("/").text)),
                   follow_redirects=False)
        assert r.status_code == 400 and "ingetrokken" in r.text
        doc = file_doc(data_dir)
        assert doc["state"] == "ingetrokken" and doc["service_token"] is None
        assert 'name="csrf"' not in c.get("/").text  # ooit gekoppeld: alleen via de Hub opnieuw


def test_hub_down_during_pairing_forgets_address(make_app: Any, hub: Any, data_dir: Path) -> None:
    sim = hub.state.sim
    code = sim.add_grant("koppelcode", APP)
    sim.faults.add("enroll_down")
    with unlinked_client(make_app) as c:
        r = c.post("/koppelen", data=form(code, sim.keyset_fingerprint(), csrf(c.get("/").text)))
        assert r.status_code == 400 and "niet bereikbaar" in r.text
        doc = file_doc(data_dir)
        assert doc["pending"] is None and doc["hub_url"] is None and doc["last_error"]["code"] == "hub_onbereikbaar"


def test_restart_during_pairing_marks_interrupted_then_same_code_works(make_app: Any, hub: Any,
                                                                       data_dir: Path) -> None:
    """§3.6 stap 5: na een herstart is het geheim van een lopende koppelcode-poging weg → koppelen_onderbroken; de
    gebruiker voert dezelfde code opnieuw in (toegestane herhaling bij de Hub, het token is nooit gebruikt)."""
    sim = hub.state.sim
    code = sim.add_grant("koppelcode", APP)
    from lifecycle.keys import normalize_pairing_code, public_jwk
    from lifecycle.secrets_store import load_identity

    with TestClient(hub, base_url="http://hub.test") as h:  # de Hub gaf al een token, de dienst crashte daarna
        assert h.post("/api/v1/services/enroll", json={
            "app_id": APP, "pairing_code": code, "version": "0.1.0", "base_url": None,
            "public_key_jwk": public_jwk(load_identity(data_dir / "secrets"))}).status_code == 200
    import hashlib

    HubLinkStore(data_dir / "secrets").update(lambda d: d.update(pending={
        "kind": "koppelcode", "secret_sha256": hashlib.sha256(str(normalize_pairing_code(code)).encode()).hexdigest(),
        "hub_url": "http://hub.test", "started_at": "2026-11-09T00:00:00.000Z", "attempts": 1}))
    with unlinked_client(make_app) as c:  # nieuwe app-instantie = herstart
        page = c.get("/").text
        assert "onderbroken door een herstart" in page
        r = c.post("/koppelen", data=form(code, sim.keyset_fingerprint(), csrf(page)), follow_redirects=False)
        assert r.status_code == 303, r.text
    assert file_doc(data_dir)["state"] == "gekoppeld"


def test_foreign_hub_cannot_get_trusted_without_its_fingerprint(clean_db: str, data_dir: Path, hub: Any) -> None:
    """Regressie ADR-0009 + pinning: een vreemde 'Hub' die elke code accepteert, wordt niet vertrouwd zonder dat de
    gebruiker diens vingerafdruk invoert; een JWT van die server geeft nooit toegang."""
    evil_state = HubState()
    evil_state.add_grant("koppelcode", APP, "EV1L-4B0D-C0DE")
    evil = create_hub_app(evil_state)
    targets = {"hub.test": hub, "evil.test": evil}

    def factory() -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            with TestClient(targets[request.url.host], base_url=f"http://{request.url.host}") as tc:
                r = tc.request(request.method, str(request.url), content=request.content,
                               headers=dict(request.headers))
                return httpx.Response(r.status_code, content=r.content, headers=r.headers)

        return httpx.Client(transport=httpx.MockTransport(handler))

    settings = Settings(data_dir=data_dir, database_url=clean_db)
    with TestClient(create_app(settings, http_client_factory=factory), base_url=BASE, headers=ORIGIN) as c:
        token = csrf(c.get("/").text)
        # de gebruiker typt de vingerafdruk van de echte Hub; de vreemde server levert andere sleutels → geweigerd
        r = c.post("/koppelen", data=form("EV1L-4B0D-C0DE", hub.state.sim.keyset_fingerprint(), token,
                                          hub_url="http://evil.test"))
        # (review: eerder slaagde dit al op de lokale controle van de code; nu echt bij de vreemde server geweigerd)
        assert r.status_code == 400 and "De vingerafdruk van de Hub klopt niet" in r.text, r.text
        assert evil_state.calls["enroll"] == 1
        evil_jwt = evil_state.mint("aanvaller", ["admin"], APP)
        r = c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {evil_jwt}"})
        assert r.status_code == 401 and r.json()["details"]["reason"] == "not_linked"
        assert "evil.test" not in c.get("/").text
        code = hub.state.sim.add_grant("koppelcode", APP)
        r = c.post("/koppelen", data=form(code, hub.state.sim.keyset_fingerprint(), token), follow_redirects=False)
        assert r.status_code == 303
        good = hub.state.sim.mint("beheerder", ["admin"], APP)
        assert c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {good}"}).status_code == 200
        r = c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {evil_jwt}"})
        assert r.status_code == 401 and r.json()["details"]["reason"] in {"unknown_key", "invalid_token"}


def test_body_limit_also_on_the_form(make_app: Any) -> None:
    with unlinked_client(make_app) as c:
        r = c.post("/koppelen", content=b"x" * (1024 * 1024 + 1),
                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert r.status_code == 413 and r.json()["code"] == "payload_too_large"


def test_pairing_that_finishes_after_another_link_never_overwrites_it(clean_db: str, data_dir: Path,
                                                                      hub: Any) -> None:
    """Review (wedloop, §3.7): de controle "nog nooit gekoppeld" gebeurde alleen vóór het verzoek naar de Hub. Een
    aanvaller op het LAN start een koppelcode-poging naar zijn eigen "Hub" en antwoordt pas nadat de echte koppeling
    (env-token in de worker) is opgeslagen; zijn antwoord overschreef dan token en pins. Nu wordt dat onder de
    vergrendeling geweigerd: de echte koppeling blijft, een JWT van de vreemde server geeft nooit toegang."""
    from conftest import link_with_hub

    evil_state = HubState()
    evil_state.add_grant("koppelcode", APP, "EV1L-4B0D-C0DE")
    evil = create_hub_app(evil_state)

    def factory() -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "evil.test" and request.url.path == "/api/v1/services/enroll":
                link_with_hub(data_dir, hub.state.sim)  # intussen: de echte koppeling (worker, env-token) is klaar
            target = evil if request.url.host == "evil.test" else hub
            with TestClient(target, base_url=f"http://{request.url.host}") as tc:
                r = tc.request(request.method, str(request.url), content=request.content,
                               headers=dict(request.headers))
                return httpx.Response(r.status_code, content=r.content, headers=r.headers)

        return httpx.Client(transport=httpx.MockTransport(handler))

    settings = Settings(data_dir=data_dir, database_url=clean_db)
    with TestClient(create_app(settings, http_client_factory=factory), base_url=BASE, headers=ORIGIN) as c:
        token = csrf(c.get("/").text)
        r = c.post("/koppelen", data=form("EV1L-4B0D-C0DE", evil_state.keyset_fingerprint(), token,
                                          hub_url="http://evil.test"), follow_redirects=False)
        assert r.status_code == 409 and "al eens gekoppeld" in r.text, r.text
        assert evil_state.calls["enroll"] == 1  # de vreemde server antwoordde wél met geldige sleutels
        doc = file_doc(data_dir)
        assert doc["state"] == "gekoppeld" and doc["hub_url"] == "http://hub.test" and doc["via"] == "koppelcode"
        assert doc["pins"]["keyset_fingerprint"] == hub.state.sim.keyset_fingerprint() and doc["pending"] is None
        assert "evil.test" not in json.dumps(doc)
        evil_jwt = evil_state.mint("aanvaller", ["admin"], APP)
        r = c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {evil_jwt}"})
        # zelfde kid-naam, andere sleutel → handtekening ongeldig; andere kid → unknown_key
        assert r.status_code == 401 and r.json()["details"]["reason"] in {"unknown_key", "invalid_token"}
        good = hub.state.sim.mint("beheerder", ["admin"], APP)
        assert c.get("/lifecycle/v1/runs", headers={"Authorization": f"Bearer {good}"}).status_code == 200


def test_second_pairing_attempt_while_one_is_pending_is_refused_under_the_lock(data_dir: Path, hub: Any) -> None:
    """Twee gelijktijdige formulierposts: de tweede ziet (onder de vergrendeling) de lopende poging en gaat nooit
    naar de Hub; een env-token mag wel (§3.8) en vervangt de lopende poging."""
    import pytest

    from lifecycle.enrollment import PairingRefused, mark_pending

    store = HubLinkStore(data_dir / "secrets")
    mark_pending(store, kind="koppelcode", secret_sha="a" * 64, hub_url="http://hub.test")
    with pytest.raises(PairingRefused) as exc:
        mark_pending(store, kind="koppelcode", secret_sha="b" * 64, hub_url="http://evil.test")
    assert exc.value.reason == "pending" and store.read().pending["secret_sha256"] == "a" * 64
    mark_pending(store, kind="token", secret_sha="c" * 64, hub_url="http://hub.test")
    assert store.read().pending["kind"] == "token"
    store.update(lambda d: d.update(pending=None, first_linked_at="2026-11-09T12:00:00Z"))
    with pytest.raises(PairingRefused) as exc:
        mark_pending(store, kind="koppelcode", secret_sha="d" * 64, hub_url="http://hub.test")
    assert exc.value.reason == "ever_linked" and store.read().pending is None


def test_no_pairing_form_after_unlink_and_post_is_refused_without_hub_call(make_app: Any, hub: Any,
                                                                           auth: Any) -> None:
    """Na de eerste koppeling (ook na ontkoppelen door de Hub) nooit meer koppelen via de statuspagina."""
    import uuid

    sim = hub.state.sim
    with TestClient(make_app(), base_url=BASE, headers=ORIGIN) as c:
        r = c.post("/connector/v1/hub-link/unlink", headers=auth("admin", idem=True))
        assert r.status_code == 200 and r.json()["state"] == "ontkoppeld"
        page = c.get("/")
        assert 'name="csrf"' not in page.text and "via de Hub" in page.text
        csrf_cookie = c.cookies.get("jlp_csrf")
        token = csrf_cookie or str(uuid.uuid4())
        code = sim.add_grant("koppelcode", APP)
        calls = sim.calls["enroll"]
        r = c.post("/koppelen", data=form(code, sim.keyset_fingerprint(), token))
        assert r.status_code == 409 and "via de Hub" in r.text
        assert sim.calls["enroll"] == calls
