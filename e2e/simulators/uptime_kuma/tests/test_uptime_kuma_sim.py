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
    assert client.get("/api/entry-page").json() == {"type": "entryPage", "entryPage": None}


def test_push_monitor_as_dead_mans_switch(client: TestClient) -> None:
    tok = client.get("/_sim/sleutels").json()["push_tokens"]["Hub-hartslag"]
    assert client.get(f"/api/push/{tok}?status=up&msg=OK&ping=12").json() == {"ok": True}
    r = client.get("/api/push/onbekend")
    assert r.status_code == 404 and r.json()["ok"] is False
    assert client.get(f"/api/push/{tok}?status=down&msg=stop").json() == {"ok": True}
    assert metrics(client)["monitor_status:8"] == 0


@pytest.mark.parametrize(("ping", "status"), [("-5", 404), ("100000000001", 404), ("12.5", 200), ("abc", 200),
                                              ("0", 200), ("7ms", 200)])
def test_push_ping_is_validated_like_uptime_kuma(client: TestClient, ping: str, status: int) -> None:
    """api-router.js: ``parseFloat(ping) || null``; buiten 0…1e11 ms → 404 {ok:false} en geen hartslag."""
    tok = client.get("/_sim/sleutels").json()["push_tokens"]["Hub-hartslag"]
    voor = len(client.get("/api/status-page/heartbeat/platform").json()["heartbeatList"]["8"])
    r = client.get(f"/api/push/{tok}", params={"status": "down", "ping": ping})
    assert r.status_code == status
    lijst = client.get("/api/status-page/heartbeat/platform").json()["heartbeatList"]["8"]
    if status == 404:
        assert r.json() == {"ok": False, "msg": "Invalid ping value. Must be between 0 and 100000000000 ms."}
        assert len(lijst) == voor and metrics(client)["monitor_status:8"] == 1  # niets vastgelegd
    else:
        verwacht = {"12.5": 12.5, "abc": None, "0": None, "7ms": 7}[ping]
        assert lijst[-1]["ping"] == verwacht and lijst[-1]["status"] == 0


def test_request_log_redacts_the_push_token(client: TestClient) -> None:
    """De push-token ís het geheim (wie hem kent, houdt de dead-man's switch op 'up'): nooit in het verzoeklog."""
    s = client.get("/_sim/sleutels").json()
    tok = s["push_tokens"]["Hub-hartslag"]
    client.get(f"/api/push/{tok}?status=up&ping=3")
    client.get("/api/push/onbekend-token")
    client.get("/metrics", headers=basic("", s["api_key"]))
    log = client.get("/_sim/verzoeken").json()
    assert tok not in str(log) and "onbekend-token" not in str(log) and s["api_key"] not in str(log)
    pushes = [v for v in log if v["pad"].startswith("/api/push/")]
    assert [v["pad"] for v in pushes] == ["/api/push/<push-token>"] * 2
    assert all(v["aanmelding"] is True for v in pushes) and pushes[0]["query"] == ["ping", "status"]
    assert log[-1]["pad"] == "/metrics" and log[-1]["aanmelding"] is True
    # foutinjectie met een pad-regex werkt nog op het echte pad (de redactie geldt alleen voor het log)
    client.post("/_sim/fouten", json={"naam": "http_503", "aantal": 1, "pad": "^/api/push/"})
    assert client.get(f"/api/push/{tok}").status_code == 503
    assert client.get(f"/api/push/{tok}").json() == {"ok": True}


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
