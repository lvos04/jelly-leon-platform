"""Bewaard rapport van de installatie-rooktest zonder YAML (``docs/platform-v2/bewijs/rooktest-p1.json``, I23-23-02).

Het rapport is bewijs in EVIDENCE.json; deze test bewaakt dat het eerlijk blijft: alle stappen aanwezig, niets
"geslaagd" wat niet kan (uitvoering via de lifecycle bestaat pas in P2 → ``verwacht_niet_beschikbaar`` met
``capability_unavailable``), validatie Sim-getest + NIET GEVALIDEERD (echte NAS), en geen geheimen.
De rooktest zelf draait met ``make rooktest`` (lokaal) en in de CI-job ``rooktest``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RAPPORT = ROOT / "docs" / "platform-v2" / "bewijs" / "rooktest-p1.json"
STAPPEN = ["S0", "C1", "C2", "W1", "W2", "W3", "W4", "W5", "W6", "W6b", "W7", "W8", "E1", "L1", "P1", "P2", "P3", "P4"]


def test_report_is_complete_and_honest() -> None:
    r = json.loads(RAPPORT.read_text(encoding="utf-8"))
    assert r["uitkomst"] == "geslaagd"
    assert r["validatie"] == "Sim-getest; NIET GEVALIDEERD (echte NAS)"
    stappen = {s["id"]: s for s in r["stappen"]}
    assert list(stappen) == STAPPEN
    assert {s["uitkomst"] for k, s in stappen.items() if k != "P2"} == {"geslaagd"}
    p2 = stappen["P2"]
    assert p2["uitkomst"] == "verwacht_niet_beschikbaar"
    assert p2["details"]["plannen"] == {"http": 501, "code": "capability_unavailable"}
    assert stappen["P1"]["details"]["catalog_app"] == "ix-app" and stappen["P1"]["details"]["custom_compose"] is False
    assert "geen --cap-drop" in stappen["C1"]["details"]["docker_run_opties"]
    for naam in ("hub", "executor"):
        assert stappen["C2"]["details"][naam]["in_orde"] is True
    assert stappen["W1"]["details"]["setupcode_in_api"] is False
    assert re.fullmatch(r"[0-9a-f]{40}", r["bron"]["ai-server-manager"])


def test_report_contains_no_secrets() -> None:
    tekst = RAPPORT.read_text(encoding="utf-8")
    for patroon in (r"enr_[A-Za-z0-9_-]{20,}", r"svc_[A-Za-z0-9_-]{20,}", r"\b\d+-[A-Za-z0-9]{40,}\b",
                    r"Rooktest-[A-Za-z0-9_-]{10,}", r"simreadonlykey", r"\"(token|password|wachtwoord|api_key)\":"):
        assert not re.search(patroon, tekst), patroon
