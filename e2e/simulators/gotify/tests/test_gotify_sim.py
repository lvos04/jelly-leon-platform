"""Gotify-simulator: protocol (zoals gotify/server v3.1.1) en foutinjectie."""

from __future__ import annotations

import base64
from collections.abc import Iterator

import pytest
from gotify_sim import create_app
from starlette.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def basic(user: str, pw: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def test_health_and_version(client: TestClient) -> None:
    assert client.get("/health").json() == {"health": "green", "database": "green"}
    v = client.get("/version").json()
    assert v["version"] == "3.1.1" and set(v) == {"version", "commit", "buildDate"}


@pytest.mark.parametrize("manier", ["header", "query", "bearer"])
def test_message_with_an_application_token(client: TestClient, manier: str) -> None:
    tok = client.get("/_sim/sleutels").json()["applicaties"]["hub"]
    body = {"title": "Back-up te oud", "message": "Laatste back-up is 30 uur oud.", "priority": 8,
            "extras": {"client::display": {"contentType": "text/plain"}}}
    if manier == "header":
        r = client.post("/message", json=body, headers={"X-Gotify-Key": tok})
    elif manier == "query":
        r = client.post(f"/message?token={tok}", json=body)
    else:
        r = client.post("/message", json=body, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200, r.text
    msg = r.json()
    assert msg["appid"] == 1 and msg["title"] == "Back-up te oud" and msg["priority"] == 8 and msg["id"] == 1
    assert msg["date"].endswith("Z") and msg["extras"]["client::display"]["contentType"] == "text/plain"


def test_message_validation_and_rejection(client: TestClient) -> None:
    tok = client.get("/_sim/sleutels").json()["applicaties"]["hub"]
    r = client.post("/message", json={"title": "x"}, headers={"X-Gotify-Key": tok})
    assert r.status_code == 400 and r.json()["errorCode"] == 400 and "message" in r.json()["errorDescription"]
    r = client.post("/message", json={"message": "x"}, headers={"X-Gotify-Key": "Aongeldig"})
    assert r.status_code == 401
    assert r.json() == {"error": "Unauthorized", "errorCode": 401,
                        "errorDescription": "you need to provide a valid access token or user credentials to access "
                                            "this api"}
    # titel en prioriteit uit de applicatie als ze ontbreken (api/message.go)
    msg = client.post("/message", json={"message": "x"}, headers={"X-Gotify-Key": tok}).json()
    assert msg["title"] == "hub" and msg["priority"] == 0


def test_client_token_needs_appid_and_sees_scoped_apps(client: TestClient) -> None:
    s = client.get("/_sim/sleutels").json()
    hdr = {"X-Gotify-Key": s["client"]}
    apps = client.get("/application", headers=hdr).json()
    assert [a["name"] for a in apps] == ["hub", "uptime-kuma", "backup"]
    assert len({a["token"] for a in apps}) == 3  # I13-13-02: per bron een eigen token, geen gedeeld beheertoken
    r = client.post("/message", json={"message": "x"}, headers=hdr)
    assert r.status_code == 400 and "appid is required" in r.json()["errorDescription"]
    assert client.post("/message", json={"message": "x", "appid": 99}, headers=hdr).status_code == 400
    assert client.post("/message", json={"message": "x", "appid": 2}, headers=hdr).json()["appid"] == 2
    assert client.get("/message", headers=hdr).json()["messages"][0]["appid"] == 2
    # een applicatietoken mag geen beheer-routes lezen
    assert client.get("/application", headers={"X-Gotify-Key": s["applicaties"]["hub"]}).status_code == 401


def test_default_admin_password_check(client: TestClient) -> None:
    """Na installatie bestaat admin/admin; de Hub controleert dat het weg is (anders incident)."""
    assert client.get("/current/user", headers=basic("admin", "admin")).status_code == 200
    assert client.post("/_sim/admin-wachtwoord", json={"wachtwoord": "kort"}).json() == {"gewijzigd": False}
    assert client.post("/_sim/admin-wachtwoord", json={"wachtwoord": "een-lang-eigen-wachtwoord"}).json()["gewijzigd"]
    assert client.get("/current/user", headers=basic("admin", "admin")).status_code == 401
    assert client.get("/_sim/sleutels").json()["admin_wachtwoord_is_standaard"] is False


def test_fault_db_and_revoked_tokens(client: TestClient) -> None:
    tok = client.get("/_sim/sleutels").json()["applicaties"]["uptime-kuma"]
    client.post("/_sim/fouten", json={"naam": "db_storing"})
    r = client.get("/health")
    assert r.status_code == 500 and r.json() == {"health": "orange", "database": "red"}
    assert client.post("/message", json={"message": "x"}, headers={"X-Gotify-Key": tok}).status_code == 500
    client.delete("/_sim/fouten")
    client.post("/_sim/fouten", json={"naam": "tokens_ingetrokken"})
    assert client.post("/message", json={"message": "x"}, headers={"X-Gotify-Key": tok}).status_code == 401
    client.delete("/_sim/fouten?naam=tokens_ingetrokken")
    assert client.post("/message", json={"message": "x"}, headers={"X-Gotify-Key": tok}).status_code == 200


def test_generic_faults_with_count_and_path(client: TestClient) -> None:
    assert client.post("/_sim/fouten", json={"naam": "bestaat-niet"}).status_code == 422
    client.post("/_sim/fouten", json={"naam": "http_503", "aantal": 1, "pad": "^/health$"})
    assert client.get("/version").status_code == 200  # ander pad
    assert client.get("/health").status_code == 503
    assert client.get("/health").status_code == 200  # aantal 1 is op
    client.post("/_sim/fouten", json={"naam": "redirect", "aantal": 1})
    r = client.get("/version", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "http://elders.invalid/"
    client.post("/_sim/fouten", json={"naam": "ongeldige_json", "aantal": 1})
    with pytest.raises(ValueError):
        client.get("/version").json()
    client.post("/_sim/fouten", json={"naam": "te_groot", "aantal": 1, "waarde": 6})
    r = client.get("/version")
    assert len(r.content) > 6 * 1024 * 1024 and "content-length" not in r.headers
    client.post("/_sim/fouten", json={"naam": "traag", "aantal": 1, "waarde": 0.2})
    assert client.get("/version").status_code == 200


def test_request_log_never_contains_credentials(client: TestClient) -> None:
    tok = client.get("/_sim/sleutels").json()["applicaties"]["hub"]
    client.post(f"/message?token={tok}", json={"message": "x"})
    log = client.get("/_sim/verzoeken").json()
    assert log[-1]["methode"] == "POST" and log[-1]["aanmelding"] is True and log[-1]["query"] == []
    assert tok not in str(log)
    client.post("/_sim/reset")
    assert client.get("/_sim/verzoeken").json() == []
