"""Heartbeat-route van de Hub-simulator (contract 1.3 §6.1) — naar het model van de echte Hub.

De flows van Node-RED (I10-10-06) melden hun hartslag hier. Een stand-in die alles aanneemt bewijst niets; daarom de
regels zelf: strikte vorm, klokgrens van 5 minuten, dedupe, snelheidsgrens, beoordeling door de Hub en ``unknown`` na
``3 × verwacht_interval_s`` zonder heartbeat. Sim-getest.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.testclient import TestClient


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


class Opstelling:
    def __init__(self, hub: Any) -> None:
        self.sim = hub.state.sim
        self.nu = 1_800_000_000.0
        self.sim.clock = lambda: self.nu
        self.client = TestClient(hub)
        _sid, token = self.sim.register_service("node-red")
        self.kop = {"Authorization": f"Bearer {token}"}

    def stuur(self, body: dict[str, Any], kop: dict[str, str] | None = None) -> Any:
        return self.client.post("/api/v1/monitoring/heartbeat", json=body, headers=self.kop if kop is None else kop)

    def gezond(self, **extra: Any) -> dict[str, Any]:
        return {"status": "healthy", "observed_at": _iso(self.nu), "verwacht_interval_s": 60, **extra}


def test_heartbeat_aangenomen_dubbel_en_te_snel(hub: Any) -> None:
    op = Opstelling(hub)
    body = op.gezond(checks=[{"name": "flow_vertraging", "status": "healthy", "detail": "3 ms"}],
                     worker={"laatste_hartslag": _iso(op.nu), "wachtrij": 0})
    r = op.stuur(body)
    assert r.status_code == 202 and r.json() == {"status": "accepted", "beoordeling": {"status": "healthy",
                                                                                     "reden": None}}
    assert op.stuur(body).json()["status"] == "duplicate"  # zelfde observed_at: geen tweede meting
    op.nu += 2
    te_snel = op.stuur(op.gezond())
    assert te_snel.status_code == 429 and te_snel.headers["Retry-After"] == "5"
    op.nu += 5
    assert op.stuur(op.gezond()).status_code == 202


def test_zonder_token_en_ongeldige_vorm(hub: Any) -> None:
    op = Opstelling(hub)
    assert op.stuur(op.gezond(), kop={}).status_code == 401
    assert op.stuur(op.gezond(), kop={"Authorization": "Bearer svc_onbekend"}).status_code == 401
    for fout in ({**op.gezond(), "extra": 1}, {**op.gezond(), "status": "ok"},
                 {**op.gezond(), "observed_at": datetime.fromtimestamp(op.nu).isoformat()},  # zonder tijdzone
                 {**op.gezond(), "verwacht_interval_s": 5},
                 op.gezond(checks=[{"name": "Hoofdletter", "status": "healthy"}]),
                 op.gezond(checks=[{"name": "x", "status": "healthy", "detail": "d" * 201}]),
                 op.gezond(worker={"wachtrij": -1}), op.gezond(worker={"onbekend": 1})):
        r = op.stuur(fout)
        assert r.status_code == 422 and r.json()["code"] == "validation_error", fout
    te_oud = op.stuur({**op.gezond(), "observed_at": _iso(op.nu - 301)})
    assert te_oud.status_code == 422 and "5 minuten" in te_oud.json()["message"]
    assert op.client.get("/_sim/heartbeats").json() == []  # niets van het bovenstaande is een meting geworden


def test_de_hub_beoordeelt_zelf(hub: Any) -> None:
    op = Opstelling(hub)

    def oordeel(body: dict[str, Any]) -> dict[str, Any]:
        op.nu += 10
        r = op.stuur({**body, "observed_at": _iso(op.nu)})
        assert r.status_code == 202, r.text
        beoordeling: dict[str, Any] = r.json()["beoordeling"]
        return beoordeling

    assert oordeel(op.gezond(checks=[{"name": "hub_bereikbaar", "status": "down"}])) == {
        "status": "degraded", "reden": "controle_faalt"}
    assert oordeel({"status": "down"}) == {"status": "down", "reden": "dienst_meldt_down"}
    assert oordeel({"status": "degraded"}) == {"status": "degraded", "reden": "dienst_meldt_degraded"}
    stil = (datetime.fromtimestamp(op.nu, UTC) - timedelta(seconds=400)).isoformat()
    assert oordeel(op.gezond(worker={"laatste_hartslag": stil})) == {"status": "degraded",
                                                                     "reden": "worker_staat_stil"}
    assert oordeel(op.gezond(worker={"wachtrij": 4, "laatste_job_klaar": stil, "jobs_stil_na_s": 60})) == {
        "status": "degraded", "reden": "jobs_staan_stil"}


def test_uitblijven_maakt_unknown_nooit_nog_gezond(hub: Any) -> None:
    """Stopt de dienst, dan blijft de heartbeat uit: na 3 × het interval is de meting verouderd en de dienst
    ``unknown`` — de laatste 'healthy' wordt nooit als actueel doorgegeven (§6.1, §6.2)."""
    op = Opstelling(hub)
    assert op.stuur(op.gezond(verwacht_interval_s=20)).status_code == 202
    (nu,) = op.client.get("/_sim/heartbeats").json()
    assert (nu["status"], nu["verouderd"], nu["app_id"]) == ("healthy", False, "node-red")
    (net,) = op.client.get("/_sim/heartbeats", params={"over_s": 60}).json()
    assert net["status"] == "healthy"  # precies 3 × 20 s: nog niet verouderd
    (later,) = op.client.get("/_sim/heartbeats", params={"over_s": 61}).json()
    assert (later["status"], later["reason_code"], later["verouderd"]) == ("unknown", "meting_verouderd", True)
    op.nu += 300
    (echt,) = op.client.get("/_sim/heartbeats").json()
    assert echt["status"] == "unknown" and echt["gemeld"] == "healthy"
