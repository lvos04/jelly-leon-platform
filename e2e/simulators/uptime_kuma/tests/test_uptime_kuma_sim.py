"""Uptime Kuma-simulator: protocol (zoals uptime-kuma 2.5.9) en foutinjectie."""

from __future__ import annotations

import base64
import re
from collections.abc import Iterator

import pytest
from starlette.testclient import TestClient
from uptime_kuma_sim import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


def basic(user: str, pw: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()}


def metrics(client: TestClient) -> dict[str, float]:
    s = client.get("/_sim/sleutels").json()
    r = client.get("/metrics", headers=basic("", s["api_key"]))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    out: dict[str, float] = {}
    for line in r.text.splitlines():
        m = re.match(r'^(monitor_status|monitor_response_time)\{monitor_id="(\d+)".*\} (-?\d+)$', line)
        if m:
            out[f"{m.group(1)}:{m.group(2)}"] = float(m.group(3))
    return out


def test_metrics_requires_the_api_key_and_reports_the_version(client: TestClient) -> None:
    s = client.get("/_sim/sleutels").json()
    r = client.get("/metrics")
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Basic")
    assert client.get("/metrics", headers=basic("", "uk1_fout")).status_code == 401
    # gebruikersnaam/wachtwoord werkt niet zolang API-sleutels aan staan (server/auth.js apiAuth)
    assert client.get("/metrics", headers=basic(*s["gebruiker"])).status_code == 401
    r = client.get("/metrics", headers=basic("wat-dan-ook", s["api_key"]))
    assert r.status_code == 200
    assert 'app_version{version="2.5.9",major="2",minor="5",patch="9"} 1' in r.text
    assert len(re.findall(r"^monitor_status\{", r.text, re.M)) == 8
    assert 'monitor_name="Hub-hartslag",monitor_type="push"' in r.text


def test_old_version_and_revoked_key(client: TestClient) -> None:
    s = client.get("/_sim/sleutels").json()
    client.post("/_sim/fouten", json={"naam": "oude_versie"})
    assert client.get("/metrics", headers=basic("", s["api_key"])).status_code == 401
    r = client.get("/metrics", headers=basic(*s["gebruiker"]))
    assert r.status_code == 200 and 'version="1.23.16"' in r.text
    client.delete("/_sim/fouten")
    client.post("/_sim/fouten", json={"naam": "metrics_weigeren"})
    assert client.get("/metrics", headers=basic("", s["api_key"])).status_code == 401


def test_status_page_and_heartbeats(client: TestClient) -> None:
    sp = client.get("/api/status-page/platform").json()
    assert sp["config"]["slug"] == "platform" and sp["incident"] is None and sp["maintenanceList"] == []
    monitors = sp["publicGroupList"][0]["monitorList"]
    assert {m["name"] for m in monitors} >= {"Hub", "TrueNAS-webinterface", "MediaGuard", "MSO", "my_house"}
    hb = client.get("/api/status-page/heartbeat/PLATFORM").json()  # slug hoofdletterongevoelig
    assert set(hb) == {"heartbeatList", "uptimeList"}
    eerste = str(monitors[0]["id"])
    assert hb["heartbeatList"][eerste][-1]["msg"] == "" and f"{eerste}_24" in hb["uptimeList"]
    r = client.get("/api/status-page/onbekend")
    assert r.status_code == 404 and r.json() == {"status": "fail", "msg": "Status Page Not Found"}
    assert client.get("/api/entry-page").json() == {"type": "entryPage", "entryPage": "dashboard"}


def test_push_monitor_as_dead_mans_switch(client: TestClient) -> None:
    tok = client.get("/_sim/sleutels").json()["push_tokens"]["Hub-hartslag"]
    assert client.get(f"/api/push/{tok}?status=up&msg=OK&ping=12").json() == {"ok": True}
    r = client.get("/api/push/onbekend")
    assert r.status_code == 404 and r.json()["ok"] is False
    assert client.get(f"/api/push/{tok}?status=down&msg=stop").json() == {"ok": True}
    assert metrics(client)["monitor_status:8"] == 0


def test_monitor_state_and_sqlite_busy(client: TestClient) -> None:
    assert client.post("/_sim/monitor/1", json={"status": 0}).json() == {"id": 1, "status": 0}
    m = metrics(client)
    assert m["monitor_status:1"] == 0 and m["monitor_response_time:1"] == -1 and m["monitor_status:2"] == 1
    client.post("/_sim/monitor/3", json={"status": 3})
    assert metrics(client)["monitor_status:3"] == 3  # onderhoud
    client.post("/_sim/fouten", json={"naam": "sqlite_busy"})
    r = client.get("/api/status-page/heartbeat/platform")
    assert r.status_code == 503 and "SQLITE_BUSY" in r.json()["msg"]
    client.post("/_sim/fouten", json={"naam": "http_500", "pad": "^/metrics$", "aantal": 1})
    s = client.get("/_sim/sleutels").json()
    r = client.get("/metrics", headers=basic("", s["api_key"]))
    assert r.status_code == 500 and r.json()["status"] == "fail"
