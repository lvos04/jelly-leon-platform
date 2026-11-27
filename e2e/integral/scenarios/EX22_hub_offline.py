"""Uptime Kuma ziet de uitval van lifecycle terwijl de Hub al uitstaat."""

from __future__ import annotations

import json
import os
import re
import secrets
from typing import Any

import httpx
import pytest

from e2e.integral.bewijs import schrijf
from e2e.integral.media import draait, uitval
from e2e.integral.scenarios.conftest import Kern, wacht
from e2e.integral.scenarios.hubveiligheid import schrijfacties
from e2e.integral.scenarios.meldingen import FIXTURES, Gotify

SCENARIO = {
    "eigenaar": "Uptime Kuma en Gotify", "groepen": ("meldingen", "uitval"), "diensten": ("uptime-kuma", "gotify-sim"),
    "soort": "Sim+", "blokkade": "NIET GEVALIDEERD (echte NAS); NIET GEVALIDEERD (hardware)",
    "toelichting": "N-P6-04: de melding op een echte telefoon ontvangen terwijl de Hub op de NAS uitstaat.",
    "verwachting": "Bij een gestopte Hub meldt Kuma de uitval van een afzonderlijke dienst rechtstreeks aan Gotify.",
}


def test_melding_bij_gestopte_hub(kern: Kern) -> None:
    kern.net.waarnemer = "executor"  # Deze HTTP-client moet kunnen blijven meten als de Hub en lifecycle uitstaan.
    project = kern.net.project
    gotify = Gotify(kern)
    kuma = kern.client("uptime-kuma", 3001)
    wacht("Kuma bereikbaar", lambda: kuma.get("/api/entry-page").status_code == 200)
    project.compose("exec", "-T", "-d", "hub", "python", "-c", (FIXTURES / "hub_meldroute.py").read_text())
    omweg_client = kern.client("hub", 31822)
    wacht("controleroute in de Hub", lambda: omweg_client.get("/").status_code == 501)
    wachtwoord = secrets.token_urlsafe(24)

    def kanalen(inrichten: bool = False) -> dict[str, Any]:
        invoer = {"inrichten": inrichten, "omweg": os.getenv("INTEGRAAL_KUMA_VIA_HUB") == "1", "wachtwoord": wachtwoord,
                  "direct": gotify.applicaties["uptime-kuma"], "via": gotify.applicaties["hub"]}
        raw = project.compose("exec", "-T", "uptime-kuma", "node", "-e", (FIXTURES / "kuma.cjs").read_text(),
                              invoer=json.dumps(invoer), tijd=65)
        return dict(json.loads(raw))

    voor = kanalen(inrichten=True)
    assert voor["direct"] and voor["via_hub"] and voor["monitor"] > 0
    assert any(r["message"] == "Rechtstreeks Testing" for r in gotify.berichten("uptime-kuma"))
    assert any(r["message"] == "Via de proef-Hub Testing" for r in gotify.berichten("hub"))

    def monitorstatus() -> int | None:
        meting = kuma.get("/metrics", auth=("proefbeheer", wachtwoord)).raise_for_status()
        for regel in meting.text.splitlines():
            if regel.startswith("monitor_status{") and f'monitor_id="{voor["monitor"]}"' in regel:
                return int(float(regel.split()[-1]))
        return None

    def uitvalmeldingen(bron: str) -> set[int]:
        return {r["id"] for r in gotify.berichten(bron) if r["title"] == "Uptime-Kuma"
                and "Proefdienst" in r["message"] and re.search(r"\bDown\b", r["message"], re.I)}

    wacht("Kuma meet gezonde proefdienst", lambda: monitorstatus() == 1)
    # Laat beide kanalen dezelfde echte monitorovergang melden vóór het wegnemen van de Hub.
    with uitval(kern, "lifecycle"):
        wacht("uitval langs beide ingestelde kanalen", lambda: all(uitvalmeldingen(b) for b in ("hub", "uptime-kuma")))
        assert monitorstatus() == 0
    wacht("Kuma meet de dienst weer gezond", lambda: monitorstatus() == 1)
    direct_voor, omweg_voor = uitvalmeldingen("uptime-kuma"), uitvalmeldingen("hub")
    assert len(direct_voor) == len(omweg_voor) == 1
    with uitval(kern, "hub"):
        assert not draait(kern, "hub")
        with pytest.raises(httpx.ConnectError):
            kern.hub.get("/readyz")
        assert kern.lifecycle.get("/healthz").status_code == 200 and monitorstatus() == 1
        tijdens = kanalen()
        assert tijdens == {"direct": True, "via_hub": False, "hub_onbereikbaar": True}

        with uitval(kern, "lifecycle"):
            assert not draait(kern, "lifecycle")
            wacht("Kuma meet uitval", lambda: monitorstatus() == 0)
            try:
                wacht("rechtstreekse uitvalmelding", lambda: bool(uitvalmeldingen("uptime-kuma") - direct_voor))
            finally:
                rechtstreeks = len(uitvalmeldingen("uptime-kuma") - direct_voor)
                omweg = len(uitvalmeldingen("hub") - omweg_voor)
                storing = {"hub_gestopt_tijdens_uitval": not draait(kern, "hub"),
                           "monitor_tijdens_uitval": monitorstatus(),
                           "uitvalmeldingen_rechtstreeks": rechtstreeks, "uitvalmeldingen_via_hub": omweg}
                schrijf(kern.net.dossier / "metingen.json", storing)
            assert rechtstreeks == 1 and omweg == 0 and storing["hub_gestopt_tijdens_uitval"]
        wacht("Kuma meet herstelde proefdienst", lambda: monitorstatus() == 1)
    wacht("Hub na herstel", lambda: kern.hub.get("/readyz").json().get("status") == "ready")
    mutaties = schrijfacties(kern)
    assert mutaties == []
    schrijf(kern.net.dossier / "metingen.json", {
        "uitvalmeldingen_vooraf_rechtstreeks": len(direct_voor), "uitvalmeldingen_vooraf_via_hub": len(omweg_voor),
        **storing, "monitor_voor_tijdens_na": [1, 0, 1],
        "kanaaltest_via_hub_mislukt": tijdens["hub_onbereikbaar"], "schrijfacties": len(mutaties),
    })
