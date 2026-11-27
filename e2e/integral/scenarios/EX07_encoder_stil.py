"""EX07 · MediaGuard en Hub. Stilstand van de proefworker geeft één melding en een alleen-lezen-diagnose."""

import json
import os
from pathlib import Path
from typing import Any

from e2e.integral.bewijs import schrijf
from e2e.integral.media import behoud, inloggen, plan_werk
from e2e.integral.scenarios.conftest import Kern
from e2e.integral.scenarios.hubveiligheid import schrijfacties

SCENARIO = {
    "eigenaar": "MediaGuard en Hub", "groepen": ("media", "uitval"), "diensten": ("mediaguard",),
    "soort": "Sim+", "blokkade": "NIET GEVALIDEERD (hardware)",
    "toelichting": "De echte A310-lane en het herstel van de hardware vallen onder N-P6-09.",
    "verwachting": "Herhaalde bewaking van een stilstaande GPU-proefworker geeft één melding, leesbare diagnose "
                   "en geen herstartverzoek.",
}


def test_stilstaande_worker(kern: Kern) -> None:
    mg = inloggen(kern, "mediaguard")
    lezer = kern.client("mediaguard", 30080)
    sleutel = mg.post("/api/v1/auth/api-keys", json={"name": "diagnoseproef", "role": "viewer", "scopes": []})
    lezer.headers["X-Api-Key"] = sleutel.raise_for_status().json()["key"]

    def meldingen() -> list[dict[str, Any]]:
        rijen = lezer.get("/api/v1/notifications", params={"full": True}).raise_for_status().json()
        return [rij for rij in rijen if rij["dedupe_key"] == "monitor.encoder.idle.gpu"]

    with behoud(kern, mg, plan_werk(kern)) as tel:
        assert meldingen() == []
        vooraf = schrijfacties(kern)
        fixture = Path(__file__).resolve().parents[1] / "mediafixtures" / "encoder_stilstand.py"
        modus = "voortgang" if os.environ.get("INTEGRAAL_ZONDER_STORING") == "1" else "stil"
        raw = kern.net.project.compose("exec", "-T", "--user", "568:568", "mediaguard", "/opt/venv/bin/python",
                                       "-", modus, invoer=fixture.read_text())
        feiten = json.loads(raw)
        eerste = meldingen()
        feiten.update(meldingen=len(eerste), diagnose_leesbaar=bool(eerste and eerste[0]["severity"] == "warning"
                      and "werkgeheugen" in eerste[0]["body"] and eerste[0]["link"] == "/av1"),
                      diagnose_ongewijzigd=eerste == meldingen(),
                      nas_schrijfverzoeken=len(schrijfacties(kern)) - len(vooraf))
        tel("tijdens", False)
        schrijf(kern.net.dossier / "encoder.json", feiten)
        assert [r["totaal_meldingen"] for r in feiten["ronden"]] == [0, 0, 1, 1], "Eén melding na 30 minuten stilstand"
        assert [r["nieuwe_melding"] for r in feiten["ronden"]] == [False, False, True, False]
        assert len(eerste) == 1 and eerste[0]["count"] == 1 and feiten["diagnose_leesbaar"]
        assert feiten["diagnose_ongewijzigd"] and feiten["worker_gezondheidslezingen"] >= 4
        assert feiten["worker_schrijfverzoeken"] == feiten["nas_schrijfverzoeken"] == 0
