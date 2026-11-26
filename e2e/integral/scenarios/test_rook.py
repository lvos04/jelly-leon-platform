"""V1-kern: ready-antwoorden én registratie, tegen de lokaal gebouwde Hub."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from e2e.integral.scenarios.conftest import Kern


SCENARIO = {
    "groepen": ("rook", "kern"),
    "eigenaar": "jelly-leon-platform",
    "verwachting": "Vier gezonde kerndiensten, een verbonden simulator, gekoppelde executor en actieve lifecycle.",
}


def test_kern_en_registratie(kern: Kern) -> None:
    assert kern.hub.get("/readyz").raise_for_status().json()["status"] == "ready"
    assert kern.simulator_gereed()
    assert kern.wijzig("POST", "/api/v1/truenas/test").json()["verbonden"] is True
    executor = kern.executor.get("/readyz").raise_for_status().json()
    assert executor["status"] == "ready" and executor["gekoppeld"] is True
    assert kern.lifecycle.get("/readyz").raise_for_status().json()["status"] == "ready"
    assert kern.geregistreerd()
