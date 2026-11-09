"""Beszel-hub-simulator: protocol (zoals Beszel v0.21.1 / PocketBase v0.40.5) en foutinjectie."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import pytest
from beszel_sim import create_app
from starlette.testclient import TestClient


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def aanmelden(client: TestClient) -> dict[str, str]:
    assert client.post("/api/beszel/create-user", json={"email": "beheer@sim.invalid",
                                                        "password": "lang-genoeg-wachtwoord"}).status_code == 200
    r = client.post("/api/collections/users/auth-with-password",
                    json={"identity": "beheer@sim.invalid", "password": "lang-genoeg-wachtwoord"})
    assert r.status_code == 200
    return {"Authorization": r.json()["token"]}


def pbtijd(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S.%fZ").replace(tzinfo=UTC)


def laatste_meting(client: TestClient, hdr: dict[str, str]) -> dict[str, Any]:
    sid = client.get("/api/collections/systems/records", headers=hdr).json()["items"][0]["id"]
    r = client.get("/api/collections/system_stats/records", headers=hdr,
                   params={"filter": f"(system='{sid}' && type='1m')", "sort": "-created", "perPage": 1})
    return dict(r.json()["items"][0])


def test_first_run_window_and_bootstrap(client: TestClient) -> None:
    assert client.get("/api/health").json() == {"message": "API is healthy.", "code": 200, "data": {}}
    assert client.get("/api/beszel/first-run").json() == {"firstRun": True}
    assert client.post("/api/beszel/create-user", json={"email": "x"}).status_code == 400
    aanmelden(client)
    assert client.get("/api/beszel/first-run").json() == {"firstRun": False}
    # tweede bootstrap geweigerd (internal/users/users.go)
    r = client.post("/api/beszel/create-user", json={"email": "aanvaller@sim.invalid", "password": "x"})
    assert r.status_code == 403 and r.json() == {"err": "Forbidden"}


def test_lists_without_token_are_empty_not_401(client: TestClient) -> None:
    """PocketBase-listRule: zonder token 200 met een lege lijst. Een client die dat als 'geen systemen' leest,
    zou een verse of uitgelogde situatie als gezond tonen — de Hub moet eerst aanmelden."""
    aanmelden(client)
    r = client.get("/api/collections/systems/records")
    assert r.status_code == 200 and r.json()["items"] == [] and r.json()["totalItems"] == 0
    r = client.get("/api/beszel/info")
    assert r.status_code == 401 and r.json()["status"] == 401
    bad = client.post("/api/collections/users/auth-with-password", json={"identity": "beheer@sim.invalid",
                                                                         "password": "fout"})
    assert bad.status_code == 400 and bad.json() == {"data": {}, "message": "Failed to authenticate.", "status": 400}


def test_info_systems_and_stats(client: TestClient) -> None:
    hdr = aanmelden(client)
    info = client.get("/api/beszel/info", headers=hdr).json()
    assert info["v"] == "0.21.1" and info["key"].startswith("ssh-ed25519 ") and info["cu"] is False
    assert client.get("/api/beszel/getkey", headers=hdr).json() == info  # verouderde alias
    systems = client.get("/api/collections/systems/records", headers=hdr).json()
    assert systems["totalItems"] == 1 and systems["items"][0]["status"] == "up"
    assert systems["items"][0]["info"]["v"] == "0.21.1"
    meting = laatste_meting(client, hdr)
    assert meting["type"] == "1m" and (datetime.now(UTC) - pbtijd(meting["created"])).total_seconds() < 120
    s = meting["stats"]
    assert {"cpu", "mp", "dp", "la", "t", "mem"} <= set(s) and s["mz"] == 20.0 and len(s["mem"]) == 4
    pag = client.get("/api/collections/system_stats/records", headers=hdr, params={"perPage": 10, "page": 2}).json()
    assert pag["page"] == 2 and pag["perPage"] == 10 and len(pag["items"]) == 10 and pag["totalPages"] >= 6
    assert client.get("/api/collections/onbekend/records", headers=hdr).status_code == 404
    assert client.get("/api/collections/containers/records", headers=hdr).json()["items"] == []


def test_agent_down_stale_no_arc_and_duplicate_host(client: TestClient) -> None:
    hdr = aanmelden(client)
    client.post("/_sim/fouten", json={"naam": "agent_down"})
    assert client.get("/api/collections/systems/records", headers=hdr).json()["items"][0]["status"] == "down"
    assert (datetime.now(UTC) - pbtijd(laatste_meting(client, hdr)["created"])).total_seconds() > 40 * 60
    client.delete("/_sim/fouten")
    client.post("/_sim/fouten", json={"naam": "verouderd", "waarde": 90})
    meting = laatste_meting(client, hdr)
    assert (datetime.now(UTC) - pbtijd(meting["created"])).total_seconds() > 85 * 60
    client.delete("/_sim/fouten")
    client.post("/_sim/fouten", json={"naam": "geen_arc"})
    s = laatste_meting(client, hdr)["stats"]
    assert "mz" not in s and s["mem"][3] == 0
    client.delete("/_sim/fouten")
    client.post("/_sim/fouten", json={"naam": "dubbele_host"})
    items = client.get("/api/collections/systems/records", headers=hdr).json()["items"]
    assert len(items) == 2 and items[0]["name"] == items[1]["name"] and items[0]["id"] != items[1]["id"]


def test_token_refresh_and_expiry(client: TestClient) -> None:
    hdr = aanmelden(client)
    r = client.post("/api/collections/users/auth-refresh", headers=hdr)
    assert r.status_code == 200 and r.json()["token"] != hdr["Authorization"]
    client.post("/_sim/fouten", json={"naam": "token_verlopen"})
    assert client.post("/api/collections/users/auth-refresh", headers=hdr).status_code == 401
    assert client.get("/api/collections/systems/records", headers=hdr).json()["items"] == []
